"""A one-way reconstruction queue. Preparation never means visual acceptance."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from .artwork import prepare_boards, prepare_source_boards, validate_output, extract_assets, inspect_board_cells
from .wordart import MODE as WORDART_MODE, select_primary_wordart, validate_wordart_output, extract_wordart_asset
from .layout import normalize_layout, SOURCE_LAYOUT_POLICY
from .boardplan import SOURCE_BUDGET_POLICY
from .renderer import build_presentation, preflight_native_text
from .source import (prepare_source, pixel_identity, normalize_layer,
                     prepare_reading_images, prepare_wordart_source_crop, TITLE_REFERENCE_POLICY)
from .prompts import (layer_brief, board_brief, wordart_brief, EMBEDDED_IMAGE_POLICY,
                      BACKGROUND_CONTAINER_POLICY_ID, SOURCE_LAYER_SCOPE_POLICY, BOARD_REFERENCE_POLICY)
from .prompts import SOURCE_BOARD_REFERENCE_POLICY as DIRECT_SOURCE_BOARD_POLICY
from .native import MODE as MATERIAL_MODE, validate_native_output
from . import execution, version, binding, recovery
from .storage import fingerprint, sha256, read_state, save_state, write_json, relative_file, job_lock


ROOT = Path(__file__).resolve().parent.parent
GENERATION_POLICY = 'single-call-single-output/1'
SEQUENTIAL_LAYER_ORDER = 'background-then-foreground/1'
FOREGROUND_COMPONENT_POLICY = 'generated-foreground-members/1'
KEYED_MATERIAL_POLICY = 'keyed-materials/1'
_TEXT_DESCRIPTION_GUIDANCE = (
    'Use schema_file for the existing scene contract. Coordinates and text size use the '
    '1920x1080 working-image pixels. Supply size explicitly on each text item or its shared '
    'style, estimating the source glyph height and line spacing. Keep the source-observed '
    'line boxes and surrounding card clearances when correcting typography. '
)


def _container_description_context(state):
    if state.get('decorative_background_policy') != BACKGROUND_CONTAINER_POLICY_ID:
        return {}, ''
    fields = {
        'decorative_background_policy': BACKGROUND_CONTAINER_POLICY_ID,
        'source_transform': copy.deepcopy(state['source']['transform']),
    }
    guidance = (
        'The background already carries empty decorative containers such as textured paper '
        'panels, ornate page/card frames and their corner/cloud ornaments. Keep these '
        'containers in that background and describe their editable content separately. '
        'Foreground artwork consists of independent images, artistic lettering, labels, '
        'icons and movable illustrations. Plain geometry and table structures remain native. '
        'Read source_transform as program-filled, read-only geometry, with its saved scale '
        'and offset. For an original-image crop enlarged by zoom, '
        'source_xy = crop_origin + local_xy / zoom, then '
        'working_xy = source_xy * scale + offset. For a working-image crop, '
        'working_xy = crop_origin + local_xy / zoom. Keep that result in working coordinates. '
        'Measure art.box and target around the complete artwork group, including its backing, '
        'brush arcs and detached strokes, using the full group height. Read all four outer '
        'group edges from the source, then compute width = right - left and '
        'height = bottom - top; do not substitute text size or glyph-line height. '
        'When retaining the source placement, omit target to reuse the measured box. '
        'Read lettering character '
        'by character from an original-image local crop, keeping all source characters and '
        'punctuation. Locate each label and each native line independently within its own '
        'source panel, preserving its line Y position and horizontal panel clearance. '
    )
    return fields, guidance


def _validate_source_policies(source_layout_policy, board_sampling_policy):
    if source_layout_policy not in (None, SOURCE_LAYOUT_POLICY):
        raise ValueError('Unsupported source-layout policy.')
    if board_sampling_policy not in (None, SOURCE_BUDGET_POLICY):
        raise ValueError('Unsupported board sampling policy.')


def _validate_source_board_policy(policy, source_layout_policy):
    if policy not in (None, DIRECT_SOURCE_BOARD_POLICY):
        raise ValueError('Unsupported source-board reference policy.')
    if policy is not None and source_layout_policy != SOURCE_LAYOUT_POLICY:
        raise ValueError('The source-board reference policy requires source-first description.')


def _validate_primary_source_policy(policy, source_layout_policy):
    if policy not in (None, TITLE_REFERENCE_POLICY):
        raise ValueError('Unsupported primary-source reference policy.')
    if policy is not None and source_layout_policy != SOURCE_LAYOUT_POLICY:
        raise ValueError('The primary-source reference policy requires source-first description.')


def _validate_layer_generation_order(order, source_layout_policy):
    if order not in (None, SEQUENTIAL_LAYER_ORDER):
        raise ValueError('Unsupported layer generation order.')
    if order is not None and source_layout_policy is not None:
        raise ValueError('Sequential layer generation uses the layers-first description route.')


def _validate_foreground_component_policy(policy, order):
    if policy not in (None, FOREGROUND_COMPONENT_POLICY):
        raise ValueError('Unsupported foreground component policy.')
    if policy is not None and order != SEQUENTIAL_LAYER_ORDER:
        raise ValueError('The foreground component policy requires sequential layers.')


def _validate_keyed_material_policy(policy, order, component_policy):
    if policy not in (None, KEYED_MATERIAL_POLICY):
        raise ValueError('Unsupported keyed material policy.')
    if policy is not None and (order != SEQUENTIAL_LAYER_ORDER or component_policy != FOREGROUND_COMPONENT_POLICY):
        raise ValueError('The keyed material policy requires sequential foreground components.')


def _scene_allocation(state, layout):
    fields = ('id', 'box', 'description', 'presentation', 'embedded_image', 'lettering')
    return {
        'background_regions': copy.deepcopy(layout['background_regions']),
        'foreground_regions': [
            {key: copy.deepcopy(item[key]) for key in fields if key in item}
            for item in layout['items'] if item['kind'] == 'art'
        ],
        'source_pixel_sha256': state['source']['pixel_sha256'],
    }


def _select_layout_wordart(layout):
    arts = [item for item in layout['items'] if item['kind'] == 'art']
    if 'primary_wordart_id' in layout:
        return select_primary_wordart(arts, primary_id=layout['primary_wordart_id'])
    return select_primary_wordart(arts)


def _source_layer_scope(layout):
    primary, members = _select_layout_wordart(layout)
    return {'policy': SOURCE_LAYER_SCOPE_POLICY,
            'board_member_ids': [item['id'] for item in members],
            'primary_wordart_id': primary['id'] if primary is not None else None}


def _source_policy_integrity(work, state):
    keys = ('source_layout_policy', 'board_sampling_policy')
    values = [state.get(key) for key in keys]
    _validate_source_policies(*values)
    marker_path = Path(work) / 'initialization.json'
    marker = json.loads(marker_path.read_text('utf-8')) if marker_path.exists() else {}
    component_policy = state.get('foreground_component_policy')
    _validate_foreground_component_policy(component_policy, state.get('layer_generation_order'))
    if component_policy != marker.get('foreground_component_policy'):
        raise ValueError('The frozen foreground component policy changed.')
    keyed_policy = state.get('keyed_material_policy')
    _validate_keyed_material_policy(keyed_policy, state.get('layer_generation_order'), component_policy)
    if keyed_policy != marker.get('keyed_material_policy'):
        raise ValueError('The frozen keyed material policy changed.')
    for task in state['tasks'].values():
        expected_keyed = keyed_policy if task['role'] == 'foreground' else None
        if task['role'] in {'foreground', 'board'} and task.get('keyed_material_policy') != expected_keyed:
            raise ValueError('A material task differs from the frozen keyed material policy.')
    order = state.get('layer_generation_order')
    _validate_layer_generation_order(order, state.get('source_layout_policy'))
    _validate_layer_generation_order(marker.get('layer_generation_order'), marker.get('source_layout_policy'))
    if order != marker.get('layer_generation_order'):
        raise ValueError('The frozen layer generation order changed.')
    layers = [task for task in state['tasks'].values() if task['role'] in {'background', 'foreground'}]
    if any(task.get('layer_generation_order') != order for task in layers):
        raise ValueError('A layer differs from the frozen layer generation order.')
    if order is not None and (len(layers) != 2 or {task['role'] for task in layers} != {'background', 'foreground'}):
        raise ValueError('Sequential layer generation requires its two original layer tasks.')
    _validate_source_policies(*(marker.get(key) for key in keys))
    for key in keys:
        if state.get(key) != marker.get(key):
            raise ValueError('The frozen ' + key.replace('_', ' ') + ' changed.')
    direct_policy = state.get('source_board_reference_policy')
    _validate_source_board_policy(direct_policy, state.get('source_layout_policy'))
    _validate_source_board_policy(marker.get('source_board_reference_policy'), marker.get('source_layout_policy'))
    if direct_policy != marker.get('source_board_reference_policy'):
        raise ValueError('The frozen source-board reference policy changed.')
    primary_policy = state.get('primary_source_reference_policy')
    _validate_primary_source_policy(primary_policy, state.get('source_layout_policy'))
    _validate_primary_source_policy(marker.get('primary_source_reference_policy'), marker.get('source_layout_policy'))
    if primary_policy != marker.get('primary_source_reference_policy'):
        raise ValueError('The frozen primary-source reference policy changed.')
    scope_policy = state.get('source_layer_scope_policy')
    if (scope_policy not in (None, SOURCE_LAYER_SCOPE_POLICY)
            or scope_policy != marker.get('source_layer_scope_policy')):
        raise ValueError('The frozen source layer scope policy changed.')
    if scope_policy is None and (state.get('source_layer_scope') is not None or any(
            task.get('source_layer_scope') is not None for task in state['tasks'].values())):
        raise ValueError('An older job cannot add a source layer scope.')
    if scope_policy is not None and state.get('source_layout_policy') != SOURCE_LAYOUT_POLICY:
        raise ValueError('A source layer scope requires the frozen source-first policy.')
    reference_policy = state.get('board_reference_policy')
    if (reference_policy not in (None, BOARD_REFERENCE_POLICY)
            or reference_policy != marker.get('board_reference_policy')
            or (reference_policy is not None and scope_policy != SOURCE_LAYER_SCOPE_POLICY)):
        raise ValueError('The frozen board reference policy changed.')
    if state.get('source_layout_policy') != SOURCE_LAYOUT_POLICY or state.get('layout') is None:
        return
    layout = state['layout']
    if (state.get('layout_source_sha256') != state['source']['working_sha256']
            or state.get('source_layout_fingerprint') != fingerprint(layout)):
        raise ValueError('The frozen source-layout description or source binding changed.')
    allocation = _scene_allocation(state, layout)
    if state.get('scene_allocation') != allocation:
        raise ValueError('The frozen scene allocation changed.')
    scope = _source_layer_scope(layout) if scope_policy is not None else None
    if state.get('source_layer_scope') != scope:
        raise ValueError('The frozen source layer scope changed.')
    for task in state['tasks'].values():
        if task['role'] == 'wordart':
            detail = task.get('source_detail') or {}
            if detail.get('reference_policy') != primary_policy:
                raise ValueError('A title reference differs from the frozen primary-source policy.')
            if primary_policy is not None:
                member = next((item for item in layout['items'] if item['id'] == task['member']['id']), None)
                if (task.get('primary_source_detail_sha256') != fingerprint(detail)
                        or task['member'] != member
                        or detail.get('source_sha256') != state['source']['working_sha256']
                        or task['references'] != {detail['path']: detail['sha256']}):
                    raise ValueError('The frozen primary-source title reference changed.')
        if task['role'] in {'background', 'foreground'} and task.get('scene_allocation') != allocation:
            raise ValueError('A layer differs from the frozen scene allocation.')
        if task['role'] in {'background', 'foreground'} and task.get('source_layer_scope') != scope:
            raise ValueError('A layer differs from the frozen source layer scope.')
        if task['role'] == 'board':
            board = task['board']
            if board.get('source_board_reference_policy') != direct_policy:
                raise ValueError('A board differs from the frozen source-board reference policy.')
            if direct_policy is not None:
                provenance = board.get('reference_provenance', {})
                if (provenance.get('kind') != 'unchanged-working-source'
                        or provenance.get('source_sha256') != state['source']['working_sha256']
                        or task.get('source_board_metadata_sha256') != fingerprint(board)):
                    raise ValueError('A direct-source board differs from the frozen source.')
            expected = reference_policy if any(m.get('embedded_image') is True for m in board['members']) else None
            if board.get('board_reference_policy') != expected:
                raise ValueError('A board differs from the frozen reference policy.')


def _source_description_action(work, state, *, layout=None, issues=None):
    container_instruction = (
        'Static decorative containers remain in the background; '
        if state.get('decorative_background_policy') == BACKGROUND_CONTAINER_POLICY_ID
        else 'Keep paper or card panels and their decorative frames in the existing foreground route; '
    )
    action = {
        'kind': 'describe',
        'files': [str(relative_file(work, state['source'][key])) for key in ('working', 'guide')],
        'schema_file': str(ROOT / 'references' / 'scene.md'),
        'source_layout_policy': SOURCE_LAYOUT_POLICY,
        'source_transform': copy.deepcopy(state['source']['transform']),
        'source_pixel_sha256': state['source']['pixel_sha256'],
        'reply_template': {'kind': 'layout', 'layout': layout if layout is not None else {
            'items': [], 'background_regions': []}},
        'instructions': (
            'Inspect the current working image and same-source coordinate guide before any image generation. '
            'In this single description first measure complete artwork and panel clearances, then transcribe '
            'each visible text line at its own position. Coordinates and font sizes use 1920x1080 working pixels. '
            'Measure art groups around all lettering, backings, full brush arcs and detached strokes. Read '
            'left, top, right and bottom, then compute width = right - left and height = bottom - top. '
            'For source placement omit target to reuse box. Determine native text size from the actual '
            'source glyph height and line spacing, preserving horizontal panel clearance and each line Y. '
            'Read uncertain lettering and punctuation from a local source crop, then return to the full '
            'working image to confirm its complete bounds and placement. For a working-image crop, '
            'working_xy = crop_origin + local_xy / zoom. For an original-image crop, '
            'source_xy = crop_origin + local_xy / zoom, then working_xy = source_xy * scale + offset. '
            'source_transform is program-filled read-only metadata. Record integrated background scenes '
            'once in background_regions as id (optional), box and description. Keep independent editable '
            'art in items, including its full rectangular embedded images and exact artistic lettering. '
            'Empty background_regions is valid. ' + container_instruction +
            'artistic brush-label backings remain with their lettering. The same frozen allocation and '
            'source coordinates will guide both layers and assembly. No second page analysis is requested. '
            + EMBEDDED_IMAGE_POLICY
        ),
    }
    if issues is not None:
        action.update(reason='native_text_requires_correction', generate_more=False, issues=issues)
        action['instructions'] += (
            ' Correct the flagged native lines against these same source images, retaining exact source '
            'wording, panel boundaries and the one current description. No image task has been created.'
        )
    if state['source'].get('reading_images'):
        action['reading_images'] = [
            {**copy.deepcopy(item), 'path': str(relative_file(work, item['path']))}
            for item in state['source']['reading_images']
        ]
        action['instructions'] += (
            ' Inspect reading_images for small lettering and each line position. They are real '
            'working-image crops enlarged by nearest-neighbour pixels. For each descriptor, '
            'working_xy = working_box top-left + local_xy / zoom. Use its own origin and zoom. '
            'Return to the full working image and ruler to confirm the complete group and panel '
            'clearances; a tile boundary does not define an artwork boundary.'
        )
    if state.get('source_layer_scope_policy') == SOURCE_LAYER_SCOPE_POLICY:
        action['instructions'] += (
            ' Box-based items may provide source_bounds=[left,top,right,bottom] in working pixels. '
            'For each background_regions entry, prefer source_bounds=[left,top,right,bottom]. '
            'Existing box=[x,y,width,height] uses width = right - left and height = bottom - top. '
            'Compound art may provide source_parts with bounds=[left,top,right,bottom] and optional name '
            'for each piece; the program unions the parts into one complete group. '
            'For the source placement omit target. Existing box input remains accepted. '
            'Ordinary headings expressible with supported native font styles remain native text. '
            'When a line has a separable ordinary-font section and a complex metallic or shaded section, '
            'register the ordinary section as native text and the complex section as artwork with its real effects. '
            'At the layout root primary_wordart_id is optional. null keeps every artwork on component boards. '
            'An eligible artwork ID selects that one standalone task. Omission keeps the largest written-art rule.'
        )
    if state.get('source_board_reference_policy') == DIRECT_SOURCE_BOARD_POLICY:
        action['instructions'] += (
            ' This job generates only the background layer before artwork tasks. Component references '
            'will be made from this unchanged source, without a generated foreground layer. These '
            'references are inputs only; final artwork still requires actual new image outputs.'
        )
    if state.get('primary_source_reference_policy') == TITLE_REFERENCE_POLICY:
        action['primary_source_reference_policy'] = TITLE_REFERENCE_POLICY
        title_reference_instruction = (
            ' If a standalone artistic member is selected, it will use a single source-parts reference. '
            if state.get('source_layer_scope_policy') == SOURCE_LAYER_SCOPE_POLICY else
            ' The primary title will use a single source-parts reference. '
        )
        action['instructions'] += (
            title_reference_instruction + 'Measure its complete '
            'writing, own arc and farthest detached strokes in the source. The reference retains '
            'original pixels inside those parts and clears outside them. Paper or decoration '
            'inside the retained rectangles may remain for the image task to remove.'
        )
    return action


def _create_layer_tasks(work, state):
    src = state['source']
    container_policy = state.get('decorative_background_policy')
    roles = ('background', 'foreground')
    if state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY:
        _, board_arts = _select_layout_wordart(state['layout'])
        if not board_arts:
            roles = ('background',)
    if state.get('source_board_reference_policy') == DIRECT_SOURCE_BOARD_POLICY:
        roles = ('background',)
    for role in roles:
        mode = {'background_mode': MATERIAL_MODE} if role == 'foreground' else {}
        identity = {'source_pixel_sha256': src['pixel_sha256'], **mode} if mode else src['pixel_sha256']
        layer_metadata = dict(mode)
        if container_policy:
            layer_metadata['decorative_background_policy'] = container_policy
            identity = {'source_pixel_sha256': src['pixel_sha256'], **layer_metadata}
        if state.get('layer_generation_order') is not None:
            layer_metadata['layer_generation_order'] = state['layer_generation_order']
            identity = {'source_pixel_sha256': src['pixel_sha256'], **layer_metadata}
        brief_options = dict(mode)
        if state.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
            layer_metadata['foreground_component_policy'] = FOREGROUND_COMPONENT_POLICY
            if role == 'foreground':
                brief_options['foreground_components_only'] = True
        if state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY:
            layer_metadata['scene_allocation'] = copy.deepcopy(state['scene_allocation'])
            if state.get('source_layer_scope') is not None:
                layer_metadata['source_layer_scope'] = copy.deepcopy(state['source_layer_scope'])
                brief_options['source_layer_scope'] = state['source_layer_scope']
            identity = {'source_pixel_sha256': src['pixel_sha256'], **layer_metadata,
                        'source_layout_policy': SOURCE_LAYOUT_POLICY}
            brief_options['scene_allocation'] = state['scene_allocation']
        brief = layer_brief(role, src['transform'], decorative_background=bool(container_policy), **brief_options)
        if role == 'background' and state.get('keyed_material_policy') == KEYED_MATERIAL_POLICY:
            layer_metadata['background_scene_emphasis'] = True
            identity = {'source_pixel_sha256': src['pixel_sha256'], **layer_metadata}
        if role == 'foreground' and state.get('keyed_material_policy') == KEYED_MATERIAL_POLICY:
            from .keyed import choose_material_key
            from .prompts import keyed_layer_brief
            matte_key = choose_material_key(relative_file(work, src['working']))
            layer_metadata.update(keyed_material_policy=KEYED_MATERIAL_POLICY, matte_key=matte_key)
            identity = {'source_pixel_sha256': src['pixel_sha256'], **layer_metadata}
            brief = keyed_layer_brief(role, src['transform'], matte_key,
                decorative_background=bool(container_policy), foreground_components_only=True)
        task = _task(work, role, identity, brief,
            [relative_file(work, src['working'])], **layer_metadata)
        if execution.enabled(state):
            execution.enroll(task)
        if binding.enabled(state):
            task['delivery_policy'] = binding.DELIVERY_POLICY
        state['tasks'][task['id']] = task


def _create_art_tasks(work, state, layout):
    if state.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
        layout = {**layout, 'primary_wordart_id': None}
    primary, other_arts = _select_layout_wordart(layout)
    source_first = state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY
    boards = []
    if other_arts or not source_first:
        options = {'background_mode': MATERIAL_MODE} if state.get('material_background_mode') == MATERIAL_MODE else {}
        if state.get('board_sampling_policy') is not None:
            options['sampling_policy'] = state['board_sampling_policy']
        if state.get('board_reference_policy') is not None:
            options['reference_policy'] = state['board_reference_policy']
        if state.get('source_board_reference_policy') == DIRECT_SOURCE_BOARD_POLICY:
            boards = prepare_source_boards(relative_file(work, state['source']['working']),
                other_arts, Path(work) / 'boards', **options)
        else:
            front = next(t for t in state['tasks'].values() if t['role'] == 'foreground')
            boards = prepare_boards(relative_file(work, state['source']['working']),
                relative_file(work, front['result']['normalized']), other_arts, Path(work) / 'boards', **options)
    state.update(layout=layout, layout_source_sha256=state['source']['working_sha256'], phase='boards')
    write_json(Path(work) / 'layout.json', layout)
    if primary is not None:
        identity = {'policy': WORDART_MODE, 'source_pixel_sha256': state['source']['pixel_sha256'], 'member': primary}
        references = [relative_file(work, state['source']['working'])]
        metadata = {}
        brief_options = {}
        if source_first:
            detail_options = ({'reference_policy': state['primary_source_reference_policy']}
                              if state.get('primary_source_reference_policy') is not None else {})
            detail = prepare_wordart_source_crop(work, state['source'], primary, **detail_options)
            identity['source_detail'] = detail
            metadata['source_detail'] = copy.deepcopy(detail)
            brief_options['source_detail'] = detail
            references.insert(0, relative_file(work, detail['path']))
            if state.get('primary_source_reference_policy') == TITLE_REFERENCE_POLICY:
                references = [relative_file(work, detail['path'])]
                metadata['primary_source_detail_sha256'] = fingerprint(detail)
        task = _task(work, 'wordart', identity, wordart_brief(primary, **brief_options),
            references, member=primary, background_mode=WORDART_MODE, **metadata)
        if execution.enabled(state):
            execution.enroll(task)
        if binding.enabled(state):
            task['delivery_policy'] = binding.DELIVERY_POLICY
        state['tasks'][task['id']] = task
    for board in boards:
        state['warnings'].extend({'board': board['id'], **note} for note in board.get('reference_warnings', []))
        # Board references carry generated placement. The unchanged source
        # remains authoritative for material appearance and exact lettering.
        metadata = ({'source_board_metadata_sha256': fingerprint(board)}
                    if board.get('source_board_reference_policy') == DIRECT_SOURCE_BOARD_POLICY else {})
        if state.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
            metadata['foreground_component_policy'] = FOREGROUND_COMPONENT_POLICY
        task = _task(work, 'board', board['signature'], board_brief(board),
            [board['image'], relative_file(work, state['source']['working'])], board=board,
            **({'background_mode': MATERIAL_MODE} if board.get('background_mode') == MATERIAL_MODE else {}),
            **metadata)
        if execution.enabled(state):
            execution.enroll(task)
        if binding.enabled(state):
            task['delivery_policy'] = binding.DELIVERY_POLICY
        state['tasks'][task['id']] = task
    save_state(work, state, 'frozen_artwork_planned' if source_first else 'description_frozen')


def _runtime():
    import numpy as np
    from PIL import Image
    if int(np.array([1,2]).sum()) != 3 or Image.new('RGB',(1,1)).size != (1,1):
        raise ValueError('Image dependencies cannot perform basic operations.')
    node = shutil.which('node') or shutil.which('nodejs')
    if not node:
        raise ValueError('An existing Node runtime is required; nothing is installed automatically.')
    script = "const fs=require('fs');for(const [n,v] of Object.entries({pptxgenjs:'4.0.1',jszip:'3.10.1'})){require(n);if(JSON.parse(fs.readFileSync('node_modules/'+n+'/package.json','utf8')).version!==v)throw Error('Dependency version mismatch: '+n)}"
    try:
        result=subprocess.run([node,'-e',script],cwd=ROOT,capture_output=True,text=True,timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('The local Node dependency check timed out.') from exc
    if result.returncode:
        raise ValueError('The extracted project needs its bundled Node dependencies; no installation was attempted.')


def _task(work, role, identity, brief, references, **extra):
    task_id = 'task-' + fingerprint([role,identity,brief])[:20]
    path = Path(work) / 'briefs' / (task_id + '.txt')
    path.parent.mkdir(exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') != brief:
        raise ValueError('An existing task brief changed.')
    if not path.exists(): path.write_text(brief,encoding='utf-8')
    refs={str(Path(p).resolve().relative_to(Path(work).resolve())).replace('\\','/'):sha256(p) for p in references}
    return {'id':task_id,'role':role,'status':'waiting','issued':False,'attempt':0,'attempts':[],
        'brief':path.relative_to(work).as_posix(),'brief_sha256':sha256(path),'references':refs,**extra}


def _native_material_task(task):
    if task['role'] not in {'foreground','board'}:
        return False
    mode=task.get('background_mode')
    board_mode=task.get('board',{}).get('background_mode')
    if any(value not in (None,MATERIAL_MODE) for value in (mode,board_mode)):
        raise ValueError('Unsupported material background mode')
    if task['role']=='board' and mode==MATERIAL_MODE and board_mode!=MATERIAL_MODE:
        raise ValueError('Inconsistent material board mode')
    return mode==MATERIAL_MODE or board_mode==MATERIAL_MODE


def _role_review_scope(task):
    if task.get('keyed_material_policy') == KEYED_MATERIAL_POLICY and task['role'] == 'foreground':
        return (binding.REVIEW_SCOPE +
            ' This full foreground is an intermediate sampling reference. Inspect all assigned '
            'subjects for correct identity, completeness and usable sampling positions. Native '
            'text and geometry are reference appearances. Residual fixed containers or context '
            'outside the sampled subjects are observations, not a reason for another full-page '
            'generation. Missing or wrong subjects, or contamination that prevents sampling a '
            'required subject, are real failures. Final component boards still require exact '
            'lettering, complete cutouts and cleared surroundings. The assigned solid colour '
            'field is intentional; actual alpha and colour removal are program checks. Preserve '
            'photo interiors and genuine subject backings. Register the original tool return.')
    if task.get('keyed_material_policy') == KEYED_MATERIAL_POLICY:
        return (binding.REVIEW_SCOPE +
            ' The assigned solid colour field is intentional and will be converted to actual alpha '
            'by the program. Review complete subjects, exact lettering and assigned cell positions '
            'in the original tool return. Check that scene backgrounds, fixed containers and '
            'neighbouring fragments are absent from the cleared field. Preserve genuine photo '
            'interiors and subject backings. Record defects in observed. Alpha decoding and colour '
            'removal are program checks; keep the original tool return as file.')
    if task.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
        return (binding.REVIEW_SCOPE +
                ' Inspect the actual saved PNG with alpha compositing. Background scenery and fixed '
                'empty paper/card containers belong to the background. In foreground and each cutout '
                'cell, observe the complete assigned subject, its own real backing, surrounding '
                'transparency, and any neighbouring caption or paper fragment. Preserve white photo '
                'contents and genuine badge material. Record actual defects in the existing observed '
                'field and base matches_task on the assigned subject. Keep the original tool return '
                'as the registered file; local rearrangement is separate derived processing.')
    if task['role'] == 'board' and task.get('board', {}).get('source_board_reference_policy') == DIRECT_SOURCE_BOARD_POLICY:
        return (binding.REVIEW_SCOPE + ' The references are unchanged-source crops, never generated '
                'outputs. Observe every assigned subject in its actual cell. If all complete subjects '
                'are present but cell identities were permuted, optionally supply observed_cell_members '
                'as the exact member IDs in row-major (slot y, then x) allocated-cell order. This '
                'one-to-one observation changes extraction slots only; it cannot excuse missing, '
                'wrong or unsafe subjects or turn a false observation into true.')
    if task.get('source_layer_scope') is None:
        return binding.REVIEW_SCOPE
    if task['role'] == 'foreground':
        return (
            'Observe only frozen board_member_ids as component-layout references. Check subject identity, '
            'complete rectangular image contents, usable source ROIs and scene-subject contamination. '
            'Missing or wrong subjects, or severe displacement making a required ROI unusable, fail. '
            'Record reference glyph and edge differences in observed; glyph differences alone do not '
            'require another foreground generation. Exact source lettering and final material come from '
            'the frozen layout and unchanged source during later artwork generation. The standalone '
            'primary title and native page content are outside this foreground task. Actual observations '
            'remain required; this scope does not certify full fidelity or provider identity.')
    return (binding.REVIEW_SCOPE + ' Clear all allocated artwork as complete groups, including red '
            'plaques, brush grounds and badge backplates. Only unassigned empty decorative containers '
            'and the same frozen background scenes remain.')


def _material_integrity(state):
    mode=state.get('material_background_mode')
    if mode not in (None,MATERIAL_MODE):
        raise ValueError('Unsupported material background mode')
    container_policy = state.get('decorative_background_policy')
    if container_policy not in (None, BACKGROUND_CONTAINER_POLICY_ID):
        raise ValueError('Unsupported decorative background policy')
    for task in state['tasks'].values():
        if (task['role'] in {'background', 'foreground'}
                and task.get('decorative_background_policy') != container_policy):
            raise ValueError('Layer task differs from the frozen decorative background policy')
        if task['role'] in {'foreground','board'}:
            if _native_material_task(task)!=(mode==MATERIAL_MODE):
                raise ValueError('Inconsistent material background mode')


def _prepared_material_file(work, task, path, digest):
    if task.get('keyed_material_policy') != KEYED_MATERIAL_POLICY:
        return Path(path), None
    from .keyed import prepare_material
    output = Path(work) / 'materials' / f"{task['id']}-{task['attempt']}-{digest[:16]}.png"
    detail = prepare_material(path, output, task['matte_key'])
    prepared = Path(detail['path']).resolve()
    if not prepared.is_relative_to(Path(work).resolve()):
        raise ValueError('Prepared material must remain in its original job.')
    if sha256(path) != digest:
        raise ValueError('The original returned material changed while preparing alpha.')
    return prepared, detail


def _public_task(work, task):
    value={k:task[k] for k in ('id','role','attempt')}
    value.update(prompt_file=str(relative_file(work,task['brief'])),
        references=[str(relative_file(work,p)) for p in task['references']],
        requested_outputs=1, transparent_background=False,
        execution_contract={'policy':GENERATION_POLICY,'max_invocations':1,'max_outputs':1,
            'provider_output_count':1,'automatic_retry':False,
            'count_provenance':'caller_reported_not_provider_verified',
            'instructions':'Use one image-tool call requesting one output. Immediately save and register its first technically usable result. Do not generate variants, compare candidates or call again while awaiting or correcting registration. If the tool returns extra images, preserve the known files, report their count and stop.'},
        result_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'file':''},
        failure_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'outcome':'failed','reason':''})
    if task['role']=='board':
        value.update(size=task['board']['size'],key=task['board']['key'],
            members=[{'id':m['id'],'description':m['description'],**({'lettering':m['lettering']} if m.get('lettering') else {})}
                     for m in task['board']['members']])
        value['execution_contract'].update(reference_inputs_are_not_outputs=True)
        value['execution_contract']['instructions']+=' Every reference input is a placement or appearance aid, not a generated result. Register the actual saved board output; never submit a reference input, renamed copy or re-encoded copy. If no real board output is available, leave this task unfinished and report the missing output.'
    elif task['role']=='wordart':
        value.update(size=[1536,1024],transparent_background=True,background_mode=WORDART_MODE,
                     member=copy.deepcopy(task['member']))
        if (task.get('source_detail') or {}).get('reference_policy') == TITLE_REFERENCE_POLICY:
            value.update(primary_source_reference_policy=TITLE_REFERENCE_POLICY,
                         source_detail=copy.deepcopy(task['source_detail']))
    else: value['size']=[1920,1080]
    if _native_material_task(task):
        value.update(transparent_background=True,background_mode=MATERIAL_MODE)
    if task.get('keyed_material_policy') == KEYED_MATERIAL_POLICY:
        value.update(transparent_background=False, keyed_material_policy=KEYED_MATERIAL_POLICY,
                     matte_key=task['matte_key'])
    if task.get('decorative_background_policy'):
        value['decorative_background_policy'] = task['decorative_background_policy']
    if task.get('layer_generation_order') is not None:
        value['layer_generation_order'] = task['layer_generation_order']
    value=execution.decorate(work,task,value)
    if task.get('delivery_policy')==binding.DELIVERY_POLICY:value=binding.decorate_task(task,value)
    if (task.get('source_layer_scope') is not None
            or task.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY
            or task.get('board', {}).get('source_board_reference_policy') is not None) and 'role_review_scope' in value:
        value['role_review_scope'] = _role_review_scope(task)
    if task.get('bounded_recovery'):
        value['bounded_recovery']=copy.deepcopy(task['bounded_recovery'])
        value['execution_contract']['recovery_policy']=task['bounded_recovery']['policy']
    return value


def _strict_generation(state):
    # Frozen older jobs keep their recorded recovery semantics. New jobs opt in
    # explicitly; an old receipt is never rewritten to claim new validation.
    return state.get('generation_policy') == GENERATION_POLICY


def _registration_task(work, task, strict=True):
    if not strict:
        return _public_task(work, task)
    value={'id':task['id'],'role':task['role'],'attempt':task['attempt'],
            'action':'register_existing_output_only',
            'result_template':{'task_id':task['id'],'role':task['role'],
                               'attempt':task['attempt'],'file':''},
            'failure_template':{'task_id':task['id'],'role':task['role'],
                                'attempt':task['attempt'],'outcome':'failed','reason':''}}
    if task['role']=='board':
        value['instructions']='Register only an existing actual board output. A reference input, renamed copy or re-encoded copy is not an output. If it is missing, keep this task unfinished; this registration request does not authorize another image call.'
    if task.get('delivery_policy')==binding.DELIVERY_POLICY:
        value['result_template']['request_sha256']=task['execution'].get('request_sha256','')
        value.update(role_review_template=binding.review_template(task),role_review_scope=_role_review_scope(task))
    if task.get('registration'):
        saved=task['registration']
        value['result_template']['file']=str(relative_file(work,saved['raw'])) if saved.get('raw') else ''
        value['registration_reason']=saved['reason']
        if saved.get('invocation_count') is not None:value['result_template']['invocation_count']=saved['invocation_count']
        if saved.get('output_count') is not None:value['result_template']['output_count']=saved['output_count']
    if task.get('surplus_registration'):
        saved=task['surplus_registration']
        outputs=[]
        for entry in saved['returns']:
            review=binding.review_template(task)
            review.update(entry.get('declared_role_review') or {})
            review.update(role=task['role'],output_sha256=entry['sha256'])
            outputs.append({'file':str(relative_file(work,entry['raw'])),'role_review':review,
                            'tool_output_id':entry.get('tool_output_id')})
        value['result_template'].update(invocation_count=saved.get('invocation_count',1),output_count=saved['output_count'],
            file=outputs[0]['file'],returned_files=[entry['file'] for entry in outputs],returned_outputs=outputs)
    return value


def _reject_reference_board_return(work, task, path, digest):
    """Detect known input substitution; this is not provider identity proof."""
    if task['role']!='board':
        return
    from PIL import Image
    candidate_pixels=None
    try:
        for name, recorded_digest in task['references'].items():
            reference=relative_file(work,name).resolve()
            if path==reference or digest==recorded_digest or digest==sha256(reference):
                raise ValueError('A board reference input cannot be registered as a generated output.')
            if candidate_pixels is None:
                with Image.open(path) as image:
                    candidate_pixels=(image.size,image.convert('RGBA').tobytes())
            with Image.open(reference) as image:
                if image.size==candidate_pixels[0] and image.convert('RGBA').tobytes()==candidate_pixels[1]:
                    raise ValueError('A re-encoded board reference input cannot be registered as a generated output.')
    except Image.DecompressionBombError as exc:
        raise ValueError('Image dimensions exceed safe decoding limits.') from exc


def _pending_generation_violations(state):
    return [task['id'] for task in state['tasks'].values()
            if any(not entry.get('acknowledged') for entry in task.get('generation_violations',[]))]


def _authorization(value, name):
    if not isinstance(value,str) or not value.strip() or len(value)>2000:
        raise ValueError(name+' must quote the explicit user request (1-2000 characters); caller assertions are not identity verification.')
    return value.strip()


def _returned_file_evidence(data):
    files=data.get('returned_files',[])
    if not isinstance(files,list) or any(not isinstance(path,str) or not path for path in files):
        raise ValueError('returned_files must list only known returned image paths, or be omitted.')
    paths=[]
    for value in ([data['file']] if data.get('file') else [])+files:
        path=Path(value).resolve()
        if path not in paths:paths.append(path)
    known=[(path,sha256(path)) for path in paths if path.is_file()]
    return {'paths':paths,'known':known,'missing':[str(path) for path in paths if not path.is_file()]}


def _generation_violation(work, state, task, data, observation, evidence):
    """Preserve known returns, even when an over-budget result is not accepted.

    Counts and filenames are host reports. This controller cannot observe or
    stop an image-tool call that the host makes outside the returned contract.
    """
    paths=evidence['paths'];unique={digest for _,digest in evidence['known']}
    exceeded=(data.get('invocation_count',0) or 0)>1 or (data.get('output_count',0) or 0)>1 or len(unique)>1 or bool(data.get('_returned_outputs_inconsistent'))
    if not exceeded:return None
    preserved=[];missing=evidence['missing']
    for path,digest in evidence['known']:
        destination=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}-generation-violation.bin"
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():destination.write_bytes(path.read_bytes())
        if sha256(destination)!=digest or sha256(path)!=digest:
            raise ValueError('A reported extra return changed while being preserved.')
        if not any(entry['sha256']==digest for entry in preserved):
            preserved.append({'raw':destination.relative_to(work).as_posix(),'sha256':digest})
    binding_errors=[]
    if binding.enabled(state):
        entries=data.get('returned_outputs') if isinstance(data.get('returned_outputs'),list) else []
        for path,digest in evidence['known']:
            supplied=next((entry for entry in entries if isinstance(entry,dict)
                and isinstance(entry.get('file'),str) and Path(entry['file']).resolve()==path),{})
            claim={**data,'tool':supplied.get('tool',data.get('tool')),
                   'tool_output_id':supplied.get('tool_output_id')}
            try:binding.claim_return(work,state,task,claim,path,digest,
                                     incomplete_registration=recovery.enabled(state))
            except binding.BindingViolation as exc:binding_errors.append({'sha256':digest,'reason':str(exc)[:500]})
            except (ValueError,OSError):pass
    identity=fingerprint([task['attempt'],data.get('invocation_count'),data.get('output_count'),
                          [entry['sha256'] for entry in preserved],missing,len(paths)])
    entries=task.setdefault('generation_violations',[])
    existing=next((entry for entry in entries if entry['id']==identity),None)
    if existing:return existing
    entry={**observation,'id':identity,'invocation_count':data.get('invocation_count'),
           'output_count':data.get('output_count'),'observed_returned_files':len(unique),
           'observed_returned_paths':len(paths),'observed_unique_returned_files':len(unique),
           'preserved_files':preserved,'unavailable_reported_files':missing,'acknowledged':False,
           'count_provenance':'caller_reported_not_provider_verified'}
    if binding_errors:entry['binding_errors']=binding_errors
    if data.get('_returned_outputs_inconsistent'):entry['returned_outputs_inconsistent']=True
    entries.append(entry)
    if not task.get('result'):
        task.update(status='failed',failure='Generation budget exceeded; preserve returns and request user direction.')
    save_state(work,state,'generation_budget_exceeded')
    return entry


def _acknowledge_violations(task, authorization, resolution):
    for entry in task.get('generation_violations',[]):
        if not entry.get('acknowledged'):
            entry.update(acknowledged=True,authorization=authorization,resolution=resolution,
                         authorization_provenance='caller_reported_user_request_not_verified')


def _reported_counts(state, key):
    totals=[];conflicts=[]
    for task in state['tasks'].values():
        for attempt in range(task['attempt']+1):
            reports=task.get('attempts',[])+task.get('generation_violations',[])
            known={report[key] for report in reports if report.get('attempt')==attempt
                   and type(report.get(key)) is int}
            if len(known)>1:conflicts.append({'task_id':task['id'],'attempt':attempt,'field':key,'reported_values':sorted(known)})
            hashes={report['sha256'] for report in task.get('attempts',[])
                    if report.get('attempt')==attempt and report.get('sha256')}
            hashes.update(preserved['sha256'] for report in task.get('generation_violations',[])
                          if report.get('attempt')==attempt for preserved in report.get('preserved_files',[]))
            if key=='output_count' and known and len(hashes)>max(known):
                conflicts.append({'task_id':task['id'],'attempt':attempt,'field':key,
                    'reported_values':sorted(known),'observed_unique_files_lower_bound':len(hashes),
                    'reason':'declared_output_count_below_known_file_count'})
            totals.append(max(known) if known else None)
    contradiction=any(entry.get('reason')=='declared_output_count_below_known_file_count' for entry in conflicts)
    return (sum(totals) if not contradiction and all(type(value) is int for value in totals) else None,
            sum(value for value in totals if type(value) is int),conflicts)


def _integrity(work, state):
    execution.integrity(work,state)
    source=state['source']
    for name,key in (('original','byte_sha256'),('working','working_sha256'),('guide','guide_sha256')):
        if sha256(relative_file(work,source[name])) != source[key]:
            raise ValueError('A saved source file changed: '+source[name])
    for item in source.get('reading_images', []):
        if (item['source_sha256'] != source['working_sha256']
                or sha256(relative_file(work, item['path'])) != item['sha256']):
            raise ValueError('A saved source reading image changed: ' + item['path'])
    if state.get('layout'):
        layout=json.loads((Path(work)/'layout.json').read_text(encoding='utf-8'))
        if layout != state['layout']: raise ValueError('The single submitted page description changed.')
    for task in state['tasks'].values():
        pairs={task['brief']:task['brief_sha256'],**task['references']}
        pairs.update({entry['brief']:entry['brief_sha256'] for entry in task.get('brief_history',[])})
        for references in task.get('references_history', []):
            pairs.update(references)
        if task.get('result'):
            result=task['result'];pairs[result['raw']]=result['sha256']
            if result.get('normalized'): pairs[result['normalized']]=result['normalized_sha256']
            if result.get('material_file'): pairs[result['material_file']]=result['material_sha256']
        for observation in task.get('attempts',[]):
            if observation.get('raw'):pairs[observation['raw']]=observation['sha256']
        for violation in task.get('generation_violations',[]):
            for preserved in violation.get('preserved_files',[]):
                pairs[preserved['raw']]=preserved['sha256']
        for path,digest in pairs.items():
            if sha256(relative_file(work,path)) != digest:
                raise ValueError('A recorded task file changed: '+path)
    for asset in state.get('assets',[]):
        if sha256(asset['path']) != asset['sha256']: raise ValueError('An extracted asset changed.')


def _done(state):
    receipt=state['done']
    if not Path(receipt['pptx']).is_file() or sha256(receipt['pptx']) != receipt['sha256']:
        raise ValueError('The completed presentation is missing or changed; the job is not reopened.')
    return copy.deepcopy(receipt)


def _check_pptx(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() or not {'ppt/presentation.xml','ppt/slides/slide1.xml'}.issubset(z.namelist()):
            raise ValueError('The existing presentation is incomplete.')


def _observed_board(task):
    board = task['board']
    observed = task['result'].get('role_review', {}).get('observed_cell_members')
    if observed is None:
        return board
    members = board['members']
    identities = [member['id'] for member in members]
    if (not isinstance(observed, list) or any(not isinstance(value, str) for value in observed)
            or len(observed) != len(identities) or len(set(observed)) != len(observed)
            or set(observed) != set(identities)):
        raise ValueError('Observed cell members must be the complete unique board member IDs.')
    slots = [member['slot'] for member in sorted(members, key=lambda member: (member['slot'][1], member['slot'][0]))]
    actual_slots = dict(zip(observed, slots))
    effective = copy.deepcopy(board)
    for member in effective['members']:
        member['slot'] = copy.deepcopy(actual_slots[member['id']])
    return effective


def _finish(work, state):
    build=Path(work)/'build';build.mkdir(exist_ok=True)
    for task in state['tasks'].values():
        if task['role'] not in {'board','wordart'} or task.get('processed'): continue
        started=time.perf_counter()
        if task['role']=='wordart':
            result=extract_wordart_asset(task['member'],relative_file(work,task['result']['raw']),
                                        Path(work)/'assets'/task['id'])
        else:
            effective_board = _observed_board(task)
            result=extract_assets(effective_board,relative_file(work,task['result'].get('material_file',task['result']['raw'])),
                                  Path(work)/'assets'/task['id'])
            if effective_board is not task['board']:
                planned = {member['id']:member['slot'] for member in task['board']['members']}
                actual = {member['id']:member['slot'] for member in effective_board['members']}
                for asset in result['assets']:
                    asset['planned_slot'] = copy.deepcopy(planned[asset['id']])
                    asset['observed_slot'] = copy.deepcopy(actual[asset['id']])
                task['extraction'] = {'observed_cell_members': copy.deepcopy(
                    task['result']['role_review']['observed_cell_members']),
                    'request_sha256': task['result'].get('request_sha256'),
                    'output_sha256': task['result']['sha256'],
                    'provenance': 'caller_observed_cell_identity_not_provider_verified'}
        for asset in result['assets']:
            asset['sha256']=sha256(asset['path'])
        state['assets'].extend(result['assets'])
        state['warnings'].extend(result['warnings'])
        if result.get('cell_mapping'):
            if task.get('extraction'):
                task['extraction']['cell_mapping'] = result['cell_mapping']
            else:
                task['extraction']={'cell_mapping':result['cell_mapping']}
        task['processed']=True
        state['processing_seconds'].append({'task_id':task['id'],'seconds':time.perf_counter()-started})
        save_state(work,state,'board_extracted')
    compiled=build/'compiled.pptx'; record=build/'compiled.report.json'
    if compiled.exists():
        _check_pptx(compiled)
        if record.exists():
            rendering=json.loads(record.read_text(encoding='utf-8'))
            if rendering.get('compiled_sha256') != sha256(compiled):
                raise ValueError('An existing compiled presentation changed.')
        else:
            rendering={'recovered_existing_container':True,'warnings':[],
                       'object_inspection':'not_recorded_before_interruption','compiled_sha256':sha256(compiled)}
            write_json(record,rendering)
    else:
        background=next(t for t in state['tasks'].values() if t['role']=='background')
        render_options={}
        if execution.enabled(state):
            from .renderer import TEXT_FIT_POLICY
            render_options['textfit_policy']=TEXT_FIT_POLICY
        rendering=build_presentation(state['layout'],relative_file(work,background['result']['normalized']),
                                     state['assets'],compiled,**render_options)
        _check_pptx(compiled)
        rendering['compiled_sha256']=sha256(compiled)
        write_json(record,rendering)
    destination=Path(work)/'output';destination.mkdir(exist_ok=True)
    final=destination/'PPT Rebuilder King.pptx'
    if final.exists():
        if sha256(final)!=sha256(compiled): raise ValueError('Refusing to replace an unrelated output presentation.')
    else:
        temporary=destination/'presentation.tmp'
        shutil.copyfile(compiled,temporary)
        _check_pptx(temporary)
        # A hard link publishes without replacing a file created by another process.
        os.link(temporary,final)
        temporary.unlink()
    state['warnings'].extend(rendering.get('warnings',[]))
    native_skipped=rendering.get('skipped',[])
    delivery_details={}
    delivery_instructions='Deliver this PowerPoint now and stop. No preview, visual check or further generation is part of this workflow.'
    if native_skipped:
        layout_items={item['id']:item for item in state['layout']['items']}
        missing_items=[]
        for skipped in native_skipped:
            item=layout_items.get(skipped['id'],{})
            warning_codes=list(dict.fromkeys(warning['code'] for warning in state['warnings']
                if isinstance(warning,dict) and warning.get('id')==skipped['id'] and warning.get('code')))
            missing_items.append({'id':skipped['id'],
                'name':item.get('lettering') or item.get('description') or item.get('text') or skipped['id'],
                'kind':item.get('kind'),'description':item.get('description'),
                'source_box':copy.deepcopy(item.get('box')),
                'target_box':copy.deepcopy(item.get('target',item.get('box'))),
                'reason':skipped.get('reason'),'warning_codes':warning_codes})
        delivery_details={'delivery_summary':{'priority':'missing_content',
            'missing_count':len(missing_items),'missing_items':missing_items,
            'message':f'本页有 {len(missing_items)} 项内容未放入 PPTX。交付时请先逐项说明缺失内容，再列出实有警告。'}}
        delivery_instructions=(f'Deliver this PowerPoint now. First disclose the {len(missing_items)} missing items '
            'by name from delivery_summary.missing_items before other warnings. State each omission plainly. '
            'Then stop. No preview, visual check or further generation is part of this workflow.')
    completed=datetime.now(timezone.utc)
    started=datetime.fromisoformat(state['started_at_utc'])
    receipt={'kind':'done','terminal':True,'pptx':str(final.resolve()),'sha256':sha256(final),
        'quality':'not_evaluated',**delivery_details,'expected_generation_tasks':len(state['tasks']),
        'warnings':state['warnings'],'skipped':native_skipped,
        'controller_wall_seconds':(completed-started).total_seconds(),
        'user_end_to_end_seconds':None,'within_600_seconds':None,
        'instructions':delivery_instructions}
    calls,known_calls,call_conflicts=_reported_counts(state,'invocation_count')
    outputs,known_outputs,output_conflicts=_reported_counts(state,'output_count')
    observed_hashes={report['sha256'] for task in state['tasks'].values()
                     for report in task.get('attempts',[]) if report.get('sha256')}
    observed_hashes.update(preserved['sha256'] for task in state['tasks'].values()
                          for violation in task.get('generation_violations',[])
                          for preserved in violation.get('preserved_files',[]))
    per_attempt=[]
    for task in state['tasks'].values():
        for attempt in range(task['attempt']+1):
            hashes={report['sha256'] for report in task.get('attempts',[])
                    if report.get('attempt')==attempt and report.get('sha256')}
            hashes.update(preserved['sha256'] for report in task.get('generation_violations',[])
                          if report.get('attempt')==attempt for preserved in report.get('preserved_files',[]))
            per_attempt.append({'task_id':task['id'],'attempt':attempt,'unique_known_files':len(hashes)})
    receipt.update(caller_reported_invocations=calls,caller_reported_outputs=outputs,
                   registered_outputs=sum(bool(task.get('result')) for task in state['tasks'].values()),
                   caller_reported_known_invocations=known_calls,caller_reported_known_outputs=known_outputs,
                   generation_count_provenance='caller_reported_not_provider_verified',
                   generation_count_method='maximum_reported_per_task_attempt; unknown attempts remain unknown',
                   observed_unique_returned_files_lower_bound=len(observed_hashes),
                   observed_file_count_scope='unique known raw file SHA256 across this job; not provider calls or billing',
                   observed_task_attempt_file_lower_bound=sum(entry['unique_known_files'] for entry in per_attempt),
                   observed_task_attempt_file_counts=per_attempt,
                   reported_count_conflicts=call_conflicts+output_conflicts,
                   generation_violations=[{'task_id':task['id'],**entry}
                       for task in state['tasks'].values() for entry in task.get('generation_violations',[])])
    if execution.enabled(state):
        receipt.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(state['runtime_identity']),
                       invocation_request_provenance=execution.PROVENANCE,
                       recorded_invocation_starts=sum(len(t.get('execution_history',[]))+
                           int(t['execution']['state']!='not_started') for t in state['tasks'].values()))
        if state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
            receipt.update(post_return_prompt_policy=execution.POST_RETURN_PROMPT_POLICY,
                           post_return_prompt_audit=execution.prompt_audit_summary(state))
        if binding.enabled(state):
            receipt.update(delivery_policy=binding.DELIVERY_POLICY,output_role_binding=binding.summary(state))
        if recovery.enabled(state):
            receipt.update(recovery_policy=state['recovery_policy'],recovery=copy.deepcopy(state['recovery']))
    write_json(Path(work)/'receipt.json',receipt)
    state.update(phase='done',done=receipt)
    save_state(work,state,'presentation_published')
    return copy.deepcopy(receipt)


def _pending_response(work,state,tasks):
    incomplete=[task for task in tasks if task.get('registration') or task.get('surplus_registration')]
    if recovery.enabled(state) and incomplete:
        return {'kind':'registration_required','generate_more':False,
                'tasks':[task['id'] for task in incomplete],
                'pending_results':[_registration_task(work,task,True) for task in incomplete],
                'instructions':'Inspect the saved existing output and complete this registration. Preserve the recorded request and image. No image invocation is permitted by this response.'}
    if execution.enabled(state):
        not_started=[t for t in tasks if t['execution']['state']=='not_started']
        if not_started:
            if state.get('execution_hold'):return _hold_response(not_started)
            return {'kind':'generate','jobs':[_public_task(work,t) for t in not_started],
                    'pending_results':[_registration_task(work,t,True) for t in tasks if t not in not_started],
                    'instructions':'Prepare only the explicitly not-started first invocations. Submit each begin_template before any image call. Already started tasks remain registration-only; no retry or extra call is permitted.'}
    return {'kind':'await_results','tasks':[t['id'] for t in tasks],'generate_more':False,
            'pending_results':[_registration_task(work,t,_strict_generation(state)) for t in tasks]}


def _response(state,value):
    if execution.enabled(state):
        value=copy.deepcopy(value)
        value.update(runtime_identity=copy.deepcopy(state['runtime_identity']),execution_policy=execution.MODE)
        if state.get('layer_generation_order') is not None:
            value['layer_generation_order'] = state['layer_generation_order']
        if state.get('post_return_prompt_policy') is not None:
            value['post_return_prompt_policy']=state['post_return_prompt_policy']
        if binding.enabled(state):value['delivery_policy']=binding.DELIVERY_POLICY
        if recovery.enabled(state):
            value['recovery_policy']=state['recovery_policy']
            value['recovery']=copy.deepcopy(state['recovery'])
            for task in value.get('jobs',[]):
                task['execution_contract']['instructions']='Submit the exact begin_template request. Invoke once only after its first invoke_once reply. Save and register the actual return with caller-reported counts and pixel-based role_review. Preserve all originals. Report unexpected extras in return order using returned_outputs. Source/reference files and other-task outputs cannot be submitted as returns.'
                if 'role_review_scope' in task:
                    task['role_review_scope']+=' Embedded images remain complete foreground rectangles, including their internal text, logos and people.'
            if value.get('kind')=='generate':
                value['instructions']='Prepare the listed attempts and submit begin_template. Call the image tool only after the first invoke_once reply for that exact attempt. Preserve completed assets and all existing returns.'
        if state.get('execution_hold'):value['remaining_generation_stopped']=True
        if value.get('kind')=='generate':
            value['instructions']='These are preparation cards, not image-call permits. Submit begin_template and invoke only after the first invoke_once reply. '+value.get('instructions','')
    return value


def _hold_response(tasks):
    return {'kind':'paused','tasks':[t['id'] for t in tasks],'generate_more':False,
            'reason':'user_stopped_remaining_generation',
            'resume_template':{'kind':'resume_unstarted','authorization':''},
            'instructions':'The recorded user instruction stopped remaining generation. Keep all files; existing outputs may still be registered. Resume only after a new user request is quoted through resume_unstarted. No image invocation is authorized.'}


def _drive(work,state):
    incomplete=[task for task in state['tasks'].values() if task.get('surplus_registration')]
    incomplete_ids={task['id'] for task in incomplete}
    reviewable=all(task['id'] in incomplete_ids and (
                       (entry.get('invocation_count') is None or
                        type(entry.get('invocation_count')) is int and entry['invocation_count'] >= 1)
                       if state.get('recovery_policy') == recovery.MODE_V2 else entry.get('invocation_count')==1)
                   and not entry.get('binding_errors') and not entry.get('returned_outputs_inconsistent')
                   for task in state['tasks'].values() for entry in task.get('generation_violations',[])
                   if not entry.get('acknowledged'))
    if recovery.enabled(state) and incomplete and reviewable and not state.get('execution_hold'):
        return _pending_response(work,state,incomplete)
    violations=_pending_generation_violations(state)
    if violations:
        return {'kind':'paused','tasks':violations,'generate_more':False,
            'reason':'generation_budget_exceeded',
            'instructions':'Known extra generation or outputs were reported. Preserve all existing files and stop image calls. Ask the user whether to retain an existing result. A submission with violation_authorization quoting that user request accepts an existing output for the named task only; it does not authorize another invocation or complete other tasks. If the user prohibits remaining generation and an actual output is missing, keep the job unfinished and report it; never substitute reference inputs or manufacture completion.'}
    if recovery.enabled(state) and not state.get('execution_hold'):
        metadata_pending=[task for task in state['tasks'].values() if task['status']=='waiting'
                          and task.get('registration',{}).get('error_kind')==binding.ReviewOutputHashRequired.kind]
        if metadata_pending:return _pending_response(work,state,metadata_pending)
    recovery.prepare(work,state)
    failed=[t['id'] for t in state['tasks'].values() if t['status']=='failed']
    if failed:
        response={'kind':'paused','tasks':failed,'generate_more':False,
            **({'failures':[{'task_id':t['id'],'kind':t.get('failure_kind'),'reason':t.get('failure')}
                            for t in state['tasks'].values() if t['status']=='failed']} if recovery.enabled(state) else {}),
            'instructions':'Keep completed files. Retry registration with an existing real output, or reopen a failed task only after the user requests another generation.'}
        if recovery.enabled(state):
            blocker=recovery.blocking_reason(state)
            response['recovery_blocker']=blocker
            guidance={
                'invocation_count_unknown':'Complete the actual invocation count through the existing output registration when it is known. Keep an unknown count unknown. Preserve the same file and its actual role observation.',
                'missing_request_binding':'Complete the existing output registration using its actual recorded invocation request binding. Preserve the same file and actual observation.',
                'missing_failure_output':'Register the saved actual failure output, or report the existing invocation failure truthfully when no output exists.',
                'binding_conflict':'A binding conflict remains in the saved evidence. Inspect its request, actual file SHA and source/task identity, and complete a valid existing output registration where possible. Preserve the full history.',
                'generation_violation':'Resolve the recorded invocation/output-count conflict using its existing real evidence and current controller action.',
                'user_stopped_generation':'The recorded user instruction stopped remaining generation. Preserve the files and follow the existing resume_unstarted action after a new user request.',
            }
            if blocker and blocker['code'] in guidance:
                response['instructions']=guidance[blocker['code']]
        return response
    if state['phase']=='layers':
        layers=[t for t in state['tasks'].values() if t['role'] in {'background','foreground'}]
        if state.get('layer_generation_order') == SEQUENTIAL_LAYER_ORDER:
            background=next(task for task in layers if task['role']=='background')
            foreground=next(task for task in layers if task['role']=='foreground')
            layers=[background] if background['status']!='recorded' else [foreground]
        unissued=[t for t in layers if not t['issued']]
        if unissued:
            if execution.enabled(state) and state.get('execution_hold'):return _hold_response(unissued)
            for task in unissued: task['issued']=True
            save_state(work,state,'layers_dispatched')
            instructions='Generate each layer once with one output per image-tool call. Save and register the first technically usable output immediately; do not compare or improve candidates. Parallel calls are optional when the host supports them; register background before foreground. Waiting or registration errors never authorize another image call.'
            if state.get('layer_generation_order') == SEQUENTIAL_LAYER_ORDER:
                instructions='Only the listed current layer is ready for preparation. Register its actual usable output before the controller releases the next layer. Detailed page description follows both recorded layers. Preserve existing outputs while waiting or correcting registration.'
            return {'kind':'generate','jobs':[_public_task(work,t) for t in unissued],
                'source':state['source'],'instructions':instructions}
        waiting=[t['id'] for t in layers if t['status']!='recorded']
        if waiting:return _pending_response(work,state,[t for t in layers if t['id'] in waiting])
        if state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY:
            _create_art_tasks(work, state, state['layout'])
        else:
            state['phase']='describe';save_state(work,state,'layers_ready')
    if state['phase']=='describe':
        if state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY:
            return _source_description_action(work, state)
        container_fields, container_description = _container_description_context(state)
        action={'kind':'describe','files':[str(relative_file(work,state['source'][k])) for k in ('working','guide')],
            'schema_file':str(ROOT/'references'/'scene.md'), **container_fields,
            'reply_template':{'kind':'layout','layout':{'items':[]}},
            'instructions':'Read only these same-source images. Describe structures, each visible native text line, and independent artwork once. '+_TEXT_DESCRIPTION_GUIDANCE+container_description+'Transcribe visible characters and punctuation exactly, without replacing an unusual phrase with a more familiar one. Read each text line, subheading, label and icon from its own source position; shared styles do not imply a shared vertical position. For artistic-writing items, fill lettering with all exact source characters and description with colours, brushwork, original outlines, material and arrangement. The largest artistic-writing group alone will use standalone true-alpha generation; all other art keeps the existing board route. Artistic lettering stays an art item; generated foreground is not a source of text or geometry facts. Complete this in the existing one-pass description, with no extra analysis call. '+EMBEDDED_IMAGE_POLICY+' For each standalone embedded image use one art item with embedded_image:true and its full rectangular box. Image-internal writing remains inside that artwork; independent page text keeps its native-text or artistic-lettering route.'}
        if state.get('layer_generation_order') == SEQUENTIAL_LAYER_ORDER:
            action['instructions']=action['instructions'].replace(
                'The largest artistic-writing group alone will use standalone true-alpha generation; all other art keeps the existing board route.',
                'At the layout root primary_wordart_id is optional. null keeps every art member on component boards. '
                'An eligible written-art ID selects that standalone member. Omission retains the largest written-art rule. '
                'Keep ordinary supported-font writing as native text and preserve complex lettering effects as artwork. '
                'Box-based items may use source_bounds=[left,top,right,bottom]. Compound art may use source_parts '
                'for its complete writing, backing and detached strokes. Retain source placement by omitting target.')
        if state.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
            foreground = next(task for task in state['tasks'].values() if task['role'] == 'foreground')
            foreground_file = str(relative_file(work, foreground['result']['normalized']))
            action['files'].append(foreground_file)
            action['foreground_file'] = foreground_file
            action['foreground_component_policy'] = FOREGROUND_COMPONENT_POLICY
            action['reply_template']['layout']['primary_wordart_id'] = None
            action['instructions'] = (
                'Complete one page description using the original source and its coordinate guide for '
                'all text, source_bounds/source_parts, sizes and final placement. The third file is the '
                'actual generated foreground normalized to 1920x1080. For every art item also measure '
                'foreground_box=[x,y,width,height] around its complete subject in that third file. '
                'These sampling coordinates are separate from original source facts and PPT targets. '
                'Include detached strokes and arcs, exclude neighbouring labels and background paper '
                'outside the subject. Keep photo interiors and real badge backings. Every art item, '
                'including the largest title, uses these foreground crops on component boards. '
                'primary_wordart_id is null. Read source lettering exactly and retain each native '
                'line Y. Use source_parts for complete title/arc bounds and omit target for source '
                'placement. For a styled table use editable shapes plus one text object per cell line, '
                'or one table with its own text. Assign each visible cell text once. '
                + _TEXT_DESCRIPTION_GUIDANCE + container_description + EMBEDDED_IMAGE_POLICY)
            if state.get('keyed_material_policy') == KEYED_MATERIAL_POLICY:
                action['instructions'] += (
                    'For a stacked icon and native caption, measure their source boxes separately. '
                    'Locate the caption glyph top and baseline in the original source, retaining '
                    'the visible gap below the icon. Use the caption line box for its native text.')
        return action
    if state['phase']=='boards':
        active=[state['tasks'][i] for i in state.get('active_batch',[])]
        waiting=[t['id'] for t in active if t['status']!='recorded']
        if waiting:return _pending_response(work,state,[t for t in active if t['id'] in waiting])
        tasks=[t for t in state['tasks'].values() if t['role'] in {'board','wordart'} and not t['issued']]
        tasks.sort(key=lambda task:task['role']!='wordart')
        if tasks:
            if execution.enabled(state) and state.get('execution_hold'):return _hold_response(tasks)
            batch=tasks[:3];state['active_batch']=[t['id'] for t in batch]
            for task in batch:task['issued']=True
            save_state(work,state,'boards_dispatched')
            field_instruction=('board jobs also require true PNG transparency.'
                if state.get('material_background_mode')==MATERIAL_MODE
                else 'board jobs retain their existing opaque field.')
            return {'kind':'generate','jobs':[_public_task(work,t) for t in batch],
                'instructions':'Generate each listed image once with one output per tool call. A wordart job is the standalone primary title and requires true transparency; '+field_instruction+' At most these three jobs may be active. Register first usable results serially and immediately; do not generate alternatives or retry while waiting. Do not generate a new batch until all current results are recorded.'}
        return _finish(work,state)
    raise ValueError('The job phase cannot be continued.')


def _adopt_single_call_surplus(work,state,task,data,observation):
    """Select in declared return order, never invoke or rank for appearance."""
    version_two = state.get('recovery_policy') == recovery.MODE_V2
    outputs=data.get('returned_outputs');count=data.get('output_count')
    if data.get('_returned_outputs_inconsistent'):return False
    invocations = data.get('invocation_count')
    known_invocations = type(invocations) is int and invocations >= 1
    reviewable_invocations = known_invocations or invocations is None
    if version_two and outputs is None:
        supplied_paths = data.get('returned_files')
        if supplied_paths is None and isinstance(data.get('file'), str):
            supplied_paths = [data['file']]
        if isinstance(supplied_paths, list):
            outputs = [{'file': path, 'role_review': data.get('role_review') if index == 0 else None,
                        'tool_output_id': data.get('tool_output_id') if index == 0 else None}
                       for index, path in enumerate(supplied_paths)]
            data = {**data, 'returned_outputs': outputs}
    if ((not reviewable_invocations if version_two else invocations!=1)
            or type(count) is not int or count<(1 if version_two else 2)
            or not isinstance(outputs,list) or len(outputs)!=count or state.get('execution_hold')
            or data.get('stop_remaining_generation')):return False
    if not version_two and any(identity!=task['id'] for identity in _pending_generation_violations(state)):return False
    if any(not v.get('acknowledged') and (
            v.get('invocation_count') is not None and
            (type(v.get('invocation_count')) is not int or v['invocation_count'] < 1)
            if version_two else v.get('invocation_count')!=1)
           for v in task.get('generation_violations',[])):return False
    if any(not isinstance(entry,dict) or not isinstance(entry.get('file'),str)
           or not Path(entry['file']).is_file()
           or (not recovery.enabled(state) and not isinstance(entry.get('role_review'),dict)) for entry in outputs):return False
    paths=[str(Path(entry['file']).resolve()) for entry in outputs]
    if data.get('returned_files') is not None and [str(Path(p).resolve()) for p in data['returned_files']]!=paths:return False
    if data.get('file') and str(Path(data['file']).resolve())!=paths[0]:return False
    original={**data,'returned_files':paths,'file':paths[0]}
    evidence=_returned_file_evidence(original)
    violation=_generation_violation(work,state,task,original,observation,evidence)
    if violation is None:return False
    preserved={entry['sha256']:entry['raw'] for entry in violation['preserved_files']}
    returns=[{'index':i,'sha256':sha256(path),'raw':preserved[sha256(path)],
              'tool':outputs[i].get('tool',data.get('tool')),'tool_output_id':outputs[i].get('tool_output_id'),
              'declared_role_review':binding.review_evidence(outputs[i].get('role_review'))} for i,path in enumerate(paths)]
    # Claim every known return before choosing one. Unselected files are still
    # from this declared invocation and cannot be relabelled as another task.
    binding_failed=False
    for index,entry in enumerate(outputs):
        claim={**data,'tool':entry.get('tool',data.get('tool')),'tool_output_id':entry.get('tool_output_id')}
        try:binding.claim_return(work,state,task,claim,Path(paths[index]),returns[index]['sha256'],
                                 incomplete_registration=recovery.enabled(state))
        except binding.BindingViolation as exc:
            binding_failed=True
            task.update(status='failed',failure='Image result binding: '+str(exc))
            task['attempts'].append({**observation,'outcome':'binding_rejected','sha256':returns[index]['sha256'],
                                     'raw':returns[index]['raw'],'reason':task['failure']})
            returns[index].update(outcome='binding_rejected',reason=task['failure'])
        except (ValueError,OSError):
            pass  # Broken images remain owned/preserved and fail selection below.
    if binding_failed:
        if recovery.enabled(state):
            task.update(failure_kind='binding',hard_binding_failure=True)
            task.pop('registration',None)
            task.pop('surplus_registration',None)
        for entry in returns:entry.setdefault('outcome','preserved_not_selected')
        task['surplus_resolution']={'policy':binding.DELIVERY_POLICY,'selected_index':None,'returns':returns,
            'invocation_count':invocations,'output_count':count,'hard_binding_failure':True,
            'provenance':('caller_declared_invocations_and_return_order_not_provider_verified' if version_two else
                          'caller_declared_single_invocation_and_return_order_not_provider_verified'),
            'selection':'All known files retained and ownership checked; source substitution or ownership contradiction forbids selection.'}
        save_state(work,state,'surplus_binding_rejected');return True
    previous=task.get('surplus_resolution')
    if previous is not None:
        if [(e['index'],e['sha256']) for e in previous['returns']]!=[(e['index'],e['sha256']) for e in returns]:
            raise ValueError('The first surplus-return order and output ownership are immutable.')
        selected=previous.get('selected_index')
        if selected is None and not previous.get('registration_incomplete'):return True
        if previous.get('registration_incomplete'):
            selected=None
        else:
            entry=outputs[selected]
            selected_data={k:v for k,v in data.items() if k not in {'returned_outputs','returned_files','role_review','tool_output_id'}}
            selected_data.update(file=paths[selected],role_review=entry['role_review'],tool_output_id=entry.get('tool_output_id'))
            if entry.get('tool') is not None:selected_data['tool']=entry['tool']
            _submit(work,state,selected_data,_surplus=True);return True
    order=list(range(len(outputs)))
    if recovery.enabled(state) and task['role']=='board':
        complete=[];partial=[]
        for index,path in enumerate(paths):
            try:
                prepared_path, _ = _prepared_material_file(work, task, Path(path), sha256(path))
                preflight=inspect_board_cells(task['board'],prepared_path)
            except (ValueError,OSError) as error:
                preflight={'complete':False,'status':'unavailable','reason':str(error)[:500],
                    'scope':'existing_return_cell_geometry_not_visual_fidelity_or_host_role_review'}
            returns[index]['cell_geometry_preflight']=preflight
            if preflight['complete']:complete.append(index)
            else:partial.append(index)
        order=complete+partial
    selected=None
    for index in order:
        entry=outputs[index]
        candidate={k:v for k,v in data.items() if k not in {'returned_outputs','returned_files','role_review','tool_output_id'}}
        # Only output-specific evidence may differ; task identity and locked
        # request binding remain the top-level invocation's immutable values.
        candidate.update(file=paths[index],role_review=entry.get('role_review'),tool_output_id=entry.get('tool_output_id'))
        if entry.get('tool') is not None:candidate['tool']=entry['tool']
        try:
            response=_submit(work,state,candidate,_surplus=True)
        except (ValueError,OSError) as exc:
            returns[index]['outcome']='rejected';returns[index]['reason']=str(exc)[:500]
            continue
        if recovery.enabled(state) and isinstance(response,dict) and response.get('kind')=='registration_required':
            task['surplus_registration']={'returns':returns,'output_count':count}
            if version_two:
                task['surplus_registration']['invocation_count'] = invocations
            task['surplus_resolution']={'policy':binding.DELIVERY_POLICY,'selected_index':None,'returns':returns,
                'invocation_count':invocations,'output_count':count,'registration_incomplete':True,
                'selection':'Await the existing returns in their original order; no new invocation is permitted.'}
            save_state(work,state,'surplus_registration_required');return True
        if recovery.enabled(state) and task['status']!='recorded':
            returns[index].update(outcome='rejected',reason=task.get('failure','Existing return failed validation.'))
            continue
        selected=index;returns[index]['outcome']='selected';break
    for entry in returns:
        entry.setdefault('outcome','preserved_not_selected')
    task['surplus_resolution']={'policy':binding.DELIVERY_POLICY,'selected_index':selected,'returns':returns,
        'invocation_count':invocations,'output_count':count,'provenance':(
            'caller_declared_invocations_and_return_order_not_provider_verified' if version_two else
            'caller_declared_single_invocation_and_return_order_not_provider_verified'),
        'selection':'first technically valid, source-safe and host-role-reviewed existing return; no appearance ranking or new invocation'}
    if selected is not None:
        task.pop('surplus_registration',None)
        violation.update(acknowledged=True,resolution='first_usable_existing_return_by_frozen_policy',
                         resolution_policy=recovery.MODE_V2 if version_two else binding.DELIVERY_POLICY)
        state['warnings'].append({'code':'existing_returns_adopted' if version_two else 'single_call_surplus_adopted','task_id':task['id'],
            'attempt':task['attempt'],'selected_index':selected,'actual_reported_outputs':count,
            **({'actual_reported_invocations': invocations,
                'invocation_count_status': 'unknown' if invocations is None else 'caller_reported'} if version_two else {}),
            'message':('Caller-reported extra invocations or outputs were preserved with their actual counts. '
                'The first usable existing return was adopted without another call.' if version_two else
                'One caller-reported invocation returned extra images. All known originals were preserved; the first usable existing return was adopted without another call.')})
    else:
        task.update(status='failed',failure='All existing returns failed technical, source or role review; preserve them without automatic generation.')
        if version_two:
            task.pop('surplus_registration', None)
            violation.update(acknowledged=True, resolution='existing_returns_reviewed_all_failed',
                             resolution_policy=recovery.MODE_V2)
            state['warnings'].append({'code':'existing_returns_all_failed','task_id':task['id'],
                'attempt':task['attempt'],'actual_reported_invocations':invocations,
                'actual_reported_outputs':count,
                'message':'Every existing return failed actual validation. Counts and failures remain recorded; this review grants no image invocation.'})
    save_state(work,state,'single_call_surplus_reviewed');return True


def _submit(work,state,data,_surplus=False):
    if not isinstance(data,dict): raise ValueError('Input must be a JSON object.')
    if (state['phase'] == 'describe' and 'items' in data
            and set(data) <= {'items', 'styles', 'background_regions', 'format', 'primary_wordart_id'}):
        data = {'kind': 'layout', 'layout': data}
    if data.get('kind')=='resume_unstarted':
        if not execution.enabled(state) or not state.get('execution_hold'):
            raise ValueError('There is no recorded remaining-generation stop to resume.')
        authorization=_authorization(data.get('authorization'),'authorization')
        state.setdefault('execution_resume_authorizations',[]).append({'authorization':authorization,
            'at_utc':datetime.now(timezone.utc).isoformat(),'provenance':'caller_reported_user_request_not_verified'})
        state.pop('execution_hold')
        save_state(work,state,'remaining_generation_resumed');return
    if data.get('kind')=='layout':
        source_first = state.get('source_layout_policy') == SOURCE_LAYOUT_POLICY
        options = {'source_layout_policy': SOURCE_LAYOUT_POLICY} if source_first else {}
        layout=normalize_layout(data.get('layout',{}), **options)
        if state.get('layout'):
            if layout != state['layout']: raise ValueError('The one-pass page description is already frozen.')
            return
        if state['phase']!='describe': raise ValueError('Record both layers before describing the page.')
        if state.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY:
            missing = [item['id'] for item in layout['items']
                       if item['kind'] == 'art' and 'foreground_box' not in item]
            if missing:
                action = _drive(work, state)
                action.update(reason='foreground_sampling_requires_correction', generate_more=False,
                    issues=[{'id': identity, 'field': 'foreground_box'} for identity in missing],
                    reply_template={'kind': 'layout', 'layout': {**layout, 'primary_wordart_id': None}})
                return action
        if source_first or recovery.enabled(state):
            issues=preflight_native_text(layout)
            if issues:
                if source_first:
                    return _source_description_action(work, state, layout=layout, issues=issues)
                container_fields, container_description = _container_description_context(state)
                return {'kind':'describe','reason':'native_text_requires_correction',
                    'generate_more':False,'issues':issues,
                    'files':[str(relative_file(work,state['source'][key])) for key in ('working','guide')],
                    'schema_file':str(ROOT/'references'/'scene.md'),
                    **container_fields,
                    'reply_template':{'kind':'layout','layout':layout},
                    'instructions':_TEXT_DESCRIPTION_GUIDANCE+container_description+'Compare the flagged native text lines with the current source and coordinate guide. '
                        'Recheck their boxes, sizes, spacing and visible line breaks, keeping the exact source wording. '
                        'Submit the corrected page description in this same job. Font widths marked estimated remain '
                        'uncertain and need source-based checking. Both generated layers remain ready for reuse; '
                        'this action authorizes page-description correction only.'}
        if source_first:
            state.update(layout=layout, layout_source_sha256=state['source']['working_sha256'],
                         source_layout_fingerprint=fingerprint(layout), phase='layers')
            state['scene_allocation'] = _scene_allocation(state, layout)
            if state.get('source_layer_scope_policy') is not None:
                state['source_layer_scope'] = _source_layer_scope(layout)
            write_json(Path(work) / 'layout.json', layout)
            _create_layer_tasks(work, state)
            save_state(work, state, 'description_frozen')
        else:
            _create_art_tasks(work, state, layout)
        return
    task=state['tasks'].get(data.get('task_id'))
    if task is None or task['role']!=data.get('role'):
        raise ValueError('The result must identify its exact task and role.')
    if not task['issued']: raise ValueError('This task has not been dispatched.')
    if data.get('attempt',0)!=task['attempt']:
        raise ValueError('The result attempt differs from the currently dispatched invocation.')
    if data.get('kind')=='begin_generation':
        if not execution.enabled(state):raise ValueError('begin_generation requires an explicit execution job; old jobs are not migrated.')
        if _pending_generation_violations(state):raise ValueError('The job is paused for a generation violation; no image call can begin.')
        if state.get('execution_hold'):raise ValueError('Remaining generation is paused by the user; resume_unstarted with a new user request first.')
        if any(t['status']=='failed' for t in state['tasks'].values()):
            authorizations = task.get('retry_authorizations', [])
            explicit_retry = isinstance(authorizations, list) and any(
                isinstance(entry, dict)
                and type(entry.get('attempt')) is int
                and entry['attempt'] == task['attempt']
                and isinstance(entry.get('authorization'), str)
                and 1 <= len(entry['authorization'].strip()) <= 2000
                for entry in authorizations
            )
            bounded_retry = recovery.prepared_attempt(state, task)
            # An authorized current retry is independent of another failed task.
            # Ordinary first invocations and failed prior attempts stay blocked.
            if (task['status'] != 'waiting' or task['attempt'] < 1
                    or not (explicit_retry or bounded_retry)):
                raise ValueError('The job is paused for a failed task; correct its existing registration or obtain an explicit retry authorization.')
        if task['role'] in {'board','wordart'} and task['id'] not in state['active_batch']:
            raise ValueError('Begin only a member of the active batch.')
        permit=execution.begin(work,task,_public_task(work,task),data.get('request'))
        if permit is None:
            return {'kind':'await_results','generate_more':False,'tasks':[task['id']],
                    'pending_results':[_registration_task(work,task,True)],
                    'instructions':'This invocation was already begun. Do not call again; register its existing output or wait.'}
        if binding.enabled(state):permit=binding.decorate_permit(task,permit)
        if (task.get('source_layer_scope') is not None
                or task.get('foreground_component_policy') == FOREGROUND_COMPONENT_POLICY) and 'role_review_scope' in permit:
            permit['role_review_scope'] = _role_review_scope(task)
        save_state(work,state,'invocation_started')
        return permit
    if execution.enabled(state):execution.require_started(task)
    if binding.enabled(state) and not _surplus:data=binding.normalize_known_returns(data)
    if 'stop_remaining_generation' in data and type(data['stop_remaining_generation']) is not bool:
        raise ValueError('stop_remaining_generation must be a boolean user-direction declaration.')
    no_output_failure=(data.get('outcome')=='failed' and not data.get('file')
                       and not data.get('returned_files') and not data.get('returned_outputs'))
    for field in ('invocation_count','output_count'):
        count=data.get(field)
        minimum=0 if field=='output_count' and no_output_failure else 1
        if count is not None and (type(count) is not int or count<minimum):
            description='nonnegative' if minimum==0 else 'positive'
            raise ValueError(field+' must be an actual '+description+' integer, or omitted when unknown.')
    observation={'attempt':task['attempt'],'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        **{k:data.get(k) for k in ('tool','tool_call_id','tool_output_id','invocation_count','output_count','executed_prompt')}}
    if binding.enabled(state) and not _surplus:
        if data.get('stop_remaining_generation'):
            quote=_authorization(data.get('violation_authorization'),'violation_authorization')
            state['execution_hold']={'authorization':quote,'provenance':'caller_reported_user_request_not_verified'}
            save_state(work,state,'remaining_generation_stopped')
        if _adopt_single_call_surplus(work,state,task,data,observation):
            if recovery.enabled(state) and task.get('surplus_registration'):
                return _pending_response(work,state,[task])
            return
    if execution.enabled(state) and data.get('file') and not task.get('result'):
        task['execution']['state']='returned'
    authorization=None;evidence=None
    if _strict_generation(state) and not _surplus:
        if data.get('violation_authorization') is not None:
            authorization=_authorization(data['violation_authorization'],'violation_authorization')
        evidence=_returned_file_evidence(data)
        _generation_violation(work,state,task,data,observation,evidence)
        pending=any(not entry.get('acknowledged') for entry in task.get('generation_violations',[]))
        if binding.enabled(state) and pending and (state.get('execution_hold') or data.get('stop_remaining_generation')):return
        if pending and authorization is None:return
        if authorization is not None and not pending:
            if any(entry.get('acknowledged') and entry.get('authorization')==authorization
                   for entry in task.get('generation_violations',[])):
                authorization=None  # Re-registering the identical accepted file is idempotent.
            elif recovery.enabled(state) and data.get('stop_remaining_generation'):
                authorization=None
            else:
                raise ValueError('No pending generation violation belongs to this task; authorization cannot apply to another task.')
    if execution.enabled(state) and data.get('stop_remaining_generation'):
        quote=_authorization(data.get('violation_authorization'),'violation_authorization')
        hold={'authorization':quote,'provenance':'caller_reported_user_request_not_verified'}
        if state.get('execution_hold')!=hold:
            state['execution_hold']=hold;save_state(work,state,'remaining_generation_stopped')
    if task['role']=='foreground' and any(t['role']=='background' and t['status']!='recorded' for t in state['tasks'].values()) and not recovery.enabled(state):
        raise ValueError('Keep this foreground file and register the background first.')
    if data.get('outcome')=='failed':
        if task.get('result'): raise ValueError('A recorded first result cannot be replaced by a failure.')
        if not isinstance(data.get('reason'),str) or not data['reason'].strip() or len(data['reason'])>1000:
            raise ValueError('A failed invocation needs a short sanitized reason.')
        pending=recovery.pending_return(task) if recovery.enabled(state) else None
        if pending:
            task['registration']=pending
            report={'attempt':task['attempt'],'reason':data['reason'],
                    'invocation_count':data.get('invocation_count'),'output_count':data.get('output_count'),
                    'provenance':'caller_reported_followup_not_provider_verified',
                    'resolution':'review_existing_return_before_generation_failure_recovery'}
            reports=task.setdefault('registration_failure_reports',[])
            if report not in reports:
                reports.append(report)
                save_state(work,state,'existing_output_review_required')
            return _hold_response([task]) if state.get('execution_hold') else _pending_response(work,state,[task])
        if task['status']=='failed' and task.get('failure')==data['reason']:return
        hard_binding=recovery.enabled(state) and task.get('hard_binding_failure')
        task.update(status='failed')
        if not hard_binding:task['failure']=data['reason']
        if execution.enabled(state):task['execution']['state']='failed'
        if recovery.enabled(state) and not hard_binding:task['failure_kind']='generation_failure'
        task['attempts'].append({**observation,'outcome':'generation_failure' if recovery.enabled(state) else 'failed','reason':data['reason']})
        save_state(work,state,'generation_paused');return
    if not data.get('file'):
        if recovery.enabled(state):
            task['registration']={**(recovery.pending_return(task) or {}),
                                  'reason':'Record the actual saved image file, or complete its pending role review.'}
            save_state(work,state,'registration_required')
            return _pending_response(work,state,[task])
        raise ValueError('Record an actual saved image file.')
    path=Path(data['file']).resolve()
    if recovery.enabled(state) and not path.is_file():
        task['registration']={**(recovery.pending_return(task) or {}),
                              'reason':'The supplied file is missing. Register the preserved real output if available.'}
        save_state(work,state,'registration_required')
        return _pending_response(work,state,[task])
    digest=sha256(path)
    if task.get('result'):
        if digest!=task['result']['sha256']: raise ValueError('The first registered output is immutable.')
        if binding.enabled(state):binding.validate_return(work,state,task,data,path,digest)
        if authorization is not None:
            _acknowledge_violations(task,authorization,'accept_existing_output_without_generation')
            save_state(work,state,'generation_violation_acknowledged')
        if execution.enabled(state) and state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
            before=len(task.get('prompt_followup_reports',[]))
            execution.audit_returned_prompt(work,task,data,digest)
            if len(task.get('prompt_followup_reports',[]))!=before:
                report=task['prompt_followup_reports'][-1]
                state['warnings'].append({'code':'post_return_prompt_followup','task_id':task['id'],
                    'attempt':task['attempt'],'output_sha256':digest,'kind':report['kind'],
                    'reported_prompt_sha256':report['reported_prompt_sha256'],
                    'provenance':execution.PROMPT_AUDIT_PROVENANCE,
                    'message':'A later caller-reported executed prompt was retained separately. The first audit snapshot and output remain unchanged; no new image call is authorized.'})
                save_state(work,state,'prompt_report_supplemented')
        return
    key='magenta' if task['role']=='foreground' else task['board']['key'] if task['role']=='board' else None
    native_normalized=None
    material_file=None
    material_preparation=None
    bound=None
    validation_stage='binding'
    try:
        pending=task.get('registration',{})
        if (pending.get('error_kind')==binding.ReviewOutputHashRequired.kind
                and pending.get('sha256')!=digest):
            raise binding.BindingViolation('Correct the review hash using the same preserved actual output bytes.')
        if execution.enabled(state) and state.get('post_return_prompt_policy')!=execution.POST_RETURN_PROMPT_POLICY:
            execution.check_reported_prompt(work,task,data)
        _reject_reference_board_return(work,task,path,digest)
        if binding.enabled(state):
            validation_stage='decoding'
            bound=binding.validate_return(work,state,task,data,path,digest,
                                          incomplete_registration=recovery.enabled(state))
        validation_stage='material'
        material_path, material_preparation = _prepared_material_file(work, task, path, digest)
        if material_preparation is not None:
            material_file = material_path
        validation=(validate_wordart_output(material_path) if task['role']=='wordart' else
                    validate_native_output(material_path,role=task['role']) if _native_material_task(task) else
                    validate_output(material_path,key=key))
        if task['role']=='foreground' and _native_material_task(task):
            native_normalized=Path(work)/'layers'/'foreground.png'
            normalize_layer(material_path,task['role'],state['source']['transform'],native_normalized,
                            background_mode=MATERIAL_MODE)
            validate_native_output(native_normalized,role='foreground')
    except (ValueError,OSError) as exc:
        if recovery.enabled(state):
            category=('registration_required' if isinstance(exc,binding.RegistrationRequired) else
                      'binding' if isinstance(exc,binding.BindingViolation) else
                      'role_mismatch' if isinstance(exc,binding.RoleMismatch) else validation_stage)
            preserved=recovery.preserve(work,task,path,digest)
            review=binding.review_evidence(data.get('role_review'))
            entry={**observation,'outcome':category,'sha256':digest,'raw':preserved,
                   'reason':str(exc)[:1000],'role_review':review}
            if isinstance(exc,binding.ReviewOutputHashRequired):entry['error_kind']=exc.kind
            if isinstance(review,dict):entry['observed']=review.get('observed')
            previous=task['attempts'][-1] if task['attempts'] else {}
            same=all(previous.get(key)==entry.get(key) for key in ('attempt','outcome','sha256','reason','role_review'))
            if not same:task['attempts'].append(entry)
            if category=='registration_required':
                if task['status']=='failed' and not _surplus:
                    if not same:save_state(work,state,'failed_output_registration_incomplete')
                    return _drive(work,state)
                if _surplus:
                    task.update(status='waiting')
                    task.pop('failure',None)
                    task.pop('failure_kind',None)
                task['registration']={'raw':preserved,'sha256':digest,'reason':str(exc)[:1000],
                                      'invocation_count':data.get('invocation_count'),'output_count':data.get('output_count')}
                if 'error_kind' in entry:task['registration']['error_kind']=entry['error_kind']
                elif pending.get('error_kind')==binding.ReviewOutputHashRequired.kind:
                    task['registration']['error_kind']=pending['error_kind']
                if not same:save_state(work,state,'registration_required')
                return _pending_response(work,state,[task])
            task.update(status='failed',failure=str(exc)[:1000],failure_kind=category)
            if category=='binding':task['hard_binding_failure']=True
            task.pop('registration',None)
            task['execution']['state']='failed'
            save_state(work,state,'image_'+category)
            if category=='binding':raise
            return
        task.update(status='failed',failure='Image registration: '+str(exc)[:500])
        preserved=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}-unusable.bin"
        preserved.parent.mkdir(exist_ok=True)
        if not preserved.exists():preserved.write_bytes(path.read_bytes())
        if sha256(preserved)!=digest:raise ValueError('The unusable output changed while being preserved.')
        task['attempts'].append({**observation,'outcome':'unreadable','sha256':digest,
            'raw':preserved.relative_to(work).as_posix(),'reason':task['failure']})
        save_state(work,state,'image_unreadable')
        raise ValueError('Image registration stopped. Preserve existing files; no automatic generation is authorized. '+str(exc)) from exc
    raw=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}.png"
    raw.parent.mkdir(exist_ok=True)
    if raw.exists() and sha256(raw)!=digest:raise ValueError('A stored output was changed.')
    if not raw.exists():raw.write_bytes(path.read_bytes())
    if sha256(raw)!=digest or sha256(path)!=digest:raise ValueError('The returned file changed while recording.')
    result={'raw':raw.relative_to(work).as_posix(),'sha256':digest,
        'tool':data.get('tool'),'tool_call_id':data.get('tool_call_id'),
        'tool_output_id':data.get('tool_output_id'),'identity_scope':'local_file' if not data.get('tool_output_id') else 'caller_reported',
        'invocation_count':data.get('invocation_count'),'executed_prompt':data.get('executed_prompt'),
        'output_count':data.get('output_count'),
        'validation':validation}
    if material_file is not None:
        result.update(material_file=material_file.relative_to(work).as_posix(),
                      material_sha256=sha256(material_file), material_preparation=material_preparation)
    if bound is not None:result.update(bound)
    if evidence is not None:
        result.update(observed_returned_paths=len(evidence['paths']),
                      observed_unique_returned_files=len({digest for _,digest in evidence['known']}))
    if task['role'] in {'background','foreground'}:
        normalized=Path(work)/'layers'/(task['role']+'.png')
        if native_normalized is None:
            normalize_layer(raw,task['role'],state['source']['transform'],normalized)
        result.update(normalized=normalized.relative_to(work).as_posix(),normalized_sha256=sha256(normalized))
    if execution.enabled(state) and state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
        audit=execution.audit_returned_prompt(work,task,data,digest)
        result['post_return_prompt_status']=audit['status']
        if audit['status']=='mismatch':
            state['warnings'].append({'code':'post_return_prompt_mismatch','task_id':task['id'],
                'attempt':task['attempt'],'output_sha256':digest,'provenance':execution.PROMPT_AUDIT_PROVENANCE,
                'message':'The caller-reported executed prompt differs from the locked request. This existing technically valid output was retained without a new image call; exact prompt compliance is not established.'})
    task.update(status='recorded',result=result)
    if execution.enabled(state):
        task['execution']['state']='recorded'
        result.update(invocation_request_file=task['execution']['request_file'],
                      invocation_request_sha256=task['execution']['request_sha256'],
                      request_provenance=execution.PROVENANCE)
    if authorization is not None:
        _acknowledge_violations(task,authorization,'accept_existing_output_without_generation')
    task['attempts'].append({**observation,'outcome':'recorded','raw':result['raw'],'sha256':digest})
    task.pop('failure',None)
    task.pop('failure_kind',None)
    task.pop('registration',None)
    state['warnings'].extend(validation.get('warnings',[]))
    save_state(work,state,'image_registered')


def run(work, *, source=None, data=None, retry_task=None, retry_authorization=None,
        execution_policy=None,expected_package=None,post_return_prompt_policy=None,delivery_policy=None,
        recovery_policy=None, source_layout_policy=None, board_sampling_policy=None,
        source_board_reference_policy=None, primary_source_reference_policy=None,
        layer_generation_order=None, foreground_component_policy=None, keyed_material_policy=None):
    """Advance only explicitly identified work; external generation stays external."""
    if sum(value is not None for value in (source,data,retry_task))>1:
        raise ValueError('Use a source, a submission, or an explicit retry, one at a time.')
    if retry_authorization is not None and retry_task is None:
        raise ValueError('retry_authorization applies only to the named --retry-task.')
    if execution_policy not in (None,execution.MODE):raise ValueError('Unsupported execution policy.')
    execution.validate_prompt_policy(post_return_prompt_policy)
    binding.validate_policy(delivery_policy)
    recovery.validate_policy(recovery_policy)
    _validate_source_policies(source_layout_policy, board_sampling_policy)
    _validate_layer_generation_order(layer_generation_order, source_layout_policy)
    if not (Path(work)/'state.json').exists() and not (Path(work)/'initialization.json').exists():
        _validate_foreground_component_policy(foreground_component_policy, layer_generation_order)
    elif foreground_component_policy not in (None, FOREGROUND_COMPONENT_POLICY):
        raise ValueError('Unsupported foreground component policy.')
    if not (Path(work)/'state.json').exists() and not (Path(work)/'initialization.json').exists():
        _validate_keyed_material_policy(keyed_material_policy, layer_generation_order, foreground_component_policy)
    elif keyed_material_policy not in (None, KEYED_MATERIAL_POLICY):
        raise ValueError('Unsupported keyed material policy.')
    if source_board_reference_policy not in (None, DIRECT_SOURCE_BOARD_POLICY):
        raise ValueError('Unsupported source-board reference policy.')
    if primary_source_reference_policy not in (None, TITLE_REFERENCE_POLICY):
        raise ValueError('Unsupported primary-source reference policy.')
    work=Path(work).resolve()
    if not (work/'state.json').exists() and not (work/'initialization.json').exists():
        _validate_source_board_policy(source_board_reference_policy, source_layout_policy)
        _validate_primary_source_policy(primary_source_reference_policy, source_layout_policy)
    if recovery_policy is not None and not (work/'state.json').exists() and not (work/'initialization.json').exists():
        recovery.require_dependencies(execution_policy,delivery_policy)
    if post_return_prompt_policy is not None and not (work/'state.json').exists() and not (work/'initialization.json').exists() and execution_policy!=execution.MODE:
        raise ValueError('The post-return prompt policy requires an explicit execution policy.')
    if delivery_policy is not None and not (work/'state.json').exists() and not (work/'initialization.json').exists() and execution_policy!=execution.MODE:
        raise ValueError('The delivery policy requires an explicit execution policy.')
    initial_identity=None
    if execution_policy==execution.MODE and not (work/'state.json').exists():
        if work.is_relative_to(ROOT):raise ValueError('Keep the job directory outside the runtime program directory.')
        initial_identity=version.runtime_identity(expected_package)
    elif expected_package is not None:
        initial_identity=version.runtime_identity(expected_package)
    if not (work/'state.json').exists() and source is None and not (work/'initialization.json').exists():
        raise ValueError('A new job requires the current user image; no example is selected.')
    with job_lock(work):
        if (work/'state.json').exists():
            state=read_state(work)
            if execution_policy is not None and state.get('execution_policy')!=execution_policy:
                raise ValueError('A saved job cannot change execution policy or be silently migrated.')
            execution.frozen_prompt_policy(work,state,post_return_prompt_policy)
            binding.frozen_policy(work,state,delivery_policy)
            recovery.frozen_policy(work,state,recovery_policy)
            for key, requested in [('source_layout_policy', source_layout_policy),
                                   ('board_sampling_policy', board_sampling_policy),
                                   ('source_board_reference_policy', source_board_reference_policy),
                                   ('primary_source_reference_policy', primary_source_reference_policy),
                                   ('layer_generation_order', layer_generation_order),
                                   ('foreground_component_policy', foreground_component_policy),
                                   ('keyed_material_policy', keyed_material_policy)]:
                if requested is not None and state.get(key) != requested:
                    raise ValueError('A saved job cannot change ' + key.replace('_', ' ') + ' or be silently migrated.')
            _source_policy_integrity(work, state)
            if execution.enabled(state):
                if version.runtime_identity(expected_package)!=state.get('runtime_identity'):
                    raise ValueError('The runtime identity changed; use this job with its original verified package.')
                execution.integrity(work,state)
            _material_integrity(state)
            if source is not None and pixel_identity(source)!=state['source']['pixel_sha256']:
                raise ValueError('This image differs from the job source. Use a new empty directory.')
            if state.get('done'):return _response(state,_done(state))
            _integrity(work,state)
        else:
            initialization=work/'initialization.json'
            if initialization.exists():
                marker=json.loads(initialization.read_text(encoding='utf-8'))
                allowed={'.lock','initialization.json','input','briefs'}
                if marker.get('format')!='newpath.start/1' or any(p.name not in allowed for p in work.iterdir()):
                    raise ValueError('The interrupted initialization directory cannot be safely resumed.')
                if source is not None and pixel_identity(source)!=marker['pixel_sha256']:
                    raise ValueError('This image differs from the interrupted job source.')
                snapshot=relative_file(work,marker['snapshot'])
                source=snapshot if snapshot.exists() else Path(marker['input_path'])
                if sha256(source)!=marker['byte_sha256']:
                    raise ValueError('The initialization source is missing or changed.')
                marker_policy=marker.get('execution_policy')
                if marker_policy not in (None,execution.MODE):raise ValueError('Unsupported initialization execution policy.')
                if execution_policy is not None and execution_policy!=marker_policy:
                    raise ValueError('An interrupted initialization cannot change execution policy.')
                execution_policy=marker_policy
                marker_prompt_policy=execution.validate_prompt_policy(marker.get('post_return_prompt_policy'))
                if post_return_prompt_policy is not None and post_return_prompt_policy!=marker_prompt_policy:
                    raise ValueError('An interrupted initialization cannot change post-return prompt policy.')
                post_return_prompt_policy=marker_prompt_policy
                marker_delivery_policy=binding.validate_policy(marker.get('delivery_policy'))
                if delivery_policy is not None and delivery_policy!=marker_delivery_policy:
                    raise ValueError('An interrupted initialization cannot change delivery policy.')
                delivery_policy=marker_delivery_policy
                marker_recovery_policy=recovery.validate_policy(marker.get('recovery_policy'))
                if recovery_policy is not None and recovery_policy!=marker_recovery_policy:
                    raise ValueError('An interrupted initialization cannot change recovery policy.')
                recovery_policy=marker_recovery_policy
                marker_source_policy = marker.get('source_layout_policy')
                marker_sampling_policy = marker.get('board_sampling_policy')
                _validate_source_policies(marker_source_policy, marker_sampling_policy)
                for key, requested, saved in [
                    ('source_layout_policy', source_layout_policy, marker_source_policy),
                    ('board_sampling_policy', board_sampling_policy, marker_sampling_policy),
                ]:
                    if requested is not None and requested != saved:
                        raise ValueError('An interrupted initialization cannot change ' + key.replace('_', ' ') + '.')
                source_layout_policy = marker_source_policy
                board_sampling_policy = marker_sampling_policy
                marker_direct_policy = marker.get('source_board_reference_policy')
                _validate_source_board_policy(marker_direct_policy, source_layout_policy)
                if source_board_reference_policy is not None and source_board_reference_policy != marker_direct_policy:
                    raise ValueError('An interrupted initialization cannot change source-board reference policy.')
                source_board_reference_policy = marker_direct_policy
                marker_primary_policy = marker.get('primary_source_reference_policy')
                _validate_primary_source_policy(marker_primary_policy, source_layout_policy)
                if primary_source_reference_policy is not None and primary_source_reference_policy != marker_primary_policy:
                    raise ValueError('An interrupted initialization cannot change primary-source reference policy.')
                primary_source_reference_policy = marker_primary_policy
                marker_order = marker.get('layer_generation_order')
                _validate_layer_generation_order(marker_order, source_layout_policy)
                if layer_generation_order is not None and layer_generation_order != marker_order:
                    raise ValueError('An interrupted initialization cannot change layer generation order.')
                layer_generation_order = marker_order
                saved_components = marker.get('foreground_component_policy')
                _validate_foreground_component_policy(saved_components, marker_order)
                if foreground_component_policy is not None and foreground_component_policy != saved_components:
                    raise ValueError('An interrupted initialization cannot change foreground component policy.')
                foreground_component_policy = saved_components
                saved_keyed = marker.get('keyed_material_policy')
                _validate_keyed_material_policy(saved_keyed, marker_order, saved_components)
                if keyed_material_policy is not None and keyed_material_policy != saved_keyed:
                    raise ValueError('An interrupted initialization cannot change keyed material policy.')
                keyed_material_policy = saved_keyed
                if recovery_policy is not None:recovery.require_dependencies(execution_policy,delivery_policy)
                if delivery_policy is not None and execution_policy!=execution.MODE:
                    raise ValueError('The delivery policy requires explicit execution records.')
                if post_return_prompt_policy is not None and execution_policy!=execution.MODE:
                    raise ValueError('The post-return prompt policy requires explicit execution records.')
                if execution_policy==execution.MODE:
                    if work.is_relative_to(ROOT):raise ValueError('Keep the job directory outside the runtime program directory.')
                    initial_identity=version.runtime_identity(expected_package)
                    if initial_identity!=marker.get('runtime_identity'):
                        raise ValueError('The initialization runtime identity changed.')
            else:
                if any(p.name!='.lock' for p in work.iterdir()):raise ValueError('A new job needs an empty directory.')
                source=Path(source).resolve()
                byte_hash=sha256(source);pixel_hash=pixel_identity(source)
                if sha256(source)!=byte_hash:raise ValueError('The source changed while initializing.')
                marker={'format':'newpath.start/1','input_path':str(source),
                    'snapshot':'input/original'+(source.suffix.lower() or '.bin'),
                    'created_at_utc':datetime.now(timezone.utc).isoformat(),
                    'byte_sha256':byte_hash,'pixel_sha256':pixel_hash}
                if execution_policy==execution.MODE:
                    marker.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(initial_identity))
                    marker['decorative_background_policy'] = BACKGROUND_CONTAINER_POLICY_ID
                if post_return_prompt_policy is not None:
                    marker['post_return_prompt_policy']=post_return_prompt_policy
                if delivery_policy is not None:marker['delivery_policy']=delivery_policy
                if recovery_policy is not None:marker['recovery_policy']=recovery_policy
                if source_layout_policy is not None:
                    marker['source_layout_policy'] = source_layout_policy
                if source_layout_policy == SOURCE_LAYOUT_POLICY:
                    marker['source_layer_scope_policy'] = SOURCE_LAYER_SCOPE_POLICY
                    marker['board_reference_policy'] = BOARD_REFERENCE_POLICY
                if board_sampling_policy is not None:
                    marker['board_sampling_policy'] = board_sampling_policy
                if source_board_reference_policy is not None:
                    marker['source_board_reference_policy'] = source_board_reference_policy
                if primary_source_reference_policy is not None:
                    marker['primary_source_reference_policy'] = primary_source_reference_policy
                if layer_generation_order is not None:
                    marker['layer_generation_order'] = layer_generation_order
                if foreground_component_policy is not None:
                    marker['foreground_component_policy'] = foreground_component_policy
                if keyed_material_policy is not None:
                    marker['keyed_material_policy'] = keyed_material_policy
                write_json(initialization,marker)
            scope_policy = marker.get('source_layer_scope_policy')
            if (scope_policy not in (None, SOURCE_LAYER_SCOPE_POLICY)
                    or (scope_policy is not None and source_layout_policy != SOURCE_LAYOUT_POLICY)):
                raise ValueError('Unsupported initialization source layer scope policy.')
            reference_policy = marker.get('board_reference_policy')
            if (reference_policy not in (None, BOARD_REFERENCE_POLICY)
                    or (reference_policy is not None and scope_policy != SOURCE_LAYER_SCOPE_POLICY)):
                raise ValueError('Unsupported initialization board reference policy.')
            _runtime()
            src=prepare_source(source,work)
            if src['byte_sha256']!=marker['byte_sha256'] or src['pixel_sha256']!=marker['pixel_sha256']:
                raise ValueError('The source changed after initialization.')
            src['input_path']=marker['input_path']
            if source_layout_policy == SOURCE_LAYOUT_POLICY:
                src['reading_images'] = prepare_reading_images(work, src)
            state={'format':'newpath.job/1','phase':'layers','source':src,'tasks':{},'layout':None,
                'generation_policy':GENERATION_POLICY,
                'material_background_mode':MATERIAL_MODE,
                'assets':[],'warnings':[],'processing_seconds':[],'active_batch':[],
                'started_at_utc':marker.get('created_at_utc',datetime.now(timezone.utc).isoformat()),'events':[]}
            if execution_policy==execution.MODE:
                state.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(initial_identity))
            container_policy = marker.get('decorative_background_policy')
            if container_policy not in (None, BACKGROUND_CONTAINER_POLICY_ID):
                raise ValueError('Unsupported initialization decorative background policy')
            if container_policy:
                state['decorative_background_policy'] = container_policy
            if post_return_prompt_policy is not None:
                state['post_return_prompt_policy']=post_return_prompt_policy
            if delivery_policy is not None:state['delivery_policy']=delivery_policy
            if recovery_policy is not None:
                state.update(recovery_policy=recovery_policy,recovery=recovery.initial_budget(recovery_policy))
            if source_layout_policy is not None:
                state['source_layout_policy'] = source_layout_policy
                state['phase'] = 'describe'
            if marker.get('source_layer_scope_policy') is not None:
                state['source_layer_scope_policy'] = marker['source_layer_scope_policy']
            if marker.get('board_reference_policy') is not None:
                state['board_reference_policy'] = marker['board_reference_policy']
            if board_sampling_policy is not None:
                state['board_sampling_policy'] = board_sampling_policy
            if source_board_reference_policy is not None:
                state['source_board_reference_policy'] = source_board_reference_policy
            if primary_source_reference_policy is not None:
                state['primary_source_reference_policy'] = primary_source_reference_policy
            if layer_generation_order is not None:
                state['layer_generation_order'] = layer_generation_order
            if foreground_component_policy is not None:
                state['foreground_component_policy'] = foreground_component_policy
            if keyed_material_policy is not None:
                state['keyed_material_policy'] = keyed_material_policy
            if source_layout_policy is None:
                _create_layer_tasks(work, state)
            save_state(work,state,'source_prepared')
        if retry_task is not None:
            if execution.enabled(state) and state.get('execution_hold'):
                raise ValueError('Remaining generation is paused by the user; resume_unstarted before a retry.')
            task=state['tasks'].get(retry_task)
            if not task or task['status']=='recorded':raise ValueError('Only an unfinished task can be explicitly reopened.')
            if task['role'] in {'board','wordart'} and task['id'] not in state['active_batch']:
                raise ValueError('Retry only an unfinished member of the active batch.')
            if _strict_generation(state):
                if task['status']!='failed':
                    raise ValueError('Only a failed task may be retried; an issued waiting task must be awaited or registered.')
                authorization=_authorization(retry_authorization,'retry_authorization')
                if any(identity!=task['id'] for identity in _pending_generation_violations(state)):
                    raise ValueError('Resolve the other task generation violation before authorizing a new invocation.')
                task.setdefault('retry_authorizations',[]).append({'attempt':task['attempt']+1,
                    'authorization':authorization,'provenance':'caller_reported_user_request_not_verified'})
                _acknowledge_violations(task,authorization,'explicit_user_authorized_retry')
            task.update(status='waiting',issued=True,attempt=task['attempt']+1)
            if execution.enabled(state):execution.reset_for_retry(task)
            task.pop('failure',None)
            task.pop('failure_kind',None)
            task.pop('registration',None)
            save_state(work,state,'explicit_retry')
            return _response(state,{'kind':'generate','jobs':[_public_task(work,task)],'instructions':'Explicitly reopened task only; keep every completed result.'})
        if data is not None:
            immediate=_submit(work,state,data)
            if immediate is not None:return _response(state,immediate)
        return _response(state,_drive(work,state))
