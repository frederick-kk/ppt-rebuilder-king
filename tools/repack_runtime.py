"""Repackage the approved runtime bytes; build source separately, without generation.

The old runtime MANIFEST.json is evidence, not a build input to regenerate.
Only the outer package, documentation and public inventory are new.
"""
from pathlib import Path, PurePosixPath
from zipfile import ZipFile
import argparse
import json
import os
import stat

try:
    from .build_release import check_text, encoded, inventory, pack, sha
    from .verify_release import verify as verify_public
except ImportError:
    from build_release import check_text, encoded, inventory, pack, sha
    from verify_release import verify as verify_public

BASELINE_SHA256 = '3a1ecd3a377206b654e4d7c5ed15dfd05292b8603e9ec5109634a20e8d1f82ed'
BASELINE_ROOT = 'PPT Rebuilder King-20261025-quality-test'
RUNTIME_MANIFEST_SHA256 = '405898341da9631bdc5a60f5f7cf3ce04c2b3f02dc8a45756bc2f2381f1773ee'
RUNTIME_SNAPSHOT_SHA256 = 'bf24b73c1f5a47e1d4bc75ee77b214bdc8525009de61849a32b53e2d312379b6'
RUNTIME_COUNT = 423
ARCHIVE_ROOT = 'PPT Rebuilder King'
RUNTIME_NAME = 'PPT-Rebuilder-King-ChatGPT-20261105.zip'
SOURCE_NAME = 'PPT-Rebuilder-King-20261105-source.zip'
RUNTIME_DOCS = frozenset({
    'PROJECT_INSTRUCTIONS.txt', 'README.zh-CN.md', 'LICENSE', 'NOTICE.md',
    'THIRD_PARTY_LICENSES.md', 'KNOWN_ISSUES.md', 'RELEASE.json',
})
RUNTIME_LEGAL_FILES = frozenset({
    'LICENSES/README.md', 'LICENSES/SOURCE_INDEX.json', 'dependency/SBOM.json',
    *('LICENSES/node/' + n for n in (
        '@types-node-22.20.5-LICENSE', 'core-util-is-1.0.3-LICENSE',
        'image-size-2.0.4-LICENSE', 'immediate-3.0.6-LICENSE.txt',
        'inherits-2.0.4-LICENSE', 'isarray-1.0.0-README.md',
        'jszip-3.10.1-LICENSE.markdown', 'lie-3.3.0-license.md',
        'pako-1.0.11-LICENSE', 'pako-1.0.11-Zlib-NOTICE.txt',
        'pptxgenjs-4.0.1-LICENSE', 'process-nextick-args-2.0.1-license.md',
        'readable-stream-2.3.8-LICENSE', 'safe-buffer-5.1.2-LICENSE',
        'setimmediate-1.0.5-LICENSE.txt', 'string_decoder-1.1.1-LICENSE',
        'undici-types-6.21.0-LICENSE', 'util-deprecate-1.0.2-LICENSE',
    )),
})
SOURCE_ROOT_FILES = RUNTIME_DOCS | frozenset({
    'README.md', 'CHANGELOG.md', 'CONTRIBUTING.md', 'CODE_OF_CONDUCT.md',
    'SECURITY.md', 'SUPPORT.md', '.gitignore', '.gitattributes',
})
SOURCE_DOCS = frozenset({
    'docs/USAGE.zh-CN.md', 'docs/ARCHITECTURE.zh-CN.md',
    'docs/CHATGPT_PROJECT_INSTRUCTIONS.txt',
    'engineering/README.zh-CN.md', 'engineering/TESTING.zh-CN.md',
    'engineering/WARNINGS.zh-CN.md',
})
SOURCE_FIXTURES = frozenset({
    'engineering/tests/fixtures/headline-plans.json',
    'engineering/tests/fixtures/adaptive-board-current.json',
    'engineering/tests/fixtures/grid25-baseline/README.zh-CN.md',
    'engineering/tests/fixtures/grid25-baseline/PROVENANCE.json',
    'engineering/tests/fixtures/grid25-baseline/LICENSE',
    *('engineering/tests/fixtures/grid25-baseline/newpath/' + n + '.py'
      for n in ('__init__', 'source', 'ruler', 'storage', 'prompts')),
})
SKIP_DIRS = frozenset({
    '.git', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache',
    'node_modules', 'verification', 'baseline-record', 'public-materials',
})


