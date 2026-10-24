"""Read-only evidence primitives, independent of the production runtime."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat


class SafetyError(ValueError):
    """Input cannot be safely or faithfully represented as evidence."""


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SafetyError('Missing or invalid JSON record: ' + str(path)) from exc


def write_json(path, value):
    """Write only a new destination; callers create dedicated output directories."""
    with Path(path).open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def _linked(path):
    details = Path(path).lstat()
    return stat.S_ISLNK(details.st_mode) or bool(getattr(details, 'st_file_attributes', 0) & 0x400)


def reject_link_components(path):
    """Reject symlinks and Windows reparse points, including linked ancestors."""
    path = Path(os.path.abspath(path))
    for piece in (path, *path.parents):
        if os.path.lexists(piece) and _linked(piece):
            raise SafetyError('Symlink or reparse-point traversal is not allowed: ' + str(piece))
    return path


def safe_relative(root, value):
    """Resolve a stored POSIX relative path, never a drive/root/parent escape."""
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        raise SafetyError('A stored path must be a relative POSIX path.')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '.' == value:
        raise SafetyError('A stored path escapes its directory: ' + value)
    root = reject_link_components(root).resolve()
    result = reject_link_components(root.joinpath(*path.parts))
    if not result.resolve().is_relative_to(root):
        raise SafetyError('A stored path escapes its directory: ' + value)
    return result


def require_new_output(output, sources=(), runtime=None):
    output = reject_link_components(output).resolve()
    protected = [Path(item).resolve() for item in sources]
    protected.append(Path(runtime).resolve() if runtime else Path(__file__).resolve().parents[1] / 'compat-runtime')
    for source in protected:
        if output == source or output.is_relative_to(source) or source.is_relative_to(output):
            raise SafetyError('Output overlaps an original source or immutable runtime: ' + str(source))
    if output.exists():
        raise SafetyError('Output must be a new directory: ' + str(output))
    return output


def inventory(root, reject_temporary=False):
    """Hash every regular file without following links; include stability metadata."""
    root = reject_link_components(root).resolve()
    if not root.is_dir():
        raise SafetyError('Source directory is missing: ' + str(root))
    records = []
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(current) / name
            if _linked(path):
                raise SafetyError('Source contains a symlink or reparse point: ' + str(path))
            if reject_temporary and (name.endswith('.tmp') or name.endswith('.copying')):
                raise SafetyError('Source has an unfinished temporary write: ' + str(path))
        for name in sorted(files):
            path = Path(current) / name
            before = path.stat()
            if not stat.S_ISREG(before.st_mode):
                raise SafetyError('Source contains a non-regular file: ' + str(path))
            checksum = sha256(path)
            after = path.stat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise SafetyError('Source changed while being hashed: ' + str(path))
            records.append({'path': path.relative_to(root).as_posix(), 'sha256': checksum, 'size': after.st_size,
                            'mtime_ns': after.st_mtime_ns, 'ctime_ns': after.st_ctime_ns})
    return sorted(records, key=lambda item: item['path'])


@contextmanager
def readonly_job_lock(root):
    """Take a shared OS lock on an existing lock file, without writing/creating it."""
    path = Path(root) / '.lock'
    if not path.exists():
        yield 'lock_file_absent_before_after_inventory_used'
        return
    reject_link_components(path)
    try:
        stream = path.open('rb')
    except OSError as exc:
        raise SafetyError('Cannot read the existing job lock.') from exc
    with stream:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            import msvcrt
            class OVERLAPPED(ctypes.Structure):
                _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                            ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(OVERLAPPED)]
            handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
            overlap = OVERLAPPED()
            if not kernel.LockFileEx(handle, 1, 0, 1, 0, ctypes.byref(overlap)):
                raise SafetyError('Job lock is held or cannot be read-locked; capture refused.')
            try:
                yield 'existing_lock_read_only_shared_lock'
            finally:
                kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError as exc:
                raise SafetyError('Job lock is held; capture refused.') from exc
            try:
                yield 'existing_lock_read_only_shared_lock'
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
