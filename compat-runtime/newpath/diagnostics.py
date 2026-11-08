"""Explicit, read-only evidence export for one existing local job."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile

from .execution import _safe_arguments
from .version import RUNTIME_ROOT


_EXCLUDED = {'node_modules', '__pycache__', '.git', '.hg', '.svn'}
_TEXT_CREDENTIAL = re.compile(
    r'(?i)\bbearer\s+[a-z0-9._~+/=-]{8,}'
    r'|\bbasic\s+[a-z0-9+/]{16,}={0,2}'
    r'|\bsk-[a-z0-9_-]{16,}'
    r'|-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'
    r'|https?://[^\s/:]+:[^\s/@]+@'
    r'|\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|password|passwd|client[_-]?secret|secret[_-]?key)'
    r'\s*[\"\']?\s*[:=]\s*[\"\']?[^\s\"\'<>`,;}\]]+')
_PRIVATE_NAMES = {'.env', 'image_config.json', 'credentials.json', 'id_rsa', 'id_ed25519'}
_LOCAL_PATH_KEYS = {'original', 'snapshot', 'working', 'guide', 'brief', 'prompt_file',
                    'request_file', 'invocation_request_file', 'raw', 'normalized', 'image',
                    'compiled', 'presentation', 'pptx', 'report', 'report_file'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _linked(path):
    return path.is_symlink() or (path.exists() and bool(
        getattr(path.lstat(), 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)))


def _safe_chain(path):
    for part in (path, *path.parents):
        if _linked(part):
            raise ValueError('Unsafe linked path is not accepted for diagnostics.')


def _read_file(path, locked_stream=None):
    if locked_stream is not None and path.name == '.lock':
        locked_stream.seek(0)
        return locked_stream.read()
    return path.read_bytes()


def _inventory(root, locked_stream=None):
    """Inventory without following links, including links in excluded folders."""
    found = {}
    for directory, folders, files in os.walk(root, followlinks=False):
        base = Path(directory)
        for name in [*folders, *files]:
            child = base / name
            if _linked(child):
                raise ValueError('Unsafe linked file or directory in diagnostics source.')
        folders[:] = sorted(name for name in folders if name not in _EXCLUDED)
        for name in sorted(files):
            path = base / name
            if path.suffix in {'.pyc', '.pyo'}:
                continue
            stat = path.stat()
            if not path.is_file():
                raise ValueError('Unsafe non-regular diagnostics source file.')
            found[path.relative_to(root).as_posix()] = {
                'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns,
                'ctime_ns': stat.st_ctime_ns, 'device': stat.st_dev, 'inode': stat.st_ino,
                'sha256': _sha(_read_file(path, locked_stream))}
    return found


@contextmanager
def _existing_lock(work):
    """Share an existing lock without creating or modifying job evidence."""
    lock = work / '.lock'
    if not lock.exists():
        yield None
        return
    _safe_chain(lock)
    with lock.open('rb') as stream:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBRLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError('Another process is updating this job; diagnostics requires a stable unlocked job.') from exc
        try:
            yield stream
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _check_credentials(name, data):
    basename = PurePosixPath(name).name.casefold()
    if basename in _PRIVATE_NAMES or basename.startswith('.env.') or basename.endswith(('.pem', '.key', '.pfx', '.p12')):
        raise ValueError('Diagnostics refused a possible credential file; no archive was exported.')
    text = data.decode('utf-8-sig', errors='replace')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        text = data.decode('utf-16', errors='replace')
    if _TEXT_CREDENTIAL.search(text):
        raise ValueError('Diagnostics refused possible credential text; no archive was exported.')
    try:
        document = json.loads(text)
    except ValueError:
        return
    def check_json(value):
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = re.sub(r'[^a-z0-9]', '', key.casefold())
                if normalized in {'apikey', 'accesstoken', 'authtoken', 'bearertoken', 'password',
                                  'passwd', 'clientsecret', 'secretkey', 'secret', 'credentials', 'token'} and child:
                    raise ValueError('Diagnostics refused possible JSON credentials; no archive was exported.')
                check_json(child)
        elif isinstance(value, list):
            for child in value:
                check_json(child)
        elif isinstance(value, str) and _TEXT_CREDENTIAL.search(value):
            raise ValueError('Diagnostics refused encoded credential text; no archive was exported.')
    check_json(document)
    if PurePosixPath(name).parts[0] == 'requests' and isinstance(document, dict):
        request = document.get('request')
        if isinstance(request, dict):
            try:
                _safe_arguments(request)
            except ValueError as exc:
                raise ValueError('Diagnostics refused unsafe request credentials or inline data; no archive was exported.') from exc


def _record(data, name, issues):
    if data is None:
        return {}
    try:
        value = json.loads(data.decode('utf-8-sig'))
        if not isinstance(value, dict):
            raise ValueError('record must be an object')
        return value
    except (UnicodeError, ValueError):
        issues.append({'path': name, 'status': 'invalid_record_original_bytes_retained'})
        return {}


def _recorded_paths(document, work, available, missing, external):
    def add(value, source):
        if not isinstance(value, str) or not value:
            return
        windows = PureWindowsPath(value)
        path = Path(value)
        if '..' in windows.parts:
            raise ValueError('Unsafe recorded diagnostics path; traversal is not allowed.')
        if path.is_absolute() or windows.is_absolute():
            resolved = path.resolve()
            if not resolved.is_relative_to(work):
                external.append({'path': value, 'source': source,
                                 'reason': 'external_reference_not_traversed'})
                return
            value = resolved.relative_to(work).as_posix()
        relative = PurePosixPath(value.replace('\\', '/'))
        if '..' in relative.parts or relative.is_absolute() or ':' in value:
            raise ValueError('Unsafe recorded diagnostics path; no external traversal is allowed.')
        if relative.as_posix() not in available:
            missing.append({'path': relative.as_posix(), 'source': source, 'status': 'missing'})

    def visit(value, source):
        if isinstance(value, dict):
            for key, child in value.items():
                location = source + '.' + str(key)
                if key == 'references' and isinstance(child, dict):
                    for reference in child:
                        add(reference, location)
                elif key == 'references' and isinstance(child, list):
                    for reference in child:
                        add(reference, location)
                elif key == 'input_path' or key in _LOCAL_PATH_KEYS:
                    add(child, location)
                visit(child, location)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, source + '[' + str(index) + ']')
    visit(document, 'record')


def _runtime_paths(runtime):
    paths = []
    code = runtime / 'newpath'
    if code.is_dir():
        for name in _inventory(code):
            if Path(name).suffix in {'.py', '.cjs', '.js', '.json'}:
                paths.append((code / name, 'runtime/newpath/' + name))
    for name in ('SKILL.md', 'MANIFEST.json', 'package.json', 'package-lock.json', 'requirements.txt'):
        paths.append((runtime / name, 'runtime/' + name))
    for name in ('PROJECT_INSTRUCTIONS.txt', 'docs/CHATGPT_PROJECT_INSTRUCTIONS.txt',
                 'RELEASE.json', 'LICENSE', 'NOTICE.md'):
        paths.append((runtime.parent / name, 'project/' + name))
    return paths


def _selected_job_summary(state):
    tasks = state.get('tasks')
    summary = {'phase': state.get('phase'), 'failed_task_ids': None,
               'not_started_task_ids': None, 'recorded_request_count': None,
               'recorded_request_count_provenance':
                   'distinct_request_file_fields_in_task_execution_records_not_provider_call_count',
               'warnings': []}
    if not isinstance(tasks, dict):
        return summary
    summary['failed_task_ids'] = sorted(identity for identity, task in tasks.items()
                                       if isinstance(task, dict) and task.get('status') == 'failed')
    summary['not_started_task_ids'] = sorted(identity for identity, task in tasks.items()
        if isinstance(task, dict) and isinstance(task.get('execution'), dict)
        and task['execution'].get('state') == 'not_started')
    requests = set()
    complete = True
    for task in tasks.values():
        if not isinstance(task, dict) or not isinstance(task.get('execution_history', []), list):
            complete = False
            continue
        for record in [*task.get('execution_history', []), task.get('execution')]:
            if not isinstance(record, dict):
                complete = False
            elif isinstance(record.get('request_file'), str) and record['request_file']:
                requests.add(record['request_file'])
            elif record.get('state') != 'not_started':
                complete = False
    if complete:
        summary['recorded_request_count'] = len(requests)
        if not requests:
            summary['warnings'].append('selected_job_has_no_recorded_image_invocation')
    return summary


def export_diagnostics(work, output):
    """Write one new external ZIP without advancing or modifying the job."""
    original = Path(work).absolute()
    _safe_chain(original)
    if not original.is_dir():
        raise ValueError('Diagnostics requires the exact existing job directory.')
    work = original.resolve()
    if not any((work / name).is_file() for name in ('state.json', 'initialization.json')):
        raise ValueError('Diagnostics requires an existing job state or initialization record.')
    runtime = Path(RUNTIME_ROOT).resolve()
    destination = Path(output).absolute()
    _safe_chain(destination)
    if os.path.lexists(destination):
        raise ValueError('Refusing existing output; choose a new diagnostics ZIP path.')
    destination = destination.resolve()
    if destination.is_relative_to(work) or destination.is_relative_to(runtime):
        raise ValueError('Refusing diagnostics output inside the job or immutable runtime.')
    if not destination.parent.is_dir():
        raise ValueError('Diagnostics output parent directory must already exist.')
    temporary = None
    with _existing_lock(work) as locked_stream:
        before = _inventory(work, locked_stream)
        contents = {}
        files = []
        missing = []
        issues = []
        external = []
        for name, facts in before.items():
            path = work / name
            data = _read_file(path, locked_stream)
            if _sha(data) != facts['sha256']:
                raise ValueError('Job changed during diagnostics; no archive was exported.')
            _check_credentials(name, data)
            contents['job/' + name] = data
            files.append({'archive_path': 'job/' + name, 'source_path': str(path),
                          'size': len(data), 'sha256': facts['sha256'],
                          'filesystem_mtime_ns': facts['mtime_ns'],
                          'timestamp_provenance': 'filesystem_mtime_not_execution_time'})
        records = {}
        for name in ('state.json', 'initialization.json', 'receipt.json', 'build/compiled.report.json'):
            if name not in before:
                missing.append({'path': name, 'status': 'missing'})
            records[name] = _record(contents.get('job/' + name), name, issues)
        for name in before:
            if name.startswith('requests/') and name.endswith('.json'):
                records[name] = _record(contents['job/' + name], name, issues)
        for name, value in records.items():
            _recorded_paths(value, work, before, missing, external)
        runtime_before = {}
        for path, name in _runtime_paths(runtime):
            _safe_chain(path.absolute())
            if not path.is_file():
                missing.append({'path': name, 'status': 'missing'})
                continue
            data = path.read_bytes()
            _check_credentials(name, data)
            facts = path.stat()
            runtime_before[path] = (_sha(data), facts.st_mtime_ns)
            contents[name] = data
            files.append({'archive_path': name, 'source_path': str(path),
                          'size': len(data), 'sha256': _sha(data),
                          'filesystem_mtime_ns': facts.st_mtime_ns,
                          'timestamp_provenance': 'filesystem_mtime_not_execution_time'})
        state = records['state.json']
        selected_job_summary = _selected_job_summary(state)
        source = state.get('source')
        source_provenance = 'state.json'
        if source is None and records['initialization.json']:
            source = {key: records['initialization.json'][key] for key in
                      ('input_path', 'snapshot', 'byte_sha256', 'pixel_sha256')
                      if key in records['initialization.json']}
            source_provenance = 'initialization.json'
        index = {'format': 'ppt-rebuilder.job-diagnostics/1', 'work': str(work),
                 'read_only': True, 'source': source, 'source_provenance': source_provenance,
                 'selected_job_summary': selected_job_summary,
                 'workflow': {'phase': state.get('phase'), 'tasks': state.get('tasks'),
                              'done': state.get('done'), 'events': state.get('events')},
                 'files': files, 'missing_files': missing, 'record_issues': issues,
                 'uncollected_external_references': external,
                 'excluded_directory_names': sorted(_EXCLUDED),
                 'timestamp_note': 'Only recorded event timestamps and labeled filesystem mtimes are included.',
                 'zip_timestamp_note': 'ZIP entry dates are fixed to 1980-01-01 container metadata, not job event dates.',
                 'assessment': 'Existing evidence only; export does not advance work or establish visual acceptance.'}
        index_data = (json.dumps(index, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        _check_credentials('index.json', index_data)
        try:
            handle, temporary_name = tempfile.mkstemp(prefix='.' + destination.name + '.', suffix='.tmp', dir=destination.parent)
            os.close(handle)
            temporary = Path(temporary_name)
            with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
                # Fixed container dates avoid inventing image-generation or job event times.
                from zipfile import ZipInfo
                for name, data in [*contents.items(), ('index.json', index_data)]:
                    info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = ZIP_DEFLATED
                    archive.writestr(info, data)
            if _inventory(work, locked_stream) != before:
                raise ValueError('Job changed during diagnostics; no archive was exported.')
            for path, facts in runtime_before.items():
                if (_sha(path.read_bytes()), path.stat().st_mtime_ns) != facts:
                    raise ValueError('Runtime changed during diagnostics; no archive was exported.')
            # Hard-link publication is atomic and refuses a destination created concurrently.
            # The completed temporary ZIP and output share the same filesystem.
            os.link(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return {'kind': 'diagnostics', 'work': str(work), 'output': str(destination),
            'read_only': True, 'files': len(files), 'missing_files': len(missing),
            'selected_job_summary': selected_job_summary}
