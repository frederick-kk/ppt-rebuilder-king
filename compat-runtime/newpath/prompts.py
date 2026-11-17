"""Task briefs authored for this implementation's stage contracts."""
import copy
import json
from .source import TITLE_REFERENCE_POLICY
from .native import MODE as MATERIAL_MODE

BACKGROUND_CONTAINER_POLICY_ID = 'decorative-background-containers/1'
SOURCE_LAYER_SCOPE_POLICY = 'source-first-board-reference/1'
BOARD_REFERENCE_POLICY = 'embedded-own-source-box/1'
SOURCE_BOARD_REFERENCE_POLICY = 'direct-source-board-reference/1'


EMBEDDED_IMAGE_POLICY = (
    'Classify visual regions by their role in the source layout. Photographic or illustrated scenery '
    'forming a continuous composition with page-edge borders, sweeping ribbons, arches or landscape '
    'decoration stays together in the background, including its people, objects and image-internal writing. '
    'Separate content photos, screenshots, scans, charts and collages are independent foreground image assets. '
    'Use their content-panel, caption or standalone image role to identify them at any page location. '
    'Preserve each as one complete rectangular image with its full rectangular boundaries, original crop '
    'and all image contents, even when borderless or large. Image-internal text, logos, people and '
    'buildings stay inside that image. Keep them within the image when describing, generating and '
    'assembling it. Never turn them into separate native text, artistic headings or cutout subjects. '
    'Preserve the image\'s original opacity, contrast and real white areas. Add no fog, haze, edge fade '
    'or feathered blend. The background excludes these entire images. Scenery and subjects naturally '
    'integrated into the page background stay together in that background. '
)


_LAYER_POLICY = (
    'Divide the slide by layout role. ' + EMBEDDED_IMAGE_POLICY +
    'Background scenery and subjects naturally blended into it stay '
    'together in the background, including people, vehicles, buildings and real branches or flowers. '
    'Separate page elements belong to the foreground: artistic headings, paper or card panels, '
    'brush-stroke labels, icons and movable small illustrations. Ordinary page text outside embedded images and simple '
    'layout geometry are native objects: absent from the background, present in the foreground '
    'reference. Do not isolate or duplicate a scene subject that stays in the background. '
    'Read ordinary page text and line positions from the unchanged source during page description. '
    'Layer-role review checks the required subjects and their assigned layer. Preserve minor small-text '
    'and edge differences in observations for source-based assembly. Missing independent content images '
    'or subjects placed in the wrong layer remain role failures. '
)

_SUBJECT_EDGES = (
    'Follow the reference material edges. Add no white sticker outline or green fringe or glow. '
    'White strokes or borders already present in the source artwork, including outlined artistic '
    'lettering, belong to the subject and must remain; do not remove white indiscriminately. '
)


_NATIVE_ALPHA = (
    'Transparency must be a real PNG alpha channel, never a painted checkerboard, white rectangle '
    'or solid colour field. Empty space must have alpha 0. Keep genuine paper/card material, '
    'painted areas, white writing, original outlines and all real subject colours intact. '
    'Preserve source translucency and narrow anti-aliased edges; do not fade opaque artwork, '
    'add haze or glow, or simulate transparency by fading the writing. '
)


_DECORATIVE_CONTAINER_POLICY = (
    'Separate fixed decorative containers from their movable contents. ' + EMBEDDED_IMAGE_POLICY +
    'Empty decorative paper bases, complex gold frames, ornamental card outlines and cloud ornaments '
    'remain in the background at their source positions, with their original texture and contours. '
    'Ordinary page text, artistic writing, independent content images, icons and brush-stroke title '
    'labels are separate foreground content. Treat artistic writing and brush-stroke title labels as '
    'complete foreground groups. Keep connected or immediately adjacent arcs, painted brushstrokes and '
    'their own backing together with the writing when they belong to the source writing group, '
    'including disconnected strokes within that group. Apply this grouping to page writing outside '
    'independent content images. Independent corner or cloud ornaments, page-edge ribbons and '
    'integrated scenery retain their background assignment. Flat simple geometry and regular tables remain native: '
    'omit them from the background and retain their reference appearance in the foreground. '
    'Read text, geometry and identities from the unchanged source during the existing page description. '
    'Layer-role review checks the assigned subjects and completeness. Record minor small-text and '
    'edge differences in observations. Missing independent content images or subjects in the wrong '
    'layer remain role failures. '
)


