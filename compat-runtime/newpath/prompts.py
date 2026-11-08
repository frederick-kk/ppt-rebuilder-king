"""Task briefs authored for this implementation's stage contracts."""
import json
from .native import MODE as MATERIAL_MODE


EMBEDDED_IMAGE_POLICY = (
    'Standalone embedded photos, screenshots, scans, charts and collages are independent foreground image assets. '
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
    'Unframed scenery and subjects naturally blended into it stay '
    'together in the background, including people, vehicles, buildings and real branches or flowers. '
    'Separate page elements belong to the foreground: artistic headings, paper or card panels, '
    'brush-stroke labels, icons and movable small illustrations. Ordinary page text outside embedded images and simple '
    'layout geometry are native objects: absent from the background, present in the foreground '
    'reference. Do not isolate or duplicate a scene subject that stays in the background. '
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


def layer_brief(role, transform, *, background_mode=None):
    if background_mode not in (None,MATERIAL_MODE):
        raise ValueError('Unknown material background mode')
    geometry = json.dumps({'working_pixels':[1920,1080], 'content_offset':transform['offset'],
                           'content_size':transform['resized']}, separators=(',', ':'))
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


def board_brief(board):
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
    return result


def wordart_brief(member):
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
