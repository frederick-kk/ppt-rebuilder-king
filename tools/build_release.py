"""Deterministic allowlisted source/runtime archives. Never edits its inputs."""
from pathlib import Path,PurePosixPath
from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
import argparse,hashlib,json,os,re,stat,time

ROOT_FILES={'README.zh-CN.md','README.md','PROJECT_INSTRUCTIONS.txt','LICENSE','NOTICE.md',
 'THIRD_PARTY_LICENSES.md','KNOWN_ISSUES.md','CHANGELOG.md','CONTRIBUTING.md','RELEASE.json','.gitignore'}
SOURCE_DIRS={'compat-runtime','engineering','tools','docs','examples','LICENSES','dependency','public-materials'}
RUNTIME_DIRS={'LICENSES','dependency'}
PRIVATE_PATH=re.compile(r'(?i)[A-Z]:[\\/]+Users[\\/]+(?!<USER>|Example(?:[\\/])|Public(?:[\\/]))[^\\/\s"<>]+[\\/]')
KEY=re.compile(r'(?<![\w-])sk-[A-Za-z0-9_-]{32,}|-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----')

def sha(data):return hashlib.sha256(data).hexdigest()
def encoded(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8')
def check_text(name,data):
    if name.lower().endswith(('.png','.jpg','.jpeg','.gif','.woff','.ttf')):return
    if name.lower().endswith('.pptx'):
        import io
        with ZipFile(io.BytesIO(data)) as z:
            for n in z.namelist():
                if n.endswith(('.xml','.rels')):check_text(name+'!'+n,z.read(n))
        return
    try:text=data.decode('utf-8')
    except UnicodeDecodeError:return
    if PRIVATE_PATH.search(text):raise ValueError('Public privacy check found a private user path: '+name)
    if KEY.search(text):raise ValueError('Public privacy check found a possible credential: '+name)

def files(root):
    """Read source inputs; dependencies are read separately from their pinned list."""
    for folder,dirs,names in os.walk(root,followlinks=False):
        dirs[:]=[d for d in dirs if d not in {'__pycache__','.git'}]
        if Path(folder)==root:dirs[:]=[d for d in dirs if d in SOURCE_DIRS]
        if Path(folder)==root/'compat-runtime':dirs[:]=[d for d in dirs if d!='node_modules']
        for name in [*dirs,*names]:
            path=Path(folder)/name;info=path.lstat()
            if path.is_symlink() or bool(getattr(info,'st_file_attributes',0)&0x400):
                raise ValueError('Release inputs cannot contain links/reparse points')
        for name in names:
            p=Path(folder)/name
            if p.suffix!='.pyc' and not name.startswith('.env'):yield p

def dependencies(source,data):
    """Package only approved dependency bytes, ignoring installer/dev extras."""
    try:manifest=json.loads(data['dependency/runtime-files.json'])
    except (KeyError,ValueError) as exc:raise ValueError('Missing/invalid dependency allowlist') from exc
    if manifest.get('format')!='ppt-rebuilder.runtime-dependencies/1' or not manifest.get('files'):
        raise ValueError('Invalid dependency allowlist format')
    if manifest.get('package_lock_sha256')!=sha(data.get('compat-runtime/package-lock.json',b'')):
        raise ValueError('Pinned dependency allowlist and package-lock.json disagree')
    modules=source/'compat-runtime/node_modules';result={};seen=set()
    for row in manifest['files']:
        name=row.get('path','');parts=PurePosixPath(name).parts
        if not name or name.startswith('/') or any(c in name for c in '\\:') or not parts or any(p in {'.','..'} for p in name.split('/')) or parts[0] in {'https','.bin'} or name.casefold() in seen:
            raise ValueError('Unsafe/duplicate dependency allowlist path')
        seen.add(name.casefold());path=modules.joinpath(*parts)
        try:
            for ancestor in [modules,*[modules.joinpath(*parts[:i]) for i in range(1,len(parts)+1)]]:
                info=ancestor.lstat()
                if ancestor.is_symlink() or bool(getattr(info,'st_file_attributes',0)&0x400):
                    raise ValueError('Pinned dependency cannot be a link/reparse point')
            body=path.read_bytes()
        except (FileNotFoundError,IsADirectoryError,NotADirectoryError) as exc:
            raise ValueError('Pinned dependency is missing: '+name) from exc
        if len(body)!=row.get('size') or sha(body)!=row.get('sha256'):
            raise ValueError('Pinned dependency content differs: '+name)
        result['compat-runtime/node_modules/'+name]=body
    return result

def inventory(data):return [{'path':n,'size':len(b),'sha256':sha(b)} for n,b in sorted(data.items())]

def pack(path,prefix,data,epoch):
    timestamp=time.gmtime(max(315532800,int(epoch)))[:6]
    with ZipFile(path,'x',compression=ZIP_DEFLATED,compresslevel=9) as z:
        for name,body in sorted(data.items()):
            zi=ZipInfo(prefix+'/'+name,timestamp);zi.create_system=3;zi.external_attr=0o100644<<16
            zi.compress_type=ZIP_DEFLATED;z.writestr(zi,body,compresslevel=9)

def build(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError('Build output must be a new directory outside source')
    if output.exists():raise FileExistsError('Build output already exists')
    meta=json.loads((source/'RELEASE.json').read_text(encoding='utf-8'));identity=meta['package_id']
    if (meta.get('distribution_mode')=='approved-complete-archive'
            or meta.get('archive_root')=='PPT Rebuilder King' and meta.get('runtime')):
        raise ValueError('This release preserves approved runtime bytes. Use tools/repack_runtime.py build --baseline <approved-archive.zip> --source <repository> --output <new-directory>; do not rebuild the runtime manifest.')
    if not re.fullmatch(r'[A-Za-z0-9 _.-]+',identity) or '..' in identity:raise ValueError('Unsafe package identity')
    for field in ['archive_name','source_archive_name']:
        if Path(meta[field]).name!=meta[field] or any(c in meta[field] for c in '/\\:') or not meta[field].endswith('.zip'):raise ValueError('Unsafe archive name')
    data={}
    for p in files(source):
        rel=p.relative_to(source).as_posix();top=rel.split('/')[0]
        if rel in ROOT_FILES or top in SOURCE_DIRS:data[rel]=p.read_bytes()
    data.update(dependencies(source,data))
    for n,b in data.items():
        if not n.startswith('compat-runtime/node_modules/'):check_text(n,b)
    version=data.get('compat-runtime/newpath/version.py',b'').decode('utf-8')
    if "PACKAGE_ID = '"+identity+"'" not in version or "ARCHIVE_NAME = '"+meta['archive_name']+"'" not in version:
        raise ValueError('Source release identity and version.py disagree')
    runtime={n[len('compat-runtime/'):]:b for n,b in data.items() if n.startswith('compat-runtime/')
             and n!='compat-runtime/MANIFEST.json' and not n.startswith('compat-runtime/node_modules/https/')}
    rows=inventory(runtime)
    runtime['MANIFEST.json']=encoded({'package_id':identity,'archive_name':meta['archive_name'],
       'format':'ppt-rebuilder.runtime-manifest/1','patch_id':meta['version'],
       'files':rows,'source_file_count':len(rows),
       'source_snapshot_sha256':sha(json.dumps(rows,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode())})
    user={n:b for n,b in data.items() if n in {'PROJECT_INSTRUCTIONS.txt','LICENSE','NOTICE.md','THIRD_PARTY_LICENSES.md','KNOWN_ISSUES.md','RELEASE.json'} or n.split('/')[0] in RUNTIME_DIRS}
    user.update({'compat-runtime/'+n:b for n,b in runtime.items()})
    user['README.zh-CN.md']=(f'# PPT Rebuilder King {meta["version"]}\n\n'
      f'用户运行包：{meta["archive_name"]}。上传本ZIP并使用根PROJECT_INSTRUCTIONS.txt。'
      '解压后读取compat-runtime/SKILL.md，作业放在程序目录之外。\n\n'
      '源码、测试与历史诊断不在本运行包中。Python/Pillow/NumPy/Node由宿主提供，JavaScript依赖已包含。'
      '复杂艺术字是独立图片，普通正文和支持的基础形状可原生编辑。Beta限制见KNOWN_ISSUES.md，许可见LICENSE与THIRD_PARTY_LICENSES.md。\n').encode('utf-8')
    public_source={n:b for n,b in data.items() if not n.startswith('compat-runtime/node_modules/') and n!='compat-runtime/MANIFEST.json'}
    for kind,mapping in [('runtime',user),('source',public_source)]:
        mapping['PACKAGE_MANIFEST.json']=encoded({'format':'ppt-rebuilder.public-package/1','kind':kind,
          'package_id':identity,'version':meta['version'],'files':inventory(mapping)})
    output.mkdir(parents=True)
    rp=output/meta['archive_name'];sp=output/meta['source_archive_name']
    pack(rp,identity,user,meta['source_date_epoch']);pack(sp,identity+'-source',public_source,meta['source_date_epoch'])
    sums=sha(rp.read_bytes())+'  '+rp.name+'\n'+sha(sp.read_bytes())+'  '+sp.name+'\n'
    (output/'SHA256SUMS.txt').write_text(sums,encoding='utf-8')
    (output/meta['archive_name'].replace('.zip','-项目指令.txt')).write_bytes(data['PROJECT_INSTRUCTIONS.txt'])
    return {'runtime':str(rp),'source':str(sp),'runtime_sha256':sha(rp.read_bytes()),'source_sha256':sha(sp.read_bytes()),'runtime_files':len(user),'source_files':len(public_source)}

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.source,a.output),ensure_ascii=True,indent=2))
if __name__=='__main__':main()