def safe_member(name):
    """Reject Windows/POSIX aliases and traversal before any member is read."""
    if (not isinstance(name, str) or not name
            or any(c in name for c in '\\:<>"|?*')):
        raise ValueError('Unsafe archive path')
    parts = name.split('/')
    if (PurePosixPath(name).is_absolute() or any(p in {'', '.', '..'} for p in parts)
            or any(p.endswith(('.', ' ')) for p in parts)
            or any(ord(c) < 32 for c in name)
            or any(p.split('.')[0].casefold() in {
                'con', 'prn', 'aux', 'nul',
                *(f'com{i}' for i in range(1, 10)),
                *(f'lpt{i}' for i in range(1, 10)),
            } for p in parts)):
        raise ValueError('Unsafe archive path')
    return parts


def read_archive(path):
    """Read bounded regular files only; this tool never extracts input archives."""
    with ZipFile(path) as archive:
        members = archive.infolist()
        seen = set()
        if sum(i.file_size for i in members) > 512_000_000:
            raise ValueError('Archive exceeds inspection size budget')
        for member in members:
            safe_member(member.filename)
            folded = member.filename.casefold()
            if folded in seen:
                raise ValueError('Duplicate archive paths')
            seen.add(folded)
            mode = member.external_attr >> 16
            if (member.is_dir() or stat.S_ISLNK(mode)
                    or (stat.S_IFMT(mode) not in (0, stat.S_IFREG))
                    or member.file_size > 100_000_000 or member.flag_bits & 1):
                raise ValueError('Unsafe archive member type/size')
        roots = {n.split('/')[0] for n in seen}
        if len(roots) != 1 or archive.testzip() is not None:
            raise ValueError('Archive root/CRC invalid')
        return {i.filename: archive.read(i) for i in members}


def validate_runtime(runtime):
    if len(runtime) != RUNTIME_COUNT:
        raise ValueError('Approved runtime inventory count differs')
    raw = runtime.get('MANIFEST.json', b'')
    if sha(raw) != RUNTIME_MANIFEST_SHA256:
        raise ValueError('Approved runtime manifest differs')
    manifest = json.loads(raw)
    rows = manifest['files']
    if (manifest.get('format') != 'newpath.package-manifest/1'
            or manifest.get('package_id') != BASELINE_ROOT
            or manifest.get('source_file_count') != len(rows)
            or len(rows) != RUNTIME_COUNT - 1):
        raise ValueError('Approved runtime identity/inventory differs')
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                           separators=(',', ':')).encode('utf-8')
    if (sha(canonical) != RUNTIME_SNAPSHOT_SHA256
            or manifest.get('source_snapshot_sha256') != RUNTIME_SNAPSHOT_SHA256):
        raise ValueError('Approved runtime snapshot differs')
    declared = set()
    for row in rows:
        name = row['path']
        safe_member(name)
        if name.casefold() in declared:
            raise ValueError('Duplicate runtime manifest paths')
        declared.add(name.casefold())
        body = runtime.get(name)
        if body is None or len(body) != row['size'] or sha(body) != row['sha256']:
            raise ValueError('Approved runtime member differs: ' + name)
    if declared != {n.casefold() for n in runtime if n != 'MANIFEST.json'}:
        raise ValueError('Approved runtime manifest inventory differs')


def approved_runtime(baseline):
    baseline = Path(baseline)
    if baseline.stat().st_size > 512_000_000:
        raise ValueError('Baseline exceeds inspection size budget')
    files = read_archive(baseline)
    if sha(baseline.read_bytes()) == BASELINE_SHA256:
        if any(not n.startswith(BASELINE_ROOT + '/') for n in files):
            raise ValueError('Approved baseline root differs')
        prefix = BASELINE_ROOT + '/compat-runtime/'
        runtime = {n[len(prefix):]: b for n, b in files.items() if n.startswith(prefix)}
    elif all(n.startswith(ARCHIVE_ROOT + '/') for n in files):
        verify_public(baseline)
        runtime = public_runtime(files)
    else:
        raise ValueError('Baseline SHA256 differs from the approved download; provide the clean runtime ZIP')
    validate_runtime(runtime)
    return runtime


