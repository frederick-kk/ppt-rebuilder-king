"""Verify archive paths, exact inventories, hashes and public privacy patterns."""
from pathlib import Path,PurePosixPath
from zipfile import ZipFile
import argparse,hashlib,json,stat
try:from .build_release import check_text
except ImportError:from build_release import check_text

def verify(path):
    with ZipFile(path) as z:
        members=z.infolist();names=[i.filename for i in members]
        if len(names)!=len(set(n.casefold() for n in names)):raise ValueError('Duplicate archive paths')
        if sum(i.file_size for i in members)>512_000_000 or any(i.file_size>100_000_000 for i in members):raise ValueError('Archive exceeds inspection size budget')
        for i in members:
            p=PurePosixPath(i.filename)
            if p.is_absolute() or '..' in p.parts or '\\' in i.filename or ':' in i.filename or stat.S_ISLNK(i.external_attr>>16):raise ValueError('Unsafe archive path')
        roots={n.split('/')[0] for n in names}
        if len(roots)!=1 or z.testzip() is not None:raise ValueError('Archive root/CRC invalid')
        prefix=next(iter(roots))+'/'
        manifest=json.loads(z.read(prefix+'PACKAGE_MANIFEST.json'))
        if manifest.get('format')!='ppt-rebuilder.public-package/1':raise ValueError('Unsupported public manifest')
        def inspect(base,rows,excluded):
            declared={base+r['path'] for r in rows}
            if len(declared)!=len(rows):raise ValueError('Duplicate manifest rows')
            actual={n for n in names if n.startswith(base) and n not in excluded}
            if declared!=actual:raise ValueError('Manifest inventory differs')
            for r in rows:
                b=z.read(base+r['path'])
                if len(b)!=r['size'] or hashlib.sha256(b).hexdigest()!=r['sha256']:raise ValueError('Manifest content hash mismatch')
        inspect(prefix,manifest['files'],{prefix+'PACKAGE_MANIFEST.json'})
        if manifest['kind']=='runtime':
            base=prefix+'compat-runtime/';raw=z.read(base+'MANIFEST.json');runtime=json.loads(raw)
            inspect(base,runtime['files'],{base+'MANIFEST.json'})
            snapshot=hashlib.sha256(json.dumps(runtime['files'],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            if snapshot!=runtime['source_snapshot_sha256'] or len(runtime['files'])!=runtime['source_file_count']:raise ValueError('Runtime snapshot invalid')
        for n in names:
            rel=n[len(prefix):]
            if rel.startswith(('verification/','baseline-record/')) or rel.endswith('.env'):raise ValueError('Private archive material included')
            if not rel.startswith('compat-runtime/node_modules/'):check_text(rel,z.read(n))
        return {'valid':True,'kind':manifest['kind'],'files':len(names),'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'scope':'inventory/hash and bounded content-pattern checks; not legal or semantic certification'}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('archive',type=Path);a=p.parse_args();print(json.dumps(verify(a.archive),ensure_ascii=True,indent=2))
