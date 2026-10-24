"""Fingerprint an original ZIP and this machine; no dependency changes or extraction."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import stat
import subprocess
import sys
import zipfile

from .common import SafetyError, read_json, reject_link_components, require_new_output, sha256, write_json


def _archive_records(archive):
    records, instructions, packages = [], [], {}
    with zipfile.ZipFile(archive) as z:
        members = {}
        for member in z.infolist():
            name = member.filename
            path = PurePosixPath(name)
            if not name or '\\' in name or ':' in name or path.is_absolute() or '..' in path.parts:
                raise SafetyError('Archive contains an unsafe path.')
            if stat.S_ISLNK(member.external_attr >> 16):
                raise SafetyError('Archive contains a symlink.')
            canonical = path.as_posix().rstrip('/')
            if canonical.casefold() in members:
                raise SafetyError('Archive contains duplicate or case-colliding paths.')
            members[canonical.casefold()] = name
            if member.is_dir():
                continue
            data = z.read(member)
            record = {'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
            records.append(record)
            if name == 'SKILL.md' or name.startswith('references/'):
                instructions.append(record.copy())
            if name.startswith('node_modules/') and name.endswith('/package.json'):
                info = json.loads(data)
                if isinstance(info.get('version'), str):
                    packages[name[len('node_modules/'):-len('/package.json')]] = info['version']
        if 'MANIFEST.json' not in z.namelist():
            raise SafetyError('Original MANIFEST.json is missing.')
        manifest = json.loads(z.read('MANIFEST.json'))
        actual = {item['path']: item for item in records if item['path'] != 'MANIFEST.json'}
        expected = {}
        if not isinstance(manifest.get('files'), list):
            raise SafetyError('Original MANIFEST files list is invalid.')
        for item in manifest['files']:
            if not isinstance(item, dict) or item.get('path') in expected:
                raise SafetyError('Original MANIFEST has duplicate or invalid entries.')
            expected[item.get('path')] = item
        if set(actual) != set(expected):
            raise SafetyError('Archive members differ from the original MANIFEST.')
        for name, record in actual.items():
            if record['sha256'] != expected[name].get('sha256') or record['size'] != expected[name].get('size'):
                raise SafetyError('Original MANIFEST verification failed: ' + name)
    return records, instructions, packages, {'passed': True, 'verified_entries': len(actual), 'scope': 'original_archive_manifest'}


def _fonts():
    roots = []
    if os.name == 'nt':
        roots = [Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts']
        local_appdata = os.environ.get('LOCALAPPDATA')
        if local_appdata:
            roots.append(Path(local_appdata) / 'Microsoft/Windows/Fonts')
    else:
        roots = [Path('/usr/share/fonts'), Path('/usr/local/share/fonts'), Path.home() / '.local/share/fonts', Path.home() / '.fonts']
    result, errors = [], []
    try:
        from PIL import ImageFont
    except ImportError:
        ImageFont = None
    seen = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob('*')):
            if path.suffix.lower() not in {'.ttf', '.otf', '.ttc', '.otc', '.fon'} or not path.is_file():
                continue
            name = str(path.resolve())
            if name in seen:
                continue
            seen.add(name)
            try:
                record = {'path': name, 'filename': path.name, 'size': path.stat().st_size, 'sha256': sha256(path),
                          'family': None, 'style': None}
                if ImageFont is not None:
                    try:
                        record['family'], record['style'] = ImageFont.truetype(str(path), size=12, index=0).getname()
                    except (OSError, ValueError):
                        pass
                result.append(record)
            except OSError:
                errors.append({'path': name, 'status': 'unreadable'})
    return {'files': result, 'errors': errors, 'bytes_copied': False,
            'scope': 'standard_system_and_user_font_directories; collection name is face index 0 only'}


def _environment(packages, successful_environment):
    versions = {}
    for name in ('PIL', 'numpy'):
        try:
            versions['Pillow' if name == 'PIL' else name] = importlib.import_module(name).__version__
        except (ImportError, AttributeError):
            versions['Pillow' if name == 'PIL' else name] = None
    node = shutil.which('node') or shutil.which('nodejs')
    node_version = None
    if node:
        try:
            run = subprocess.run([node, '--version'], capture_output=True, text=True, timeout=10, check=False)
            if run.returncode == 0:
                node_version = run.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'scope': 'current_capture_machine', 'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
            'python': {'version': sys.version, 'executable': sys.executable, 'implementation': platform.python_implementation()},
            'os': {'system': platform.system(), 'release': platform.release(), 'version': platform.version(), 'machine': platform.machine()},
            'python_packages_actual': versions, 'node_actual': {'path': node, 'version': node_version},
            'bundled_node_packages': packages, 'fonts': _fonts(), 'external_model_runtime': None, 'external_tool_runtime': None,
            'successful_environment': None if successful_environment is None else {
                'provenance': 'operator_supplied_not_independently_verified', 'value': successful_environment}}


def snapshot_baseline(archive, output, *, instructions=(), successful_environment=None):
    archive = reject_link_components(archive).resolve()
    if not archive.is_file():
        raise SafetyError('Original archive is missing.')
    output = require_new_output(output, [archive])
    before = sha256(archive)
    try:
        files, archive_instructions, packages, verified = _archive_records(archive)
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, UnicodeError) as exc:
        raise SafetyError('Original archive or MANIFEST is invalid.') from exc
    external = []
    for instruction in instructions:
        path = reject_link_components(instruction).resolve()
        if not path.is_file():
            raise SafetyError('Explicit instruction is missing: ' + str(path))
        external.append({'path': str(path), 'sha256': sha256(path), 'size': path.stat().st_size,
                         'provenance': 'operator_supplied_external_instruction'})
    if successful_environment is not None and not isinstance(successful_environment, dict):
        raise SafetyError('Successful environment must be an explicitly supplied JSON object.')
    environment = _environment(packages, successful_environment)
    if sha256(archive) != before or any(sha256(item['path']) != item['sha256'] for item in external):
        raise SafetyError('An original input changed while being fingerprinted.')
    output.mkdir(parents=True, exist_ok=False)
    (output / 'archive.sha256').write_text(before + '  ' + archive.name + '\n', encoding='utf-8')
    write_json(output / 'files.json', {'archive': {'path': str(archive), 'sha256': before, 'size': archive.stat().st_size},
                                     'files': files, 'manifest_verification': verified})
    write_json(output / 'environment.json', environment)
    write_json(output / 'instruction-fingerprints.json', {'archive_instructions': archive_instructions,
        'external_instructions': external, 'missing': [] if external else ['external_instructions_not_supplied']})
    (output / 'limitations.md').write_text(
        '# Baseline fingerprint limitations\n\n'
        'The original archive remains at its recorded path and is not extracted, modified or duplicated by this tool.\n'
        'Environment facts describe the current capture machine, not the original successful environment.\n'
        'A supplied successful environment is operator supplied and not independently verified.\n'
        'External model and tool runtime versions are unknown. No image/model service or Office was called.\n'
        'Installed font file hashes and discoverable names are recorded; font bytes are not copied.\n'
        'Font collections report name information for face index 0 only. Font fallback and Office rendering remain unverified.\n'
        'Package presence and archive integrity do not prove visual quality, runtime equivalence or speed.\n', encoding='utf-8')
    return {'output': str(output), 'archive_sha256': before, 'manifest_verification': verified}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--instruction', action='append', default=[])
    parser.add_argument('--successful-environment', help='Explicit JSON file supplied by operator')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(snapshot_baseline(args.archive, args.output, instructions=args.instruction,
            successful_environment=read_json(args.successful_environment) if args.successful_environment else None), ensure_ascii=False))
    except (SafetyError, OSError) as exc:
        parser.exit(2, 'Snapshot refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