def source_allowed(name):
    parts = safe_member(name)
    if any(p in SKIP_DIRS for p in parts) or any(p.startswith('.env') for p in parts):
        return False
    if name in SOURCE_ROOT_FILES | SOURCE_DOCS | SOURCE_FIXTURES | RUNTIME_LEGAL_FILES:
        return True
    if parts[0] == 'compat-runtime':
        return name != 'compat-runtime/MANIFEST.json' and (
            len(parts) == 2 and parts[-1] in {
                'LICENSE', 'SKILL.md', 'README.md', 'requirements.txt', 'pyproject.toml',
                'package.json', 'package-lock.json'
            } or len(parts) == 3 and parts[1] == 'newpath' and name.endswith(('.py', '.js', '.cjs'))
            or name == 'compat-runtime/references/scene.md')
    if parts[0] == 'tools':
        return len(parts) == 2 and name.endswith('.py')
    if parts[0] == 'engineering':
        return name.endswith('.py') and (
            len(parts) == 2 or len(parts) == 3 and parts[1] == 'tests')
    if parts[0] == 'dependency':
        return len(parts) == 2 and parts[-1] in {'SBOM.json', 'runtime-files.json'}
    if parts[:2] == ['examples', 'synthetic-basic']:
        return len(parts) == 3 and parts[-1] in {
            'README.md', 'layout.json', 'input.png', 'expected-output.pptx'}
    return False