_COMPONENT_SUBJECT_OWNERSHIP = (
    'Each cutout member contains the complete source subject and its own intrinsic material. '
    'Keep a badge\'s own original backplate and a writing group\'s own brush backing, arcs and detached '
    'strokes. Exclude surrounding page paper, fixed card containers, gold frames and fragments of a '
    'neighbouring red label from an independent icon or other cutout. Preserve complete rectangular '
    'photos, screenshots and scans, including their opaque interiors, real white areas and internal '
    'writing. Apply material preservation to the assigned member itself. Do not erase white or other '
    'colours globally. '
)


_FOREGROUND_COMPONENT_REFERENCE = (
    'Use this foreground as the actual component-sampling reference for the later component board. '
    'Exclude integrated background scenes and their people, objects and ornaments. Keep fixed empty '
    'paper bases, gold frames and card containers assigned to background absent from this foreground. '
    'Ordinary native text remains a reference at its original position, without adding a paper or card '
    'underlay beneath it. Retain all assigned artistic writing as complete foreground groups, including '
    'its own strokes, arcs and brush backing. Cleared areas use the empty field specified above. '
    + _COMPONENT_SUBJECT_OWNERSHIP
)


def _decorative_layer_brief(role, geometry, background_mode):
    if role == 'background':
        return (_DECORATIVE_CONTAINER_POLICY +
            'Background task: Keep the integrated scene and the empty decorative paper bases, gold frames, '
            'card outlines and cloud ornaments at their original positions and sizes. Preserve their '
            'texture, linework, colours, contours and overlaps. Remove all ordinary page text, independent '
            'content images, icons, and the complete artistic writing and brush-stroke title labels '
            'together with their associated arcs, strokes and backing. Remove simple '
            'native geometry and table content as well. Restore the empty paper or scene beneath those '
            'removed contents while retaining each decorative container. Outer fitting margins stay '
            'empty white. Return one opaque PNG covering the full working rectangle. Geometry: ' + geometry)
    result = (_DECORATIVE_CONTAINER_POLICY +
        'Foreground task: Keep ordinary page text, independent content images, icons, and complete '
        'artistic writing and brush-stroke title labels including their associated arcs, strokes and '
        'backing at their original positions and sizes, on uniform RGB(255,0,255). '
        'Retain the reference appearance of simple native geometry and regular tables. Preserve the '
        'writing, colours, original outlines and complete image contents of these assigned elements. '
        'Omit the background scene and all decorative containers assigned to background. Their regions and the outer fitting '
        'margins must be solid magenta. ' + _SUBJECT_EDGES +
        'Add no objects or checkerboard, and use no transparent field. Return '
        'one opaque PNG covering the full working rectangle. Geometry: ' + geometry)
    if background_mode == MATERIAL_MODE:
        alpha_rules = _NATIVE_ALPHA.replace('Keep genuine paper/card material, ',
                                            'Keep assigned foreground material, ')
        result = result.replace('on uniform RGB(255,0,255)', 'on a true transparent field')
        result = result.replace('must be solid magenta.', 'must be fully transparent with alpha 0.')
        result = result.replace('Add no objects or checkerboard, and use no transparent field. Return ',
                                'Add no objects or checkerboard. ' + alpha_rules + 'Return ')
        result = result.replace('one opaque PNG', 'one transparent PNG')
    return result


