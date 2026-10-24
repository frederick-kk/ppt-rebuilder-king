"""Explicit, offline dual-runtime replay. This module is never imported by newpath."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

sys.dont_write_bytecode = True


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def unlinked_path(value):
    path = Path(os.path.abspath(value))
    for part in (path, *path.parents):
        if os.path.lexists(part):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('Replay refuses symlink or reparse-point paths')
    return path.resolve()


def protected_roots(runtime_roots, fixture_root, fixture):
    roots = [*runtime_roots, fixture_root, Path(__file__).resolve().parents[1] / 'compat-runtime']
    original = fixture.get('job_original_root')
    if isinstance(original, str) and Path(original).is_absolute(): roots.append(Path(original))
    return [Path(root).resolve() for root in roots]


def new_output(path, roots):
    path = unlinked_path(path)
    for source in roots:
        if path.is_relative_to(source) or source.is_relative_to(path):
            raise ValueError('Replay output overlaps an input/runtime/original captured job')
    if os.path.lexists(path): raise ValueError('Replay output must not exist')
    return path


def contained(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or '\\' in relative or ':' in relative:
        raise ValueError('Fixture paths must be relative POSIX paths')
    result = unlinked_path(root / relative)
    if not result.is_relative_to(root):
        raise ValueError('Fixture path escapes its root or traverses a symlink')
    return result


def verify_fixture(root):
    root = Path(root).resolve()
    fixture = read_json(root / 'fixture.json')
    if fixture.get('format') != 'ppt-rebuilder.fixture/1':
        raise ValueError('Unsupported fixture format')
    listed = set()
    for entry in fixture.get('files', []):
        name = entry['path']
        if name in listed or name == 'fixture.json':
            raise ValueError('Duplicate or self-referential fixture inventory')
        listed.add(name)
        path = contained(root, name)
        data = path.read_bytes()
        if digest(data) != entry['sha256'] or len(data) != entry['size']:
            raise ValueError('Fixture content changed: ' + name)
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and p.name != 'fixture.json'}
    if actual != listed:
        raise ValueError('Fixture has missing or unlisted files')
    needed = [fixture.get('source'), fixture.get('layout')]
    for task in fixture.get('tasks', []):
        needed.extend([task.get('brief'), task.get('result')])
        needed.extend(r.get('path') for r in task.get('references', []))
        for reference in task.get('references', []):
            if not reference.get('sha256') or digest(contained(root, reference['path']).read_bytes()) != reference['sha256']:
                raise ValueError('Fixture reference checksum is missing or inconsistent')
    if any(name not in listed for name in needed if name is not None):
        raise ValueError('Fixture references an uninventoried file')
    if not fixture.get('source') or not fixture.get('layout'):
        raise ValueError('Replay needs the fixed source and saved layout; no inference is performed')
    return fixture


def deep_differences(a, b, path='$', limit=200):
    differences = []
    def visit(left, right, at):
        if len(differences) >= limit:
            return
        if type(left) is not type(right):
            differences.append({'path': at, 'baseline': left, 'candidate': right})
        elif isinstance(left, dict):
            for key in sorted(left.keys() | right.keys()):
                if key not in left or key not in right:
                    differences.append({'path': at + '.' + key, 'baseline': left.get(key, '<absent>'), 'candidate': right.get(key, '<absent>')})
                else:
                    visit(left[key], right[key], at + '.' + key)
        elif isinstance(left, list):
            if len(left) != len(right):
                differences.append({'path': at + '.length', 'baseline': len(left), 'candidate': len(right)})
            for i, (x, y) in enumerate(zip(left, right)):
                visit(x, y, f'{at}[{i}]')
        elif left != right:
            differences.append({'path': at, 'baseline': left, 'candidate': right})
    visit(a, b, path)
    return differences


def pptx_content(path, root_mappings=()):
    """Keep every entry and byte except two core-property timestamp values."""
    entries, exclusions = {}, []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or z.testzip():
            raise ValueError('Corrupt PPTX or duplicate package entries')
        for name in sorted(names):
            data = z.read(name)
            if name == 'docProps/core.xml':
                root = ET.fromstring(data)
                for kind in ('created', 'modified'):
                    for node in root.findall('{http://purl.org/dc/terms/}' + kind):
                        exclusions.append({'entry': name, 'field': 'dcterms:' + kind, 'value': node.text,
                                           'reason': 'explicit package timestamp whitelist'})
                        node.text = '$PACKAGE_TIMESTAMP'
                data = ET.tostring(root, encoding='utf-8')
            elif name.startswith('ppt/slides/') and name.endswith('.xml') and root_mappings:
                root = ET.fromstring(data)
                changed = False
                for node in root.iter('{http://schemas.openxmlformats.org/presentationml/2006/main}cNvPr'):
                    original = node.get('descr', '')
                    for source, replacement in root_mappings:
                        for spelling in (str(source), str(source).replace('\\', '/')):
                            if original.startswith(spelling + '/') or original.startswith(spelling + '\\'):
                                node.set('descr', replacement + original[len(spelling):].replace('\\', '/'))
                                exclusions.append({'entry': name, 'field': 'p:cNvPr[@id=' + node.get('id', '') + ']/@descr',
                                                   'value': original, 'mapped_to': node.get('descr'),
                                                   'reason': 'exact isolated work-root path in backend image description'})
                                changed = True
                                break
                        if node.get('descr') != original: break
                if changed: data = ET.tostring(root, encoding='utf-8')
            entries[name] = {'sha256': digest(data), 'size': len(data)}
    return {'raw_sha256': digest(Path(path).read_bytes()), 'entries': entries,
            'content_sha256': digest(json.dumps(entries, sort_keys=True).encode()), 'exclusions': exclusions,
            'zip_metadata_policy': 'ZIP container timestamps/compression metadata excluded; all entry names/content retained'}


def _temporal(path, key):
    if path == ('initialization.json',) and key == 'created_at_utc': return True
    if path == ('state.json',) and key == 'started_at_utc': return True
    if path[:2] == ('state.json', 'events') and key == 'at_utc': return True
    if path[:2] == ('state.json', 'tasks') and 'attempts' in path and key == 'observed_at_utc': return True
    if path[:2] == ('state.json', 'processing_seconds') and key == 'seconds': return True
    if key == 'controller_wall_seconds' and (path in [('receipt.json',), ('state.json', 'done')] or path[:1] == ('transcript',)):
        return True
    return False


def normalized(value, path, roots, pptx_hashes, exclusions):
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if _temporal(path, key):
                exclusions.append({'path': '.'.join(map(str, (*path, key))), 'value': child,
                                   'reason': 'local replay timestamp/duration; not a production speed measurement'})
                result[key] = '$LOCAL_TIME'
            elif key in ('sha256', 'compiled_sha256') and isinstance(child, str) and child in pptx_hashes:
                result[key] = 'pptx-content:' + pptx_hashes[child]
                exclusions.append({'path': '.'.join(map(str, (*path, key))), 'value': child,
                                   'reason': 'PPTX content digest compared separately with explicit timestamp whitelist'})
            else:
                result[key] = normalized(child, (*path, key), roots, pptx_hashes, exclusions)
        return result
    if isinstance(value, list):
        return [normalized(child, (*path, index), roots, pptx_hashes, exclusions) for index, child in enumerate(value)]
    path_field = bool(path) and (path[-1] in ('input_path', 'path', 'image', 'pptx', 'background', 'output', 'prompt_file', 'file')
                                or (isinstance(path[-1], int) and len(path) > 1 and path[-2] in ('references', 'files')))
    if isinstance(value, str) and path_field:
        for original, replacement in roots:
            for spelling in (str(original), str(original).replace('\\', '/')):
                if value == spelling or value.startswith(spelling + '/') or value.startswith(spelling + '\\'):
                    value = replacement + value[len(spelling):].replace('\\', '/')
                    exclusions.append({'path': '.'.join(map(str, path)), 'reason': 'isolated replay path mapping',
                                       'from': spelling, 'to': replacement})
                    return value
        return value
    return value


def tree_inventory(root):
    result = {}
    for path in sorted(Path(root).rglob('*')):
        unlinked_path(path)
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def artifacts(work, roots, transcript, renders):
    from PIL import Image
    work = Path(work)
    exclusions, raw_hashes, pptx_hashes = [], {}, {}
    for path in work.rglob('*.pptx'):
        content = pptx_content(path, [(work, '$WORK')])
        pptx_hashes[content['raw_sha256']] = content['content_sha256']
    output = {}
    for path in sorted(work.rglob('*')):
        if not path.is_file(): continue
        name = path.relative_to(work).as_posix()
        if name == '.lock':
            exclusions.append({'path': '.lock', 'reason': 'OS-specific synchronization byte only'})
            continue
        data = path.read_bytes(); raw_hashes[name] = digest(data)
        if path.suffix == '.pptx':
            content = pptx_content(path, [(work, '$WORK')])
            output[name] = {'entries': content['entries'], 'content_sha256': content['content_sha256']}
            exclusions.extend({'path': name, **entry} for entry in content['exclusions'])
        elif path.suffix == '.json':
            output[name] = normalized(read_json(path), (name,), roots, pptx_hashes, exclusions)
        elif path.suffix.lower() == '.png':
            with Image.open(path) as opened:
                rgba = opened.convert('RGBA')
                output[name] = {'sha256': digest(data), 'size': len(data), 'dimensions': list(rgba.size),
                                'rgba_sha256': digest(rgba.tobytes())}
        else:
            output[name] = {'sha256': digest(data), 'size': len(data)}
    return {'files': output,
            'transcript': normalized(transcript, ('transcript',), roots, pptx_hashes, exclusions),
            'render_manifests': normalized(renders, ('render_manifests',), roots, pptx_hashes, exclusions)}, raw_hashes, exclusions


def _request_record(job, state, work):
    task = state['tasks'][job['id']]
    return {**job, 'prompt_sha256': digest(Path(job['prompt_file']).read_bytes()),
            'ordered_references': [digest(Path(p).read_bytes()) for p in job['references']],
            'board': task.get('board')}


def _match_request(job, fixture_task, fixture_root):
    expected_prompt = contained(fixture_root, fixture_task['brief']).read_bytes()
    if Path(job['prompt_file']).read_bytes() != expected_prompt:
        raise ValueError('Replay request prompt differs from captured input: ' + job['id'])
    refs = fixture_task.get('references', [])
    if [digest(Path(p).read_bytes()) for p in job['references']] != [digest(contained(fixture_root, r['path']).read_bytes()) for r in refs]:
        raise ValueError('Replay ordered references differ from captured input: ' + job['id'])
    for key in ('role', 'attempt', 'size', 'key', 'requested_outputs', 'transparent_background', 'background_mode'):
        if job.get(key) != fixture_task.get(key):
            raise ValueError('Replay request parameter differs: ' + job['id'] + '.' + key)


@contextmanager
def offline_only():
    original_connect, original_create = socket.socket.connect, socket.create_connection
    def blocked(*args, **kwargs):
        raise RuntimeError('Offline replay prohibits network connections')
    socket.socket.connect = blocked
    socket.create_connection = blocked
    try: yield
    finally:
        socket.socket.connect, socket.create_connection = original_connect, original_create


def worker(runtime, fixture_root, work, report):
    runtime, fixture_root = map(unlinked_path, (runtime, fixture_root))
    fixture = verify_fixture(fixture_root)
    roots_to_protect = protected_roots([runtime], fixture_root, fixture)
    work = new_output(work, roots_to_protect)
    report = new_output(report, [*roots_to_protect, work])
    sys.path.insert(0, str(runtime))
    from newpath import controller, renderer
    # Capture only the temporary render.json passed to the existing backend.
    # The runtime module/files and its normal subprocess behavior remain unchanged.
    original_run = subprocess.run
    renders, roots = [], [(work, '$WORK'), (fixture_root, '$FIXTURE')]
    def captured_run(command, *args, **kwargs):
        if isinstance(command, (tuple, list)) and len(command) > 2 and Path(str(command[1])).name == 'render.cjs':
            manifest = read_json(command[2])
            roots.insert(0, (manifest['output'], '$RENDER_OUTPUT'))
            renders.append(manifest)
        return original_run(command, *args, **kwargs)
    subprocess.run = captured_run
    transcript, requests = [], []
    completed, error = False, None
    by_identity = {}
    for task in fixture.get('tasks', []):
        identity = (task['id'], task['role'], task['attempt'])
        # A failed/unreadable registration may be corrected with an already
        # returned image without authorizing another invocation/attempt.
        by_identity.setdefault(identity, []).append(task)
    try:
        with offline_only():
            response = controller.run(work, source=contained(fixture_root, fixture['source']))
            for step in range(1000):
                transcript.append(response)
                kind = response.get('kind')
                if kind == 'done':
                    if any(by_identity.values()):
                        raise ValueError('Captured observations remain unconsumed at replay completion')
                    completed = True
                    break
                if kind == 'describe':
                    response = controller.run(work, data={'kind': 'layout', 'layout': read_json(contained(fixture_root, fixture['layout']))})
                elif kind == 'generate':
                    state = read_json(work / 'state.json')
                    for job in response['jobs']: requests.append(_request_record(job, state, work))
                    # A whole batch was issued, but registration remains serial.
                    # Process each resulting pause before registering dependent results.
                    response = _register_recorded(response['jobs'][0], by_identity, fixture_root, work, controller, transcript)
                elif kind == 'await_results':
                    pending = response.get('pending_results', [])
                    if not pending: raise ValueError('Replay lacks an exact pending-result descriptor')
                    response = _register_recorded(pending[0], by_identity, fixture_root, work, controller, transcript)
                elif kind == 'paused':
                    # Explicit replay retries only when a subsequent recorded attempt exists.
                    state = read_json(work / 'state.json')
                    pending = next((t for t in state['tasks'].values() if t['status'] == 'failed' and
                                    (by_identity.get((t['id'], t['role'], t['attempt'])) or
                                     by_identity.get((t['id'], t['role'], t['attempt'] + 1)))), None)
                    if pending is None:
                        error = 'Captured job is paused; no subsequent recorded retry exists'
                        break
                    if by_identity.get((pending['id'], pending['role'], pending['attempt'])):
                        response = _register_recorded(controller._public_task(work, pending), by_identity, fixture_root, work, controller, transcript)
                    else:
                        captured=by_identity[(pending['id'],pending['role'],pending['attempt']+1)][0]
                        authorization=captured.get('retry_authorization')
                        if state.get('generation_policy') and authorization is None:
                            raise ValueError('Captured retry has no recorded user authorization; replay will not invent approval')
                        retry_kwargs={'retry_task':pending['id']}
                        if authorization is not None:retry_kwargs['retry_authorization']=authorization
                        response = controller.run(work, **retry_kwargs)
                else:
                    raise ValueError('Replay cannot infer missing registration for action: ' + str(kind))
            else: raise ValueError('Replay step limit reached')
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        error = str(exc)
    finally:
        subprocess.run = original_run
    snapshot, hashes, exclusions = artifacts(work, roots, transcript, renders) if work.exists() else ({}, {}, [])
    snapshot['requests'] = normalized(requests, ('requests',), roots, {}, exclusions)
    result = {'format': 'ppt-rebuilder.replay-run/1', 'complete': completed, 'error': error,
              'snapshot': snapshot, 'raw_file_hashes': hashes, 'exclusions': exclusions,
              'replay_image_requests_issued': 0, 'real_generation_calls': None,
              'generation_count_provenance': 'No image-service client exists in replay; external service usage is not independently measured',
              'network_enforcement': 'Python socket guard only; trusted local Node rendering subprocess is not OS-network-sandboxed',
              'fresh_work_directory': True,
              'fixture_origin': fixture.get('origin', 'unknown'),
              'limitations': ['Fixed-return local replay only; no host/visual/Office acceptance',
                              'Local replay duration is not T_user or service performance']}
    write_json(report, result)
    return result


def _register_recorded(job, by_identity, fixture_root, work, controller, transcript):
    observations = by_identity.get((job['id'], job['role'], job['attempt']))
    if not observations:
        raise ValueError('No captured result matches exact task/role/attempt: ' + job['id'])
    recorded = observations.pop(0)
    # Await descriptors authorize registration only. Recover the frozen
    # internal request for matching, never to dispatch image generation.
    if job.get('action')=='register_existing_output_only':
        state=read_json(work/'state.json')
        request=controller._public_task(work,state['tasks'][job['id']])
    else:request=job
    _match_request(request, recorded, fixture_root)
    submission = {'task_id': job['id'], 'role': job['role'], 'attempt': job['attempt']}
    for key in ('invocation_count', 'tool', 'tool_call_id', 'tool_output_id', 'executed_prompt'):
        if recorded.get(key) is not None: submission[key] = recorded[key]
    if recorded.get('outcome') == 'failed':
        submission.update(outcome='failed', reason=recorded.get('reason') or 'Recorded failed invocation')
    elif recorded.get('result'):
        submission['file'] = str(contained(fixture_root, recorded['result']))
    else:
        raise ValueError('Captured result is missing; real generation is prohibited')
    try:
        return controller.run(work, data=submission)
    except ValueError as exc:
        state = read_json(work / 'state.json')
        current = state['tasks'][job['id']]
        if recorded.get('outcome') != 'unreadable' or current['status'] != 'failed': raise
        transcript.append({'kind': 'recorded_unreadable_registration', 'task_id': job['id'], 'attempt': job['attempt'], 'message': str(exc)})
        return controller.run(work)


def compare(baseline, candidate, fixture, output):
    baseline, candidate, fixture = map(unlinked_path, (baseline, candidate, fixture))
    # Check basic overlaps before loading the fixture so invalid outputs fail first.
    output = new_output(output, [baseline, candidate, fixture])
    for runtime in (baseline, candidate):
        if not (runtime / 'newpath/controller.py').is_file(): raise ValueError('Runtime directory lacks newpath')
    fixture_data = verify_fixture(fixture)
    output = new_output(output, protected_roots([baseline, candidate], fixture, fixture_data))
    original_trees = {str(p): tree_inventory(p) for p in (baseline, candidate, fixture)}
    output.mkdir(parents=True)
    runs = []
    for name, runtime in [('baseline', baseline), ('candidate', candidate)]:
        report = output / (name + '.json')
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--worker', '--runtime', str(runtime),
                   '--fixture', str(fixture), '--work', str(output / (name + '-work')), '--report', str(report)]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=600,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUTF8': '1'})
        if result.returncode or not report.exists():
            runs.append({'complete': False, 'error': 'Offline worker failed: ' + (result.stderr or result.stdout)[-2000:], 'snapshot': {}})
        else: runs.append(read_json(report))
    preserved = all(tree_inventory(Path(p)) == inventory for p, inventory in original_trees.items())
    differences = deep_differences(runs[0]['snapshot'], runs[1]['snapshot'])
    complete = all(run['complete'] for run in runs)
    summary = {'format': 'ppt-rebuilder.replay-comparison/1',
               'passed': complete and not differences and preserved,
               'equivalent': complete and not differences and preserved,
               'partial_snapshots_equal': not differences, 'replay_complete': complete,
               'inputs_preserved': preserved, 'differences': differences,
               'errors': {'baseline': runs[0].get('error'), 'candidate': runs[1].get('error')},
               'replay_image_requests_issued': 0, 'real_generation_calls': None,
               'generation_count_provenance': 'No image-service client exists in replay; external service usage is not independently measured',
               'network_enforcement': 'Python socket guard only; trusted local Node rendering subprocess is not OS-network-sandboxed',
               'fixture_origin': fixture_data.get('origin', 'unknown'),
               'report_files': ['baseline.json', 'candidate.json'],
               'quality': 'not_evaluated', 'office_render_checked': False,
               'performance_acceptance': 'not_evaluated',
               'exclusion_policy': 'Only isolated root/render-output paths (including exact work-root prefixes in p:cNvPr/@descr), enumerated replay timestamps/durations, core created/modified XML values, OS lock byte and ZIP container metadata. Full exclusion lists and raw hashes retained in each run.'}
    write_json(output / 'comparison.json', summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--candidate', type=Path)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--runtime', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--work', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--report', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    try:
        if args.worker:
            worker(args.runtime, args.fixture, args.work, args.report)
            return 0
        if not all((args.baseline, args.candidate, args.output)):
            parser.error('--baseline, --candidate and --output are required')
        result = compare(args.baseline, args.candidate, args.fixture, args.output)
        print(json.dumps({key: result[key] for key in ('passed', 'equivalent', 'replay_complete', 'inputs_preserved', 'errors')}, ensure_ascii=False))
        return 0 if result['passed'] else 2
    except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'passed': False, 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