def read_source(source):
    result = {}
    for folder, dirs, names in os.walk(source, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        if Path(folder) == source:
            dirs[:] = [d for d in dirs if d in {
                'compat-runtime', 'engineering', 'tools', 'docs', 'examples',
                'LICENSES', 'dependency'}]
        for name in [*dirs, *names]:
            path = Path(folder) / name
            info = path.lstat()
            if path.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Release source cannot contain links/reparse points')
        for name in sorted(names):
            path = Path(folder) / name
            rel = path.relative_to(source).as_posix()
            if source_allowed(rel):
                body = path.read_bytes()
                check_text(rel, body)
                result[rel] = body
    return result


def add_public_manifest(data, kind, meta, baseline_sha256):
    manifest = {
        'format': 'ppt-rebuilder.public-package/1', 'kind': kind,
        'package_id': BASELINE_ROOT, 'archive_root': ARCHIVE_ROOT if kind == 'runtime'
        else ARCHIVE_ROOT + '-source', 'version': meta['version'],
        'baseline_archive_sha256': baseline_sha256,
        'runtime_origin_archive_sha256': BASELINE_SHA256,
        'runtime_manifest_sha256': RUNTIME_MANIFEST_SHA256,
        'runtime_source_snapshot_sha256': RUNTIME_SNAPSHOT_SHA256,
        'files': inventory(data),
    }
    if kind == 'runtime':
        manifest['runtime_files_preserved'] = RUNTIME_COUNT
    else:
        manifest['runtime_dependencies_bundled'] = False
    data['PACKAGE_MANIFEST.json'] = encoded(manifest)


def runtime_readme(meta):
    """User instructions independent of developer-only repository navigation."""
    return f'''# PPT Rebuilder King · {meta['version']}

将单页截图重建为对象可编辑的 PowerPoint。普通正文和支持的基础图形使用原生对象，艺术字、图标和复杂插画使用独立图片。

## 上传到 ChatGPT 项目

1. 保留下载的运行 ZIP，不用解压或改名。在 ChatGPT 网页版新建项目，记忆设置选择「仅限本项目」。
2. 将整个 ZIP 上传到项目「来源」。打开配套项目指令 TXT，或本包根目录的 [PROJECT_INSTRUCTIONS.txt](PROJECT_INSTRUCTIONS.txt)，复制全文到「项目设置」→「项目指令」并保存。
3. 在项目中新开对话，选择具备文件处理、代码执行和生图能力的模型，上传一张单页高清截图，发送：

   > 请读取本项目指定的 ZIP，按项目指令和包内工作流，将这张截图还原成可编辑 PPTX。

4. 下载返回的 PPTX，用 WPS 或 PowerPoint 打开并逐项检查。实际可用模型和工具以账号为准。

无需在本地 Codex 中安装。本包的运行入口和完整执行规则见 [compat-runtime/SKILL.md](compat-runtime/SKILL.md)。每次输入使用程序目录之外的新作业目录；继续任务时保留同一目录和已有回图。

## 能力与限制

- 工作空间为 1920×1080，辅助尺为 25/50/5，用于定位，不让对象吸附网格。
- 复杂艺术字图片可以移动、缩放，其内部文字不能逐字编辑；基础形状和普通正文的编辑范围以实际输出为准。
- 流程需要宿主实际调用图像工具、查看并登记回图；每项任务请求一次调用、一张图片。不能用离线检查代替真实图像或 Office 效果验收。
- Python、Node.js、Pillow、NumPy 和字体由宿主提供。详细限制见 [KNOWN_ISSUES.md](KNOWN_ISSUES.md)。

## 文件与许可

程序、依赖及工作流在 `compat-runtime/`。本版只整理发行结构，全部 423 个运行文件保持不变；内部程序身份仍为 `PPT Rebuilder King-20261025-quality-test`。完整文件清单见 `PACKAGE_MANIFEST.json`。

第一方源码和文档采用 [MIT License](LICENSE)，第三方依赖各自适用原许可。详见 [NOTICE.md](NOTICE.md)、[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)、[许可原文](LICENSES/README.md) 与 [依赖清单](dependency/SBOM.json)。用户输入及生成素材的使用范围须按其实际来源与授权确定。
'''.encode('utf-8')


def public_runtime(files):
    """Validate strict public envelope and immutable runtime provenance."""
    prefix = ARCHIVE_ROOT + '/'
    if any(not n.startswith(prefix) for n in files):
        raise ValueError('Distribution archive root differs')
    relative = {n[len(prefix):]: b for n, b in files.items()}
    outer = {n for n in relative if not n.startswith('compat-runtime/')}
    if outer != RUNTIME_DOCS | RUNTIME_LEGAL_FILES | {'PACKAGE_MANIFEST.json'}:
        raise ValueError('Runtime outer inventory differs from the strict allowlist')
    actual = {n[len('compat-runtime/'):]: b for n, b in relative.items()
              if n.startswith('compat-runtime/')}
    validate_runtime(actual)
    manifest = json.loads(relative['PACKAGE_MANIFEST.json'])
    if (manifest.get('kind') != 'runtime'
            or manifest.get('package_id') != BASELINE_ROOT
            or manifest.get('runtime_origin_archive_sha256') != BASELINE_SHA256
            or not isinstance(manifest.get('baseline_archive_sha256'), str)
            or len(manifest['baseline_archive_sha256']) != 64
            or any(c not in '0123456789abcdef' for c in manifest['baseline_archive_sha256'])
            or manifest.get('runtime_manifest_sha256') != RUNTIME_MANIFEST_SHA256
            or manifest.get('runtime_source_snapshot_sha256') != RUNTIME_SNAPSHOT_SHA256
            or manifest.get('runtime_files_preserved') != RUNTIME_COUNT):
        raise ValueError('Public manifest provenance differs')
    return actual


def verify_runtime(baseline, runtime_archive):
    expected = approved_runtime(baseline)
    result = verify_public(Path(runtime_archive))
    actual = public_runtime(read_archive(runtime_archive))
    if actual != expected:
        raise ValueError('Runtime bytes differ from the approved download')
    return {**result, 'runtime_files_compared': len(actual),
            'verification_baseline_sha256': sha(Path(baseline).read_bytes()),
            'runtime_byte_identical_to_approved_download': True,
            'runtime_manifest_sha256': sha(actual['MANIFEST.json']),
            'excluded_original_outer_files': 352}


def verify_source(source_archive):
    result = verify_public(Path(source_archive))
    files = read_archive(source_archive)
    prefix = ARCHIVE_ROOT + '-source/'
    if any(not n.startswith(prefix) for n in files):
        raise ValueError('Source archive root differs')
    for name in files:
        relative = name[len(prefix):]
        if relative != 'PACKAGE_MANIFEST.json' and not source_allowed(relative):
            raise ValueError('Source archive includes a non-allowlisted file: ' + relative)
    manifest = json.loads(files[prefix + 'PACKAGE_MANIFEST.json'])
    if manifest.get('kind') != 'source':
        raise ValueError('Source archive kind differs')
    return result


def build(baseline, source, output):
    baseline = Path(baseline).resolve()
    source = Path(source).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError('Build output already exists; use a new directory')
    if output.is_relative_to(source) or source.is_relative_to(output) or baseline.is_relative_to(output):
        raise ValueError('Build output must be outside source and baseline inputs')
    runtime = approved_runtime(baseline)
    public_source = read_source(source)
    missing = (RUNTIME_DOCS | RUNTIME_LEGAL_FILES) - public_source.keys()
    if missing:
        raise ValueError('Missing required public document: ' + ', '.join(sorted(missing)))
    meta = json.loads(public_source['RELEASE.json'])
    if (meta.get('package_id') != BASELINE_ROOT
            or meta.get('archive_root') != ARCHIVE_ROOT
            or meta.get('archive_name') != RUNTIME_NAME
            or meta.get('source_archive_name') != SOURCE_NAME):
        raise ValueError('Release distribution metadata differs')
    instructions_name = meta.get('project_instructions_name',
                                 'PPT-Rebuilder-King-ChatGPT-20261105-项目指令.txt')
    sums_name = meta.get('sha256sums_name', 'SHA256SUMS.txt')
    for name in (instructions_name, sums_name):
        if len(safe_member(name)) != 1:
            raise ValueError('Unsafe companion filename')
    if len({n.casefold() for n in (RUNTIME_NAME, SOURCE_NAME, instructions_name, sums_name)}) != 4:
        raise ValueError('Companion filenames collide with release archives')
    epoch = int(meta['source_date_epoch'])
    user = {n: public_source[n] for n in RUNTIME_DOCS | RUNTIME_LEGAL_FILES}
    user['README.zh-CN.md'] = runtime_readme(meta)
    user.update({'compat-runtime/' + n: b for n, b in runtime.items()})
    baseline_sha256 = sha(baseline.read_bytes())
    add_public_manifest(user, 'runtime', meta, baseline_sha256)
    add_public_manifest(public_source, 'source', meta, baseline_sha256)
    # All input validation precedes creating a fresh output directory.
    output.mkdir(parents=True, exist_ok=False)
    rp = output / RUNTIME_NAME
    sp = output / SOURCE_NAME
    pack(rp, ARCHIVE_ROOT, user, epoch)
    pack(sp, ARCHIVE_ROOT + '-source', public_source, epoch)
    runtime_check = verify_runtime(baseline, rp)
    source_check = verify_source(sp)
    ip = output / instructions_name
    ip.write_bytes(user['PROJECT_INSTRUCTIONS.txt'])
    sums = ''.join(sha(p.read_bytes()) + '  ' + p.name + '\n' for p in (rp, sp, ip))
    (output / sums_name).write_bytes(sums.encode('utf-8'))
    return {'runtime': str(rp), 'source': str(sp), 'instructions': str(ip),
            'checksums': str(output / sums_name),
            'runtime_check': runtime_check, 'source_check': source_check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    make = commands.add_parser('build')
    make.add_argument('--baseline', type=Path, required=True)
    make.add_argument('--source', type=Path, required=True)
    make.add_argument('--output', type=Path, required=True)
    verify = commands.add_parser('verify')
    verify.add_argument('--baseline', type=Path, required=True)
    verify.add_argument('--runtime', type=Path, required=True)
    verify.add_argument('--source-archive', type=Path)
    args = parser.parse_args()
    if args.command == 'build':
        result = build(args.baseline, args.source, args.output)
    else:
        result = {'runtime': verify_runtime(args.baseline, args.runtime)}
        if args.source_archive:
            result['source'] = verify_source(args.source_archive)
    print(json.dumps(result, ensure_ascii=True, indent=2))


if __name__ == '__main__':
    main()
