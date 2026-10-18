"""Small local records and exclusive job updates; no external service telemetry."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def relative_file(work, path):
    root = Path(work).resolve()
    result = (root / path).resolve()
    if not result.is_relative_to(root):
        raise ValueError('A recorded job file escapes its directory.')
    return result


def read_state(work):
    path = Path(work) / 'state.json'
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('format') != 'newpath.job/1':
        raise ValueError('This directory belongs to a different job format.')
    return value


def save_state(work, state, event):
    state.setdefault('events', []).append({'event': event, 'at_utc': datetime.now(timezone.utc).isoformat()})
    write_json(Path(work) / 'state.json', state)


@contextmanager
def job_lock(work):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    with (work / '.lock').open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0)
            if not stream.read(1):
                stream.write(b'1'); stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError('Another process is updating this job.') from exc
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
