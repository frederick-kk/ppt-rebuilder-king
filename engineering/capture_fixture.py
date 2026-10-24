"""Explicit offline capture of a stable completed, failed or paused job."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from .common import (SafetyError, inventory, read_json, readonly_job_lock, reject_link_components,
                     require_new_output, safe_relative, sha256, write_json)


TELEMETRY = ('invocation_count', 'tool', 'tool_call_id', 'tool_output_id', 'executed_prompt')
_NO_EXPECTED_HASH = object()
MATERIAL_MODE = 'native-transparent-materials/1'
WORDART_MODE = 'standalone-native-wordart/2'


def _recorded_path(job, path, expected=_NO_EXPECTED_HASH, *, allow_absolute=False):
    if not isinstance(path, str) or not path:
        raise SafetyError('A referenced original path is missing.')
    if expected is not _NO_EXPECTED_HASH and (not isinstance(expected, str) or len(expected) != 64
            or any(character not in '0123456789abcdef' for character in expected)):
        raise SafetyError('A required recorded SHA256 is missing or invalid: ' + str(path))
    if allow_absolute and Path(path).is_absolute():
        absolute = reject_link_components(path).resolve()
        if not absolute.is_relative_to(job):
            raise SafetyError('A recorded artifact escapes the source job.')
    else:
        absolute = safe_relative(job, path)
    if not absolute.is_file():
        raise SafetyError('A referenced original file is missing: ' + str(path))
    if expected is not _NO_EXPECTED_HASH and sha256(absolute) != expected:
        raise SafetyError('A recorded original file changed: ' + str(path))
    return 'job/' + absolute.relative_to(job).as_posix()


def _validate_state(job, state):
    if not isinstance(state, dict) or state.get('format') != 'newpath.job/1' or not isinstance(state.get('tasks'), dict):
        raise SafetyError('Unsupported or incomplete source state.')
    tasks = list(state['tasks'].values())
    if any(not isinstance(task, dict) for task in tasks):
        raise SafetyError('Invalid task record.')
    if not isinstance(state.get('phase'), str) or state['phase'] not in {'layers', 'describe', 'boards', 'done'}:
        raise SafetyError('State phase is not part of the frozen controller format.')
    if state.get('phase') == 'done' and any(task.get('status') != 'recorded' for task in tasks):
        raise SafetyError('Completed state contains an unfinished or failed task.')
    terminal = state['phase'] == 'done' or any(task.get('status') == 'failed' for task in tasks)
    if not terminal:
        raise SafetyError('Capture requires a completed, failed or paused job; active work is refused.')
    source = state.get('source', {})
    for key, checksum in (('original', 'byte_sha256'), ('working', 'working_sha256'), ('guide', 'guide_sha256')):
        if not isinstance(source.get(key), str) or not source.get(checksum):
            raise SafetyError('Source snapshot provenance is incomplete: ' + key)
        _recorded_path(job, source[key], source[checksum])
    layout = None
    if state.get('layout') is not None:
        _recorded_path(job, 'layout.json')
        if read_json(job / 'layout.json') != state['layout']:
            raise SafetyError('Normalized layout differs from controller state.')
        layout = 'job/layout.json'
    elif (job / 'layout.json').exists():
        raise SafetyError('A layout file exists without a matching controller record.')
    for identity, task in state['tasks'].items():
        if not isinstance(task, dict) or task.get('id') != identity or task.get('role') not in {'background', 'foreground', 'board', 'wordart'}:
            raise SafetyError('Invalid task identity or role.')
        if type(task.get('attempt')) is not int or task['attempt'] < 0:
            raise SafetyError('Invalid task attempt number.')
        if task.get('status') not in {'waiting', 'recorded', 'failed'}:
            raise SafetyError('Invalid task status.')
        if task['status'] == 'recorded' and not task.get('result'):
            raise SafetyError('Recorded task has no immutable result record.')
        if task['status'] != 'recorded' and task.get('result'):
            raise SafetyError('Unrecorded task has an inconsistent immutable result.')
        if not task.get('brief_sha256') or not isinstance(task.get('references'), dict):
            raise SafetyError('Task request provenance is incomplete.')
        _recorded_path(job, task.get('brief'), task['brief_sha256'])
        for name, checksum in task['references'].items():
            _recorded_path(job, name, checksum)
        for observation in task.get('attempts', []):
            if type(observation.get('attempt')) is not int or not 0 <= observation['attempt'] <= task['attempt']:
                raise SafetyError('An attempt record is inconsistent.')
            if observation.get('raw'):
                if not observation.get('sha256'):
                    raise SafetyError('Raw attempt hash is missing.')
                _recorded_path(job, observation['raw'], observation['sha256'])
            if observation.get('outcome') == 'recorded':
                immutable = task.get('result')
                if (not isinstance(immutable, dict) or observation['attempt'] != task['attempt']
                        or observation.get('raw') != immutable.get('raw')
                        or observation.get('sha256') != immutable.get('sha256')):
                    raise SafetyError('Recorded attempt contradicts the immutable task result.')
        if task.get('result'):
            result = task['result']
            if not result.get('sha256'):
                raise SafetyError('Result hash is missing.')
            _recorded_path(job, result.get('raw'), result['sha256'])
            if result.get('normalized'):
                if not result.get('normalized_sha256'):
                    raise SafetyError('Normalized result hash is missing.')
                _recorded_path(job, result['normalized'], result.get('normalized_sha256'))
    for asset in state.get('assets', []):
        if not asset.get('sha256'):
            raise SafetyError('Extracted asset hash is missing.')
        _recorded_path(job, asset.get('path'), asset.get('sha256'), allow_absolute=True)
    if state.get('done'):
        if state.get('phase') != 'done' or not state['done'].get('sha256'):
            raise SafetyError('Completion receipt is inconsistent or missing a hash.')
        _recorded_path(job, state['done'].get('pptx'), state['done'].get('sha256'), allow_absolute=True)
    elif state.get('phase') == 'done':
        raise SafetyError('Completed phase has no completion receipt.')
    return layout


def _task_records(job, state):
    result = []
    for task in state['tasks'].values():
        observed = list(task.get('attempts', []))
        current = task.get('result')
        if current and not any(item.get('attempt') == task['attempt'] and item.get('outcome') == 'recorded' for item in observed):
            observed.append({'attempt': task['attempt'], 'outcome': 'recorded', **current})
        if not any(item.get('attempt') == task['attempt'] for item in observed):
            observed.append({'attempt': task['attempt'], 'outcome': 'failed' if task.get('status') == 'failed' else 'unobserved',
                             'reason': task.get('failure')})
        for item in observed:
            if item.get('outcome') not in {'recorded', 'failed', 'unreadable', 'unobserved'}:
                raise SafetyError('An attempt has an unknown recorded outcome.')
            board = task.get('board', {}) if task['role'] == 'board' else {}
            mode = task.get('background_mode', board.get('background_mode'))
            native_material = task['role'] in {'foreground', 'board'} and mode == MATERIAL_MODE
            if task['role'] in {'foreground', 'board'} and mode not in (None, MATERIAL_MODE):
                raise SafetyError('Unknown material background_mode in recorded task.')
            if (task['role'] == 'board' and board.get('background_mode') is not None
                    and board['background_mode'] != mode):
                raise SafetyError('Board and task background_mode disagree.')
            wordart = task['role'] == 'wordart'
            entry = {'id': task['id'], 'role': task['role'], 'attempt': item['attempt'],
                'brief': _recorded_path(job, task['brief']),
                'references': [{'path': _recorded_path(job, name), 'sha256': checksum} for name, checksum in task['references'].items()],
                'size': board.get('size') if task['role'] == 'board' else [1536, 1024] if wordart else [1920, 1080],
                'key': board.get('key') if task['role'] == 'board' else None,
                'requested_outputs': 1, 'transparent_background': native_material or wordart,
                'result': _recorded_path(job, item['raw']) if item.get('raw') else None,
                'outcome': item['outcome'], 'reason': item.get('reason'),
                'parameter_provenance': 'derived_from_current_public_task_contract',
                'actual_request_arguments': None,
                **{key: item.get(key, current.get(key) if current and item['attempt'] == task['attempt'] else None) for key in TELEMETRY}}
            # Explicit modes are request provenance. An omitted mode remains
            # legacy; native records never invent a colour key for replay.
            if native_material: entry['background_mode'] = MATERIAL_MODE
            elif wordart: entry['background_mode'] = WORDART_MODE
            # Capture an existing authorization only; a later attempt alone
            # is not evidence that the user approved another invocation.
            authorization=next((record.get('authorization') for record in task.get('retry_authorizations',[])
                                if record.get('attempt')==item['attempt']),None)
            if authorization is not None:entry['retry_authorization']=authorization
            if task['role'] == 'board':
                if (not isinstance(entry['size'], list) or len(entry['size']) != 2
                        or (entry['key'] is None and not native_material)):
                    raise SafetyError('Board request size/key are missing.')
                if native_material and entry['key'] is not None:
                    raise SafetyError('Native material board cannot record a colour key.')
                entry['members'] = board.get('members')
            result.append(entry)
    return result


def capture_fixture(job, output, *, raw_layout=None, instructions=(), human_review=None, origin='real_job'):
    if origin not in {'real_job', 'synthetic'}:
        raise SafetyError('Origin must be real_job or synthetic.')
    if human_review is not None and not isinstance(human_review, dict):
        raise SafetyError('Human review must be an explicit JSON object.')
    job = reject_link_components(job).resolve()
    output = require_new_output(output, [job])
    extra_inputs = []
    if raw_layout is not None:
        extra_inputs.append(('original_layout', raw_layout))
    extra_inputs.extend(('instruction', instruction) for instruction in instructions)
    originals = []
    for kind, source in extra_inputs:
        path = reject_link_components(source).resolve()
        if not path.is_file():
            raise SafetyError('Explicit provenance file is missing: ' + str(path))
        if output == path or output.is_relative_to(path) or path.is_relative_to(output):
            raise SafetyError('Output overlaps an explicit provenance file.')
        originals.append({'kind': kind, 'path': path, 'sha256': sha256(path), 'size': path.stat().st_size})
    with readonly_job_lock(job) as lock_mode:
        before = inventory(job, reject_temporary=True)
        state = read_json(job / 'state.json')
        layout = _validate_state(job, state)
        tasks = _task_records(job, state)
        if inventory(job, reject_temporary=True) != before:
            raise SafetyError('Source changed during state validation.')
        output.mkdir(parents=True, exist_ok=False)
        copied = []
        for item in before:
            source = safe_relative(job, item['path'])
            destination = output / 'job' / item['path']
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination, follow_symlinks=False)
            if sha256(destination) != item['sha256'] or destination.stat().st_size != item['size']:
                raise SafetyError('Source changed while copying: ' + item['path'])
            copied.append({'path': 'job/' + item['path'], 'sha256': item['sha256'], 'size': item['size']})
        provenance = []
        for index, item in enumerate(originals):
            destination = output / 'provenance' / (str(index) + '-' + item['path'].name)
            destination.parent.mkdir(exist_ok=True)
            shutil.copyfile(item['path'], destination, follow_symlinks=False)
            if sha256(destination) != item['sha256'] or sha256(item['path']) != item['sha256']:
                raise SafetyError('An explicitly supplied provenance file changed while copying.')
            entry = {'path': destination.relative_to(output).as_posix(), 'sha256': item['sha256'], 'size': item['size'],
                     'original_path': str(item['path']), 'kind': item['kind'], 'provenance': 'operator_supplied'}
            provenance.append(entry)
            copied.append({key: entry[key] for key in ('path', 'sha256', 'size')})
        after = inventory(job, reject_temporary=True)
        if before != after:
            raise SafetyError('Source inventory changed during capture; no fixture was published.')
        copied_inventory = inventory(output / 'job')
        expected = [(item['path'], item['sha256'], item['size']) for item in before]
        actual = [(item['path'], item['sha256'], item['size']) for item in copied_inventory]
        if expected != actual:
            raise SafetyError('Copied job inventory is incomplete or changed.')
        missing = ['model_runtime_unknown', 'actual_request_arguments_not_stored', 'quality_and_speed_not_inferred']
        if raw_layout is None:
            missing.append('original_layout_not_supplied')
        if not instructions:
            missing.append('external_instructions_not_supplied')
        if layout is None:
            missing.append('normalized_layout_not_recorded')
        if human_review is None:
            missing.append('human_review_not_supplied')
        fixture = {'format': 'ppt-rebuilder.fixture/1', 'origin': origin,
            'captured_at_utc': datetime.now(timezone.utc).isoformat(), 'job_root': 'job',
            'source': 'job/' + state['source']['original'], 'layout': layout, 'job_original_root': str(job),
            'files': sorted(copied, key=lambda entry: entry['path']), 'missing': missing,
            'human_review': human_review if human_review is not None else {'status': 'unknown'}, 'tasks': tasks,
            'original_layout': next((entry for entry in provenance if entry['kind'] == 'original_layout'), None),
            'instructions': [entry for entry in provenance if entry['kind'] == 'instruction'],
            'capture_consistency': {'lock': lock_mode, 'before_copy_after_sha256_and_inventory': True,
                'absolute_atomic_snapshot_guarantee': False,
                'limitation': 'Read-only shared lock covers cooperating controller writers only. Before/copy/after hashes detect observed changes; arbitrary writers and change-then-restore races cannot be excluded.'}}
        # The only validity marker is written last. Any failure above leaves no fixture.json.
        write_json(output / 'fixture.json', fixture)
    return fixture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--raw-layout')
    parser.add_argument('--instruction', action='append', default=[])
    parser.add_argument('--human-review', help='Explicit JSON file supplied by operator')
    parser.add_argument('--origin', choices=('real_job', 'synthetic'), default='real_job')
    args = parser.parse_args(argv)
    try:
        fixture = capture_fixture(args.job, args.output, raw_layout=args.raw_layout, instructions=args.instruction,
            human_review=read_json(args.human_review) if args.human_review else None, origin=args.origin)
        print(json.dumps({'output': str(Path(args.output).resolve()), 'origin': fixture['origin'],
                          'files': len(fixture['files']), 'missing': fixture['missing']}, ensure_ascii=False))
    except (SafetyError, OSError, TypeError, KeyError) as exc:
        parser.exit(2, 'Capture refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
