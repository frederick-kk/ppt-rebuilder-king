"""Synthetic fixed returns, expressly not a real image-service success case."""
from pathlib import Path
import sys

sys.dont_write_bytecode = True
PACKAGE = Path(__file__).resolve().parents[2]
RUNTIME = PACKAGE / 'compat-runtime'


def make_job(root, failure=None):
    from PIL import Image, ImageDraw
    sys.path.insert(0, str(RUNTIME))
    from newpath.controller import run
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    source = root / 'source.png'
    picture = Image.new('RGB', (1920, 1080), '#E4EEF8')
    ImageDraw.Draw(picture).rectangle((120, 180, 359, 339), fill='#104AA0')
    picture.save(source)
    background = root / 'background.png'
    Image.new('RGB', (1920, 1080), '#E4EEF8').save(background)
    foreground = root / 'foreground.png'
    # New jobs require a genuine alpha return. This program-made fixture is
    # still synthetic replay evidence, never a real image-service success.
    picture = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(picture).rectangle((120, 180, 359, 339), fill='#104AA0')
    picture.save(foreground)
    work = root / 'job'
    response = run(work, source=source)
    for task in response['jobs']:
        if task['role'] == 'background' and failure:
            if failure in ('unreadable', 'unreadable_same_attempt'):
                bad = root / 'bad.png'; bad.write_bytes(b'invalid PNG')
                try:
                    run(work, data={'task_id': task['id'], 'role': task['role'], 'attempt': task['attempt'], 'file': str(bad)})
                except ValueError:
                    pass
                else: raise AssertionError('Unreadable synthetic return was accepted')
            else:
                run(work, data={'task_id': task['id'], 'role': task['role'], 'attempt': task['attempt'],
                                'outcome': 'failed', 'reason': 'Synthetic recorded service failure'})
            if not failure.endswith('_same_attempt'):
                retried = run(work, retry_task=task['id'], retry_authorization='Synthetic user: retry the recorded failed task once.')
                task = retried['jobs'][0]
        response = run(work, data={'task_id': task['id'], 'role': task['role'], 'attempt': task['attempt'],
                                   'file': str(background if task['role'] == 'background' else foreground)})
    layout = {'items': [
        {'id': 'body', 'kind': 'text', 'text': 'Synthetic ABC', 'box': [420, 170, 900, 80], 'size': 44},
        {'id': 'art-blue', 'kind': 'art', 'box': [120, 180, 240, 160], 'description': 'solid blue rectangle'},
        {'id': 'shape', 'kind': 'shape', 'shape': 'roundRect', 'box': [450, 350, 200, 100], 'fill': 'AADDFF'},
        {'id': 'equation', 'kind': 'formula', 'text': r'x^2+1', 'box': [700, 350, 400, 100], 'size': 36},
    ]}
    raw_layout = root / 'original-layout.json'
    import json
    raw_layout.write_text(json.dumps(layout), encoding='utf-8')
    response = run(work, data={'kind': 'layout', 'layout': layout})
    while response['kind'] == 'generate':
        jobs = list(response['jobs'])
        for task in jobs:
            # Independent synthetic output, not a copied input/reference. This
            # tests queue/replay mechanics, never real component repaint quality.
            picture = Image.new('RGBA', tuple(task['size']), (0, 0, 0, 0))
            draw = ImageDraw.Draw(picture)
            from newpath.storage import read_state
            board = read_state(work)['tasks'][task['id']]['board']
            for member in board['members']:
                x, y, width, height = member['slot']
                inset = max(10, int(min(width, height) * .2))
                draw.rectangle((x + inset, y + inset, x + width - inset - 1,
                                y + height - inset - 1), fill='#205BB2')
            returned = root / (task['id'] + '-synthetic-output.png')
            picture.save(returned)
            response = run(work, data={'task_id': task['id'], 'role': task['role'], 'attempt': task['attempt'],
                                       'file': str(returned)})
    if response['kind'] != 'done': raise AssertionError(response)
    return work, raw_layout
