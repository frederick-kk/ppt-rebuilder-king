"""Task briefs authored for this implementation's stage contracts."""
import json


def layer_brief(role, transform):
    geometry = json.dumps({'working_pixels':[1920,1080], 'content_offset':transform['offset'],
                           'content_size':transform['resized']}, separators=(',', ':'))
    if role == 'background':
        return ('Rebuild only the continuous setting of the attached slide. Use the attachment to determine '
            'its camera, atmosphere, palette and surface detail. Erase lettering and every separate foreground '
            'item: characters, illustrations, photographs, panels, charts, rules, containers and decoration. '
            'Complete the exposed setting naturally, keeping the original composition. Do not turn a card '
            'or simple rectangle into part of the setting. White margins outside the fitted source remain '
            'empty white. Deliver one opaque PNG on the full working rectangle. Geometry: ' + geometry)
    return ('Isolate the complete visible foreground of this attachment onto uniform RGB(255,0,255). '
        'Retain the slide positions, dimensions and spacing of its words, artwork, lines, panels and small marks. '
        'Keep all writing unchanged and readable. Replace the continuous environmental layer and any outer '
        'white fitting margins with the same solid magenta field. Add nothing, rearrange nothing, and do '
        'not use a checkerboard or transparency as the field. Return a single opaque PNG covering the '
        'working rectangle. Geometry: ' + geometry)


def board_brief(board):
    key_rgb={'magenta':'RGB(255,0,255)', 'green':'RGB(0,255,0)',
             'cyan':'RGB(0,255,255)', 'yellow':'RGB(255,255,0)'}[board['key']]
    assignments=[]
    for member in board['members']:
        row={'id':member['id'],'slot_pixels':member['slot'],'subject':member['description']}
        if member.get('lettering'): row['required_artistic_writing']=member['lettering']
        assignments.append(row)
    return ('The attachment arranges independent visual subjects for this reconstruction. Render each '
        'assigned subject in its existing cell with its characteristic contour, material, internal details '
        'and relative proportions. Keep the cell layout and the empty gaps; dark guides belong only in '
        'those gaps. Leave eight percent clearance on each side of a cell. Nearby ordinary captions and '
        'other slide fragments are context, not extra objects. Artistic writing explicitly listed below '
        'belongs to its subject: preserve every character and its painted or calligraphic appearance. '
        'Do not draw cell IDs. Use a uniform opaque ' + key_rgb + ' field outside the subjects; '
        'retain fine edges and the appearance of natural shadows. Return one PNG; follow the arrangement '
        'even if the service chooses different pixel dimensions. Requested pixels: ' + str(board['size']) +
        '. Assignments: ' + json.dumps(assignments,ensure_ascii=False,separators=(',', ':')))
