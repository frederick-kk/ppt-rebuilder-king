"""Check the local release snapshot; this is not provider identity proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re


PACKAGE_ID = 'PPT Rebuilder King-20261108-flow-r1'
ARCHIVE_NAME = 'PPT-Rebuilder-King-ChatGPT-20261108-flow-r1.zip'
RUNTIME_ROOT = Path(__file__).resolve().parent.parent
_CRITICAL_FILES = {'newpath/version.py', 'newpath/controller.py',
                   'newpath/__main__.py', 'newpath/execution.py'}


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _derived(path):
    return '__pycache__' in path.parts or path.suffix == '.pyc'


def validate_work_directory(work):
    """Keep mutable job evidence outside the immutable runtime snapshot."""
    if Path(work).resolve().is_relative_to(Path(RUNTIME_ROOT).resolve()):
        raise ValueError('A new job directory must be outside the verified compat-runtime directory.')


def runtime_identity(expected_package=None):
    """Verify this release's manifest and every declared local runtime file.

    The expected name is an execution constraint. A caller cannot silently use
    a differently named package, stale code snapshot or unlisted runtime file.
    Python's derived bytecode cache is deliberately outside the source snapshot.
    No file is written, no remote service is contacted, and no key is inspected.
    """
    expected = PACKAGE_ID if expected_package is None else expected_package
    if not isinstance(expected, str) or expected != PACKAGE_ID:
        raise ValueError('Expected package differs from this runtime version: '+PACKAGE_ID)
    root = Path(RUNTIME_ROOT).resolve()
    manifest_path = root/'MANIFEST.json'
    raw = manifest_path.read_bytes()
    try:
        manifest = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError) as error:
        raise ValueError('Runtime manifest is not valid UTF-8 JSON.') from error
    if not isinstance(manifest, dict) or manifest.get('package_id') != PACKAGE_ID:
        raise ValueError('Runtime manifest package identity differs from the expected version.')
    if manifest.get('archive_name') != ARCHIVE_NAME:
        raise ValueError('Runtime manifest archive identity differs from the expected package.')
    rows = manifest.get('files')
    if not isinstance(rows, list) or not rows:
        raise ValueError('Runtime manifest must declare its files.')
    if type(manifest.get('source_file_count')) is not int or manifest['source_file_count'] != len(rows):
        raise ValueError('Runtime manifest file count differs from its records.')
    snapshot = _sha(json.dumps(rows, ensure_ascii=False, sort_keys=True,
                               separators=(',', ':')).encode('utf-8'))
    if snapshot != manifest.get('source_snapshot_sha256'):
        raise ValueError('Runtime manifest source snapshot digest differs from its records.')
    declared = set(); aliases = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('Runtime manifest file record must be an object.')
        name = row.get('path')
        if not isinstance(name, str) or not name or '\\' in name or ':' in name:
            raise ValueError('Runtime manifest contains an unsafe path.')
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or name != relative.as_posix()
                or name == 'MANIFEST.json' or _derived(relative)):
            raise ValueError('Runtime manifest contains an unsafe path.')
        if name.casefold() in aliases:
            raise ValueError('Runtime manifest contains duplicate paths.')
        aliases.add(name.casefold()); declared.add(name)
        path = root.joinpath(*relative.parts)
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise ValueError('Runtime manifest file is missing or its path leaves the package: '+name)
        if (type(row.get('size')) is not int or row['size'] < 0 or
                not isinstance(row.get('sha256'), str) or
                re.fullmatch('[0-9a-f]{64}', row['sha256']) is None):
            raise ValueError('Runtime manifest contains invalid file size or hash records.')
        data = path.read_bytes()
        if len(data) != row['size'] or _sha(data) != row['sha256']:
            raise ValueError('Runtime file size/hash changed after packaging: '+name)
    if not _CRITICAL_FILES.issubset(declared):
        raise ValueError('Runtime manifest is missing critical execution code.')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*')
              if p.is_file() and p != manifest_path and not _derived(p.relative_to(root))}
    if actual != declared:
        raise ValueError('Runtime contains missing or unlisted manifest files.')
    return {'format': 'ppt-rebuilder.local-runtime-identity/1',
            'package_id': PACKAGE_ID, 'archive_name': ARCHIVE_NAME,
            'manifest_sha256': _sha(raw), 'source_snapshot_sha256': snapshot,
            'verified_files': len(rows),
            'identity_provenance': 'local_manifest_and_file_hashes_not_provider_verified'}