def _background_layer_allocation(allocation):
    """Keep background facts and foreground clearing geometry in a separate view."""
    fields = ('id', 'box', 'presentation', 'embedded_image')
    result = {key: copy.deepcopy(allocation[key]) for key in ('background_regions', 'source_pixel_sha256')
              if key in allocation}
    result['foreground_regions'] = [
        {key: copy.deepcopy(region[key]) for key in fields if key in region}
        for region in allocation['foreground_regions']]
    return result


def _scoped_layer_brief(role, transform, background_mode, decorative_background, allocation, scope):
    if role not in ('background', 'foreground') or not isinstance(allocation, dict) or not isinstance(scope, dict):
        raise ValueError('A source layer scope requires a layer role and frozen allocation objects')
    if scope.get('policy') != SOURCE_LAYER_SCOPE_POLICY:
        raise ValueError('Unsupported source layer scope')
    members = scope.get('board_member_ids')
    primary = scope.get('primary_wordart_id')
    regions = {region['id']: region for region in allocation['foreground_regions']}
    if (not isinstance(members, list) or any(not isinstance(member, str) for member in members)
            or len(members) != len(set(members)) or (primary is not None and primary not in regions)
            or set(members) != set(regions) - {primary}):
        raise ValueError('The source layer scope must match the complete frozen artwork ownership')
    if background_mode not in (None, MATERIAL_MODE):
        raise ValueError('Unknown material background mode')
    geometry = json.dumps({'working_pixels': [1920, 1080], 'content_offset': transform['offset'],
                           'content_size': transform['resized']}, separators=(',', ':'))
    if role == 'background':
        containers = (
            'Keep all levels of empty paper bases, gold frames, card contours, ornaments and dividers '
            'not assigned to foreground artwork, at their source positions, proportions and contours. '
            if decorative_background else
            'Remove the paper or card panels assigned to foreground artwork together with their contents. ')
        goal = (
            'Background task: Generate only the empty background layer. '
            'Remove ordinary page text and independent content photos, screenshots, scans, charts and '
            'collages as complete rectangles, including every internal subject and all image-internal writing. '
            'Remove every foreground_regions item as a complete group, including the standalone primary '
            'title and its arcs, strokes and backing. Remove entire red plaques, brush grounds and badge '
            'backplates assigned to artwork even when the lettering is removed; these are not empty '
            'decorative containers. Keep every background_regions scene, including its people, objects, '
            'intrinsic image-internal writing and fixed brand marks, at its source position and proportions. '
            + containers +
            'For background-assigned empty containers and native geometry, the background_regions assignment takes precedence. Preserve background-assigned empty icon backplates, '
            'containers, frame lines and dividers, including flat or simple pieces. Remove simple native geometry, '
            'connections, arrows and table content only when not assigned to background_regions. '
            'Restore the assigned scene or empty container beneath all cleared content. '
            'Outer fitting margins stay empty white. Return one opaque PNG covering the full working rectangle. ')
        payload = {'scene_allocation': _background_layer_allocation(allocation), 'source_layer_scope': scope}
        return (goal + 'All box values are [x,y,width,height] in 1920x1080 working pixels. Geometry: ' + geometry +
                '\nFrozen source layer allocation and component-reference scope follow.\n' +
                json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    else:
        goal = (
            'Foreground task: Produce a component-layout reference for only board_member_ids, at their '
            'frozen source positions and proportions. Keep each required subject, its original edges '
            'and complete backing. Preserve complete rectangular images including their image-internal '
            'writing, people, logos and all other image contents. Keep paper or card panels only when '
            'assigned to these members. Do not generate the standalone primary title, ordinary page text, '
            'simple native geometry, connections, arrows or table content outside embedded images. '
            'Clear every background_regions scene including its people and objects, and every decorative '
            'container assigned to background. Source lettering and final material appearance come from '
            'the frozen layout and unchanged full source in later artwork generation. Record reference '
            'glyph and edge differences in observed; glyph differences alone do not require another '
            'foreground generation. Missing or wrong subjects, or severe displacement or scene-subject '
            'contamination making a required source ROI unusable, remain role failures. ' + _SUBJECT_EDGES)
        if background_mode == MATERIAL_MODE:
            goal += ('Use a true transparent field and fully transparent outer fitting margins. ' +
                     _NATIVE_ALPHA.replace('Keep genuine paper/card material, ', 'Keep assigned foreground material, ') +
                     'Return one transparent PNG covering the full working rectangle. ')
        else:
            goal += ('Use uniform RGB(255,0,255) for every cleared area and outer fitting margin. '
                     'Add no objects or checkerboard and use no transparent field. '
                     'Return one opaque PNG covering the full working rectangle. ')
    payload = {'scene_allocation': allocation, 'source_layer_scope': scope}
    return (EMBEDDED_IMAGE_POLICY + goal + 'Geometry: ' + geometry +
            '\nFrozen source layer allocation and component-reference scope follow.\n' +
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def layer_brief(role, transform, *, background_mode=None, decorative_background=False,
                scene_allocation=None, source_layer_scope=None, foreground_components_only=False):
    if foreground_components_only and role == 'foreground':
        base = layer_brief(role, transform, background_mode=background_mode,
                           decorative_background=decorative_background,
                           scene_allocation=scene_allocation, source_layer_scope=source_layer_scope)
        return base + '\n\n' + _FOREGROUND_COMPONENT_REFERENCE
    if source_layer_scope is not None:
        return _scoped_layer_brief(role, transform, background_mode, decorative_background,
                                   scene_allocation, source_layer_scope)
    if scene_allocation is not None:
        if role not in ('background','foreground'):
            raise ValueError('A frozen scene allocation applies only to background and foreground tasks')
        if not isinstance(scene_allocation,dict):
            raise ValueError('A frozen scene allocation must be an object')
        base=layer_brief(role,transform,background_mode=background_mode,
                         decorative_background=decorative_background)
        if role=='background':
            assignment=('Keep every background_regions scene in the background, including its people, '
                'objects and image-internal writing. Remove every foreground_regions item. '
                'Remove embedded images as complete rectangles together with all their image-internal '
                'content. Restore the underlying scene or empty decorative container in those cleared areas. ')
        else:
            assignment=('Retain every foreground_regions item according to its presentation, including '
                'embedded images as complete rectangles with all their image-internal content. '
                'Clear every background_regions scene from the foreground, including its people, objects '
                'and image-internal writing. Keep those cleared areas in the background field required '
                'by this task. Retain ordinary page text, native geometry and table reference appearance '
                'under the existing rules. ')
        return (base+'\n\nApply these frozen source-region roles in working-image pixels [x,y,width,height]. '+
            assignment+'Use this same allocation for both layers without deciding their roles again. '
            'Actual role observation applies to each generated return. '
            'Frozen source layer allocation follows.\n'+
            json.dumps(scene_allocation,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    if background_mode not in (None,MATERIAL_MODE):
        raise ValueError('Unknown material background mode')
    geometry = json.dumps({'working_pixels':[1920,1080], 'content_offset':transform['offset'],
                           'content_size':transform['resized']}, separators=(',', ':'))
    if decorative_background:
        return _decorative_layer_brief(role, geometry, background_mode)
    if role == 'background':
        return (_LAYER_POLICY + 'Background task: Keep the scene and its integrated subjects at their '
            'original positions, with the same atmosphere, colours and surface detail. Remove only the '
            'foreground page elements, complete embedded images and native content defined above. Fill the areas they covered '
            'from the surrounding scene without removing its people, vehicles, architecture or plants. '
            'The people and buildings inside removed embedded images leave with those images. '
            'Preserve the composition. Outer fitting margins stay empty white. Return one opaque PNG '
            'covering the full working rectangle. Geometry: ' + geometry)
    result=(_LAYER_POLICY + 'Foreground task: Keep only the separate page elements and native content '
        'defined above, at their original positions and sizes, on uniform RGB(255,0,255). Preserve the '
        'writing and details of those retained items. Omit the scenery and every integrated subject assigned to '
        'the background; do not extract them a second time. Their areas and the outer fitting margins '
        'must be solid magenta. ' + _SUBJECT_EDGES +
        'Add no objects or checkerboard, and use no transparent field. Return '
        'one opaque PNG covering the full working rectangle. Geometry: ' + geometry)
    if background_mode==MATERIAL_MODE:
        result=result.replace('on uniform RGB(255,0,255)', 'on a true transparent field')
        result=result.replace('must be solid magenta.', 'must be fully transparent with alpha 0.')
        result=result.replace('Add no objects or checkerboard, and use no transparent field. Return ',
                              'Add no objects or checkerboard. '+_NATIVE_ALPHA+'Return ')
        result=result.replace('one opaque PNG', 'one transparent PNG')
    return result


def keyed_layer_brief(role, transform, key, **kwargs):
    """Request a solid field without changing the existing layer allocation."""
    from .keyed import rgb_for_key
    rgb = rgb_for_key(key)
    options = {**kwargs, 'background_mode': None}
    result = layer_brief(role, transform, **options)
    if role == 'background':
        return result
    selected = 'RGB(' + ','.join(str(value) for value in rgb) + ')'
    return result.replace('RGB(255,0,255)', selected).replace('solid magenta', 'solid ' + key)


def keyed_board_brief(board, key):
    """Change only the generation field on a copy of the native board plan."""
    from .keyed import rgb_for_key
    rgb_for_key(key)
    request_board = copy.deepcopy(board)
    request_board.pop('background_mode', None)
    request_board['key'] = key
    return board_brief(request_board)


def _owned_image_board_brief(board):
    if board['board_reference_policy'] != BOARD_REFERENCE_POLICY:
        raise ValueError('Unsupported board reference policy')
    previous = dict(board)
    previous.pop('board_reference_policy')
    ownership = [{'id': member['id'], 'source_box_pixels': member['box'],
                  'context_exclusions': member.get('context_exclusions', [])}
                 for member in board['members'] if member.get('embedded_image') is True]
    reference_origin = (
        ' Each embedded-image reference contains only its own source-box pixels, with outside '
        'context transparent. ')
    if any(member.get('embedded_image') is True and 'foreground_box' in member
           and 'foreground_crop_pixels' in member for member in board['members']):
        reference_origin = (
            ' An embedded-image member with foreground_crop_pixels retains its complete '
            'generated-foreground sampling window. Other embedded-image members use the '
            'source-box ownership window applied by the program, with outside context transparent. ')
    return (board_brief(previous) + reference_origin +
            'Labels, artistic headings and other separately assigned member IDs '
            'belong only to their own members, even when their fragments appear near or overlap the '
            'image boundary in the source. They are not image-internal writing and must not be '
            'completed or duplicated inside this image. Preserve genuine image-internal text, logos '
            'and people that belong to the image itself. Keep the existing source bounds and '
            'subject footprint; add no caption panel to the image. Reference ownership follows.\n' +
            json.dumps(ownership, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _source_board_brief(board):
    if board['source_board_reference_policy'] != SOURCE_BOARD_REFERENCE_POLICY:
        raise ValueError('Unsupported source-board reference policy')
    previous = dict(board)
    previous.pop('source_board_reference_policy')
    return (
        'Both references are original-source inputs. Image 1 contains program-arranged unchanged-source crops '
        'from each assigned source box; it is not a generated foreground or a finished asset. '
        'Image 2 is the full unchanged working source. Use these real source pixels for each assigned '
        'subject and exact lettering while following image 1\'s allocated cells. The transparent context '
        'outside each source box only separates references. Remove source background within contour '
        'subjects when generating their transparent output; preserve complete rectangular embedded '
        'images and their internal writing. Never register either reference, a crop of it or a '
        're-encoded copy as a newly generated result. Generate one actual new component-board PNG. '
        + board_brief(previous) + '\nOriginal-source reference provenance follows.\n'
        + json.dumps(board['reference_provenance'], ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def board_brief(board):
    if board.get('source_board_reference_policy') is not None:
        return _source_board_brief(board)
    if board.get('board_reference_policy') is not None:
        return _owned_image_board_brief(board)
    mode=board.get('background_mode')
    if mode not in (None,MATERIAL_MODE):
        raise ValueError('Unknown material background mode')
    native=mode==MATERIAL_MODE
    key_rgb={'magenta':'RGB(255,0,255)', 'green':'RGB(0,255,0)',
             'cyan':'RGB(0,255,255)', 'yellow':'RGB(255,255,0)'}[board['key']] if not native else None
    assignments=[]
    for member in board['members']:
        row={'id':member['id'],'slot_pixels':member['slot'],'source_box_pixels':member['box'],
             'clearance_pixels':member.get('inset_px',0),'subject':member['description']}
        if member.get('inset_xy') is not None:
            ix,iy=member['inset_xy']
            x,y,width,height=member['slot']
            row['clearance_pixels']=[ix,iy]
            row['safe_area_pixels']=[x+ix,y+iy,width-2*ix,height-2*iy]
        warnings=[note['code'] for note in board.get('reference_warnings',[])
                  if note.get('id')==member['id']]
        if warnings:row['reference_warnings']=warnings
        if 'source_matte_collision_unresolved' in warnings:
            row['ignore_board_sealed_colour_blocks']=True
        if 'embedded_image' in member:
            row['embedded_image']=member['embedded_image']
        if member.get('embedded_image') is True:
            row['presentation']='rectangle'
            if member.get('lettering'):row['image_internal_writing']=member['lettering']
        elif member.get('lettering'):
            row['required_artistic_writing']=member['lettering']
        if 'foreground_box' in member and 'foreground_crop_pixels' in member:
            row['foreground_box'] = copy.deepcopy(member['foreground_box'])
            row['foreground_crop_pixels'] = copy.deepcopy(member['foreground_crop_pixels'])
            row.setdefault('presentation', member.get('presentation', 'cutout'))
        assignments.append(row)
    reference_roles={'layout_image':1,'appearance_image':2,'add_white_sticker_outline':False,
                     'add_green_fringe':False,'preserve_source_white_edges':True}
    grid_rule = ''
    if board.get('grid'):
        grid = board['grid']
        grid_rule = ('Equal-cell grid: ' + str(grid['rows']) + ' rows by ' + str(grid['columns']) +
            ' columns; cell size [width,height]=' + json.dumps(grid['cell_size']) +
            ' reference-canvas pixels. Outer margin: ' + str(grid['margin']) +
            ' pixels; clear gap between cells: ' + str(grid['gap']) +
            ' pixels. Each member slot_pixels is its complete equal cell [x,y,width,height]. '
            'Keep every cell and subject in its assigned place, matching the reference subject footprint. '
            'Do not enlarge a subject beyond its clearance-defined safe area. Gaps and margins stay '
            'empty in the selected background colour; add no borders, grid lines or numbers. ')
    result=('Use the two attached images for different purposes. Image 1 is the program-made component '
        'board: it controls only cell positions, sizes and the subject arrangement. Image 2 is the '
        'unchanged 1920x1080 working source: use it for the assigned subjects’ actual material, writing, '
        'contours and whether an outline belongs to them. Rebuild only those subjects in image 1’s slots; '
        'never redraw the full source slide or put its scene into a cell. source_box_pixels=[x,y,width,height] '
        'locates the subject in image 2. For source_matte_collision_unresolved assignments, image 1 is '
        'a layout helper with possible matte contamination: use image 2 for material and original edges, '
        'and do not copy sealed matte-coloured blocks from the board into the subject. Canvas coordinates refer to '
        'image 1, starting at its upper-left corner; x increases rightwards and y downwards. Each slot_pixels value is '
        '[x,y,width,height] in reference-canvas pixels, not two corner points or slide coordinates. '
        'Keep the canvas aspect ratio; a different output resolution scales the complete arrangement together. ' +
        grid_rule +
        'Keep every complete slot at its supplied position and size: do not swap, move, shrink or '
        'repack slots, and do not rotate their subjects. Preserve each subject’s contour, material, '
        'details and proportions within that slot. clearance_pixels=[horizontal,vertical] gives the '
        'left/right and top/bottom insets; an integer applies equally to all four sides. When supplied, '
        'safe_area_pixels=[x,y,width,height] is the resulting safe rectangle in reference-canvas '
        'coordinates. Keep the subject inside it. Leave the clearance empty in the '
        'selected background colour. Outside slots, preserve only layout guides already supplied '
        'by image 1; every other area stays empty. Add no new layout marks. Nearby ordinary captions and '
        'other slide fragments are context, not extra objects. Artistic writing explicitly listed below '
        'belongs to its subject: preserve every character and its painted or calligraphic appearance. '
        'Do not draw cell IDs. ' + ('Use a true transparent PNG field outside the subjects; ' if native else
        'Use a uniform opaque ' + key_rgb + ' field outside the subjects; ')+
        'retain fine edges and the appearance of natural shadows. ' + _SUBJECT_EDGES +
        'Return one PNG; follow the arrangement '
        'even if the service chooses different pixel dimensions. Requested pixels: ' + str(board['size']) +
        '. Reference roles: ' + json.dumps(reference_roles,separators=(',', ':')) +
        '. Assignments: ' + json.dumps(assignments,ensure_ascii=False,separators=(',', ':')))
    if native:
        result=result.replace('empty in the selected background colour', 'fully transparent with alpha 0')
        result=result.replace('selected background colour.', 'transparent PNG field with alpha 0.')
        result=result.replace('For source_matte_collision_unresolved assignments, image 1 is '
            'a layout helper with possible matte contamination: use image 2 for material and original edges, '
            'and do not copy sealed matte-coloured blocks from the board into the subject. ', '')
        result += ' '+_NATIVE_ALPHA
    if any(member.get('embedded_image') is True for member in board['members']):
        result += ' '+EMBEDDED_IMAGE_POLICY
    if any('foreground_box' in member and 'foreground_crop_pixels' in member
           for member in board['members']):
        result += (
            ' foreground_box=[x,y,width,height] locates the program-sampled window in the generated '
            '1920x1080 foreground. foreground_crop_pixels=[left,top,right,bottom] records its complete '
            'quantized pixel window. Those pixels have already been arranged into image 1; this '
            'sampling window adds no extra image reference. source_box_pixels continues to locate '
            'the subject in image 2 for source identity, exact lettering and appearance. Use slot_pixels '
            'only for placement on this output board. ' + _COMPONENT_SUBJECT_OWNERSHIP)
        if native:
            result += (
                'Keep every area outside each cutout subject fully transparent with alpha 0, including '
                'space inside its cell. Transparent gaps between cells alone do not establish a '
                'transparent subject. Preserve the member\'s genuine opaque and translucent material. ')
    return result


def _isolated_title_brief(member, source_detail):
    original_reference=('Use the attached unchanged source image to re-create only the single assigned main title '
        'as a separate true transparent PNG. The source_box_pixels locates the complete artistic-writing '
        'group in that image; use the source to ')
    isolated_reference=(
        'Use the single attached source-parts title reference to re-create only the assigned complete '
        'writing group as one separate true transparent PNG. It retains original RGBA pixels inside '
        'the declared source parts; outside their union is transparent. Paper or decorative pixels '
        'may remain inside the retained rectangles. Use these actual source pixels to identify the '
        'lettering, letter shapes and complete writing-to-arc proportions. Preserve the source line '
        'count and horizontal arrangement, including the full own arc, brush backing and detached '
        'strokes. Remove remaining paper and unrelated decoration from the generated subject. '
        'source_box_pixels records working-source coordinates; crop_box and zoom describe the '
        'reference origin and scale. The reference is an input and must not be delivered as a '
        'generated result. Generate only this title, excluding subtitles, neighbouring sections '
        'and the complete slide. Use this single real-source reference to ')
    return (wordart_brief(member).replace(original_reference, isolated_reference, 1) +
        '\n\nSource-parts title reference follows.\n' +
        json.dumps(source_detail, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def wordart_brief(member, *, source_detail=None):
    if source_detail is not None:
        if source_detail.get('reference_policy') is not None:
            if source_detail['reference_policy'] != TITLE_REFERENCE_POLICY:
                raise ValueError('Unsupported primary-source title reference policy')
            return _isolated_title_brief(member, source_detail)
        original_reference=('Use the attached unchanged source image to re-create only the single assigned main title '
            'as a separate true transparent PNG. The source_box_pixels locates the complete artistic-writing '
            'group in that image; use the source to ')
        detail_reference=('Use the two attached unchanged-source images to re-create only the assigned complete writing group '
            'as one separate true transparent PNG. Image 1 is the actual unchanged-source detail crop. '
            'Use its characters, letter shapes, complete writing-to-arc proportions and brushwork as the appearance '
            'reference. Image 2 is the complete unchanged working source and provides source identity and placement. '
            'The source_box_pixels locates the assigned group in image 2. The crop may include nearby ordinary captions '
            'and other page objects. Generate only the assigned complete writing group, including its own arc, '
            'brush backing and detached strokes. Preserve the source line count and horizontal arrangement. '
            'If the source is a single line, keep one line. Read the complete subject proportions from the visible '
            'writing and its own arc in image 1. Use these actual source images to ')
        return (wordart_brief(member).replace(original_reference,detail_reference,1)+
            '\n\nTrue-source detail reference follows.\n'+
            json.dumps(source_detail,ensure_ascii=False,sort_keys=True,separators=(',',':')))
    assignment={'id':member['id'],'source_box_pixels':member['box'],
                'required_artistic_writing':member['lettering'],'appearance':member['description']}
    return ('Use the attached unchanged source image to re-create only the single assigned main title '
        'as a separate true transparent PNG. The source_box_pixels locates the complete artistic-writing '
        'group in that image; use the source to preserve its exact characters, punctuation, layout, '
        'letter shapes, coloured sections, brush tips, dry-brush gaps and genuine original white outlines. '
        'The appearance description below is part of the request. If the assigned group includes a source '
        'emblem, ribbon or painted stroke backing, keep it as part of this one complete title. '
        'Preserve the source subject proportions and arrangement, including all detached strokes and '
        'underlines. Do not reproduce the surrounding scenery, other slide objects, nearby ordinary '
        'captions, a rectangular source crop or the full slide. Do not add new gold bevels, pale highlights, '
        'a white sticker border, coloured fringe, haze, mist, glow or a soft opacity mask. '
        'Keep the main painted ink and genuine opaque title materials at alpha 255. Only a narrow '
        'anti-aliased edge may have partial alpha; every area outside the complete subject must have '
        'alpha 0. Genuine source white writing and white outlines are artwork and stay visible. '
        'Do not fade the writing to simulate transparency. Transparency must be a real alpha channel, '
        'not a drawn checkerboard or any white, magenta or coloured background. '
        'Leave a small fully transparent clearance on all four outer edges so no stroke is clipped. '
        'Return one PNG containing this single complete title. The canvas aspect ratio may differ from '
        'the source; the subject itself must keep the source proportions. Assignment: '+
        json.dumps(assignment,ensure_ascii=False,separators=(',', ':')))
