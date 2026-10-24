"""Exercise the real CLI using deterministic synthetic PNG fixtures.

No image provider is called. The simulated host review is not human inspection,
provider evidence, image fidelity acceptance, or Office editing acceptance.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from xml.etree import ElementTree as ET
from zipfile import ZipFile

PAGE=(1920,1080)
TITLE='GLOW DEMO'
BODY=['Editable text stays selectable','Two art images, a shape and a line']
TITLE_BOX=[180,110,1350,200]
BADGE_BOX=[1320,710,240,240]
TOOL='synthetic-raster-fixture-no-provider'
MACHINE_PATH=re.compile(r'(?i)(?<![a-z0-9])[a-z]:[\\/]|/(?:Users|home|tmp|private|mnt)/')
BINARY_PRIVATE_PATH=re.compile(r'(?i)(?<![a-z0-9])[a-z]:[\\/](?:Users|Documents and Settings|home)[\\/]|/(?:Users|home|tmp|private|mnt)/')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def _clean_semantics(name,data):
    root=ET.fromstring(data)
    if re.fullmatch(r'ppt/slides/slide\d+\.xml',name):
        for element in root.iter():
            if element.tag.rsplit('}',1)[-1]=='cNvPr':element.attrib.pop('descr',None)
    elif name=='docProps/core.xml':
        for element in list(root):
            if element.tag.rsplit('}',1)[-1] in {'creator','lastModifiedBy','created','modified'}:
                root.remove(element)
    return ET.tostring(root,encoding='utf-8')


def scan_pptx_privacy(path,forbidden_values=()):
    """Read every ZIP member; return categories/part names, never private text."""
    issues=[];xml_parts=0
    needles=[str(v).encode('utf-8') for v in forbidden_values if str(v)]
    with ZipFile(path) as archive:
        if archive.testzip():raise ValueError('Public PPTX has an invalid ZIP member.')
        names=archive.namelist()
        if len(set(names))!=len(names):raise ValueError('Duplicate PPTX member names are not accepted.')
        for name in names:
            data=archive.read(name)
            if MACHINE_PATH.search(name):issues.append({'part':name,'category':'machine_path_in_member_name'})
            if any(needle in data for needle in needles):
                issues.append({'part':name,'category':'forbidden_private_work_path'})
            decoded=data.decode('utf-8',errors='replace')
            textual=name.endswith(('.xml','.rels','.txt','.json','.svg'))
            # Compressed pixel bytes often contain accidental drive-letter
            # triplets. Text parts get the broad check; every other member is
            # still checked for concrete private-directory patterns and exact
            # work-path bytes, without rewriting any media.
            pattern=MACHINE_PATH if textual else BINARY_PRIVATE_PATH
            if pattern.search(decoded):issues.append({'part':name,'category':'machine_absolute_path'})
            if re.search(r'(?i)(?<![a-z0-9])sk-[a-z0-9_-]{16,}',decoded):
                issues.append({'part':name,'category':'credential_literal_candidate'})
            if name.endswith(('.xml','.rels')):
                xml_parts+=1;root=ET.fromstring(data)
                if name.endswith('.rels'):
                    for element in root:
                        if element.attrib.get('TargetMode')=='External':
                            issues.append({'part':name,'category':'external_relationship_in_synthetic_example'})
    return {'members_scanned':len(names),'xml_parts_scanned':xml_parts,'issues':issues,
            'scope':'All decompressed ZIP members; machine-path/credential patterns and external XML relationships. Not a universal secret detector.'}


def sanitize_public_pptx(source,destination,work_path=None):
    """Publish a separate copy after narrow metadata edits and full-part checks."""
    source=Path(source);destination=Path(destination)
    if destination.exists():raise FileExistsError('Refusing to replace an existing public example.')
    if source.resolve()==destination.resolve():raise ValueError('Public copy must differ from the private original.')
    original_sha=sha(source);changes=[];buffer=io.BytesIO()
    with ZipFile(source) as original,ZipFile(buffer,'w') as public:
        for entry in original.infolist():
            data=original.read(entry.filename);updated=data
            if re.fullmatch(r'ppt/slides/slide\d+\.xml',entry.filename):
                content=data.decode('utf-8')
                def clean_description(match):
                    value=html.unescape(match.group(3))
                    if MACHINE_PATH.search(value) or value.startswith('/'):
                        changes.append({'part':entry.filename,'field':'cNvPr@descr','action':'replace machine path with generic synthetic-art description'})
                        return match.group(1)+match.group(2)+'Synthetic fixture artwork'+match.group(2)
                    return match.group(0)
                content=re.sub(r'(<(?:[\w]+:)?cNvPr\b[^>]*?\sdescr=)([\"\x27])(.*?)\2',clean_description,content,flags=re.S)
                updated=content.encode('utf-8')
            elif entry.filename=='docProps/core.xml':
                content=data.decode('utf-8')
                for tag,value in [('dc:creator','PPT Rebuilder King synthetic example'),
                                  ('cp:lastModifiedBy','PPT Rebuilder King synthetic example'),
                                  ('dcterms:created','2026-10-24T00:00:00Z'),
                                  ('dcterms:modified','2026-10-24T00:00:00Z')]:
                    pattern=r'(<'+re.escape(tag)+r'\b[^>]*>)(.*?)(</'+re.escape(tag)+r'>)'
                    content,count=re.subn(pattern,lambda m:m.group(1)+value+m.group(3),content,flags=re.S)
                    if count:changes.append({'part':entry.filename,'field':tag,'action':'set generic synthetic-example metadata'})
                updated=content.encode('utf-8')
            if updated!=data:
                if _clean_semantics(entry.filename,data)!=_clean_semantics(entry.filename,updated):
                    raise ValueError('Public-copy sanitation changed non-metadata XML semantics.')
            public.writestr(entry,updated)
    # Check in memory before publishing so an unapproved part never leaks.
    payload=buffer.getvalue()
    privacy=scan_pptx_privacy(io.BytesIO(payload),[work_path] if work_path else [])
    if privacy['issues']:raise ValueError('Public PPTX privacy scan found an unapproved private path or part.')
    with ZipFile(source) as original,ZipFile(io.BytesIO(payload)) as public:
        if original.namelist()!=public.namelist():raise ValueError('Public-copy package members changed.')
        for name in original.namelist():
            before,after=original.read(name),public.read(name)
            if before!=after and _clean_semantics(name,before)!=_clean_semantics(name,after):
                raise ValueError('Image, text or geometry changed while sanitizing the public copy.')
    if sha(source)!=original_sha:raise ValueError('Private original changed while making its public copy.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as output:output.write(payload)
    return {'private_original_sha256':original_sha,'public_copy_sha256':sha(destination),
            'changes':changes,'geometry_text_and_media_unchanged':True,
            'private_original_unchanged':True,'privacy_scan':privacy}


def synthetic_layout():
    return {'format':'newpath.scene/1','items':[
        {'id':'demo-title','kind':'art','box':TITLE_BOX,'lettering':TITLE,
         'description':'Synthetic blue outlined artistic title GLOW DEMO, drawn by this fixture program.'},
        {'id':'body-one','kind':'text','box':[180,420,1500,90],'text':BODY[0],'size':48,'color':'20385C'},
        {'id':'body-two','kind':'text','box':[180,535,1500,70],'text':BODY[1],'size':36,'color':'40506A'},
        {'id':'demo-panel','kind':'shape','box':[180,720,650,150],'shape':'roundRect',
         'fill':'DCE8FF','stroke':'5376BF','stroke_width':3},
        {'id':'demo-line','kind':'line','points':[180,650,1730,650],'stroke':'7B91B5','stroke_width':3},
        {'id':'orange-badge','kind':'art','box':BADGE_BOX,
         'description':'Synthetic orange circular badge with white concentric rings and central diamond; no lettering.'}]}


def _text_pixels(value,color,size):
    from PIL import Image,ImageDraw,ImageFont
    # New Pillow versions expose a scalable built-in font; older supported
    # versions still have a bitmap fallback and need no downloaded font file.
    try:font=ImageFont.load_default(size=64)
    except TypeError:font=ImageFont.load_default()
    box=ImageDraw.Draw(Image.new('RGBA',(1,1))).textbbox((0,0),value,font=font,stroke_width=1)
    small=Image.new('RGBA',(box[2]-box[0]+8,box[3]-box[1]+8),(0,0,0,0))
    ImageDraw.Draw(small).text((4-box[0],4-box[1]),value,font=font,fill=color,stroke_width=1,stroke_fill='white')
    return small.resize(tuple(size),Image.Resampling.LANCZOS)


def _badge(size):
    from PIL import Image,ImageDraw
    image=Image.new('RGBA',(size[0]*2,size[1]*2),(0,0,0,0));draw=ImageDraw.Draw(image)
    width,height=image.size
    draw.ellipse((8,8,width-9,height-9),fill='#F3972C',outline='#C56B14',width=7)
    draw.ellipse((65,65,width-66,height-66),outline='white',width=12)
    draw.polygon([(width//2,118),(width-118,height//2),(width//2,height-118),(118,height//2)],fill='#FFF5DE')
    return image.resize(tuple(size),Image.Resampling.LANCZOS)


def draw_fixture_inputs(work):
    from PIL import Image,ImageDraw
    work=Path(work);assets=work/'synthetic-returns';assets.mkdir()
    background=Image.new('RGB',PAGE)
    draw=ImageDraw.Draw(background)
    for y in range(PAGE[1]):
        draw.line((0,y,PAGE[0],y),fill=(242-y//100,248-y//140,255-y//160))
    foreground=Image.new('RGBA',PAGE,(0,0,0,0))
    title=_text_pixels(TITLE,'#3453C4',TITLE_BOX[2:]);badge=_badge(BADGE_BOX[2:])
    foreground.alpha_composite(title,tuple(TITLE_BOX[:2]));foreground.alpha_composite(badge,tuple(BADGE_BOX[:2]))
    source=Image.alpha_composite(background.convert('RGBA'),foreground)
    draw=ImageDraw.Draw(source)
    draw.rounded_rectangle((180,720,830,870),radius=25,fill='#DCE8FF',outline='#5376BF',width=3)
    draw.line((180,650,1730,650),fill='#7B91B5',width=3)
    for item in synthetic_layout()['items']:
        if item['kind']=='text':
            width=round(item['size']*0.52*len(item['text']))
            source.alpha_composite(_text_pixels(item['text'],'#'+item['color'],(width,round(item['size']*1.1))),tuple(item['box'][:2]))
    source.convert('RGB').save(work/'input.png')
    background.save(assets/'background.png');foreground.save(assets/'foreground.png')
    title_board=Image.new('RGBA',(1536,1024),(0,0,0,0))
    title_board.alpha_composite(title.resize((1296,192),Image.Resampling.LANCZOS),(120,416))
    title_board.save(assets/'wordart.png')
    return assets


def _review(task,path):
    review={'role':task['role'],'output_sha256':sha(path),'looked_at_return':True,'matches_task':True,
            'observed':'Synthetic automated fixture: this program constructed the assigned '+task['role']+' pixels. This is a simulated host declaration, not human visual inspection or provider evidence.',
            'notes':['Deterministic program-made test PNG; no model image generation.']}
    if task['role']=='background':review['foreground_text_absent']=True
    elif task['role']=='foreground':review['page_elements_match']=True
    elif task['role']=='wordart':review.update(lettering_matches=True,only_assigned_title=True)
    elif task['role']=='board':review.update(matched_members=[m['id'] for m in task['members']],ordinary_text_removed=True)
    return review


def _inspect_output(path):
    ns={'p':'http://schemas.openxmlformats.org/presentationml/2006/main',
        'a':'http://schemas.openxmlformats.org/drawingml/2006/main'}
    with ZipFile(path) as archive:
        root=ET.fromstring(archive.read('ppt/slides/slide1.xml'))
        text=[e.text or '' for e in root.findall('.//a:t',ns)]
        for expected in BODY:
            if expected not in text:raise ValueError('Synthetic body text is missing from native PowerPoint XML.')
        shapes=root.findall('.//p:sp',ns);lines=root.findall('.//p:cxnSp',ns);pictures=root.findall('.//p:pic',ns)
        # PptxGenJS represents its editable line as p:sp + prstGeom=line;
        # connector objects are another valid native-line representation.
        line_shapes=[shape for shape in shapes
                     if any(g.get('prst')=='line' for g in shape.findall('./p:spPr/a:prstGeom',ns))]
        line_count=len(lines)+len(line_shapes)
        if len(shapes)-len(line_shapes)<3 or line_count<1 or len(pictures)!=2:
            raise ValueError('Synthetic PowerPoint lacks the two native texts, shape/line, or two artwork pictures.')
    return {'native_body_texts_present':2,'shape_elements':len(shapes),'native_line_elements':line_count,
            'artwork_picture_elements':len(pictures),'office_editing_test_performed':False}


def run_smoke(runtime,work,example_dir=None):
    from PIL import Image
    runtime=Path(runtime).resolve();work=Path(work).resolve()
    if not(runtime/'newpath/__main__.py').is_file():raise ValueError('--runtime must name an extracted compat-runtime directory.')
    if work.is_relative_to(runtime):raise ValueError('Smoke work must be outside the runtime directory.')
    if work.exists() and any(work.iterdir()):raise ValueError('Smoke work must be a new empty directory; existing evidence is preserved.')
    work.mkdir(parents=True,exist_ok=True)
    public=Path(example_dir).resolve() if example_dir else work/'public-example'
    if public.exists() and any(public.iterdir()):raise ValueError('Public example destination must be empty; nothing is overwritten.')
    if public.is_relative_to(runtime):raise ValueError('Public example must be outside the immutable runtime.')
    assets=draw_fixture_inputs(work);job=work/'job';trace=work/'cli-trace';trace.mkdir()
    steps=[];begun=[];repeated=0
    def cli(*,source=None,data=None):
        index=len(steps);command=[sys.executable,'-B','-m','newpath','--work',str(job)]
        if source is not None:command.extend(['--source',str(source)])
        if data is not None:
            path=trace/f'{index:02d}-input.json';write_json(path,data);command.extend(['--input',str(path)])
        environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8'}
        result=subprocess.run(command,cwd=runtime,capture_output=True,text=True,encoding='utf-8',timeout=180,env=environment)
        try:response=json.loads(result.stdout)
        except ValueError as exc:raise RuntimeError('Smoke CLI did not return JSON; preserve its private work directory.') from exc
        write_json(trace/f'{index:02d}-output.json',response)
        steps.append({'index':index,'kind':response.get('kind'),'exit_code':result.returncode})
        if result.returncode or response.get('kind')=='error':
            raise RuntimeError('Smoke CLI stopped: '+str(response.get('message','unknown error')))
        return response
    response=cli(source=work/'input.png')
    for _ in range(20):
        kind=response.get('kind')
        if kind=='done':break
        if kind=='describe':
            response=cli(data={'kind':'layout','layout':synthetic_layout()});continue
        if kind!='generate':raise RuntimeError('Synthetic smoke paused unexpectedly: '+str(kind))
        tasks=response['jobs']
        for task in tasks:
            begin=copy.deepcopy(task['begin_template']);begin['request']['tool']=TOOL
            permit=cli(data=begin)
            if permit.get('kind')!='invoke_once':raise RuntimeError('A new synthetic task did not receive its first local permit.')
            duplicate=cli(data=begin)
            if duplicate.get('kind')!='await_results' or duplicate.get('generate_more') is not False:
                raise RuntimeError('Repeated begin unexpectedly permits another invocation.')
            repeated+=1;begun.append({'task_id':task['id'],'role':task['role']})
            if task['role']=='board':
                # The deterministic fixture keeps the prepared geometry and
                # changes visible RGB. It is explicitly not a provider return.
                path=assets/(task['id']+'.png')
                with Image.open(task['references'][0]) as image:
                    rgba=image.convert('RGBA');pixels=rgba.load()
                    for y in range(rgba.height):
                        for x in range(rgba.width):
                            red,green,blue,alpha=pixels[x,y]
                            if alpha:pixels[x,y]=(min(255,red+3),green,max(0,blue-2),alpha)
                    rgba.save(path)
            else:path=assets/(task['role']+'.png')
            payload={**permit['result_template'],'file':str(path),'tool':TOOL,
                     'request_sha256':permit['request_sha256'],'invocation_count':None,'output_count':1,
                     'executed_prompt':None,'role_review':_review(task,path)}
            response=cli(data=payload)
    else:raise RuntimeError('Synthetic smoke exceeded its bounded controller transitions.')
    if response.get('kind')!='done':raise RuntimeError('Synthetic smoke did not reach done.')
    if len(begun)!=4 or {e['role'] for e in begun}!={'background','foreground','wordart','board'}:
        raise ValueError('This fixture must exercise exactly four assigned tasks and both artwork routes.')
    if response.get('caller_reported_invocations') is not None:
        raise ValueError('A fixture must not claim actual provider invocation counts.')
    if response.get('skipped'):raise ValueError('Synthetic smoke unexpectedly skipped an output item.')
    private=Path(response['pptx']);inspection=_inspect_output(private);private_sha=sha(private)
    public.mkdir(parents=True,exist_ok=True)
    sanitized=sanitize_public_pptx(private,public/'expected-output.pptx',work)
    shutil.copyfile(work/'input.png',public/'input.png')
    write_json(public/'layout.json',synthetic_layout())
    readme='''# 完全合成的可编辑示例

`input.png` 由 tools/smoke_test.py 使用 Pillow 程序绘制；没有真实学生/客户截图、第三方图片或模型生成图片。`layout.json` 是对应 1920×1080 对象布局，只有相对内容与数值，没有机器路径。

`expected-output.pptx` 通过实际新 CLI 从空作业依次完成准备、begin、登记、describe 与 done。四个任务为背景、透明前景、独立透明艺术标题和一个普通艺术组件板；回图均是确定性程序夹具，角色核对为明确标注 Synthetic automated fixture 的模拟宿主声明，不是人工看图或提供方认证。调用前 request_sha256 使用真实许可记录；重复 begin 已验证不再给第二次许可。控制器的提供方调用数字段保持 null，不把四次本地许可申报成四次模型调用；本脚本实际没有调用图像提供方。

可以在 PowerPoint 中选择两行原生文字、一个圆角形状、一条线，并移动两张艺术图片。艺术标题与橙色徽章内部不是可逐字编辑的文字。软件检查确认这些原生/图片结构存在；本示例没有执行 Office 打开、编辑、另存或视觉相似度验收，也不说明真实图像模型效果与交付速度。

公开 PPTX 是私有作业成品的独立副本，只将图片 cNvPr@descr 中的绝对路径替换为合成说明，并将 core 文档作者、最后修改者及时间改为固定合成元数据。完整 ZIP member 检查确认图片字节、文本、几何及其它非目标内容未改变；原始私有 PPTX 保持原样。公开副本已扫描全部解压部件及 XML，未发现机器路径、密钥模式或外部关系。

复现（Python、Pillow、NumPy、Node 与运行依赖需按项目说明预先可用）：

    python tools/smoke_test.py --runtime /path/to/extracted/compat-runtime --work /new/empty/outside-directory

默认只在指定私有 work 下写作业与 public-example 四个公开文件。为发行目录生成可另加 --example-dir /new/empty/public-example；拒绝覆盖已有证据。工作目录、state、receipt、真实请求记录和绝对路径日志不随本示例公开。

这是合成的软件流程示例，不是一次真实模型图片还原案例。本例代码、布局及程序绘制素材按项目根 MIT 许可提供。
'''
    (public/'README.md').write_text(readme,encoding='utf-8',newline='\n')
    if sha(private)!=private_sha:raise ValueError('Private PPTX was modified during public example export.')
    public_files=sorted(p.name for p in public.iterdir() if p.is_file())
    if public_files!=['README.md','expected-output.pptx','input.png','layout.json']:
        raise ValueError('Public example contains unexpected files.')
    report={'schema':'ppt-rebuilder.synthetic-cli-smoke/1','passed':True,'provider_image_calls':0,
            'fixture_rendering':'deterministic Pillow drawing; not image generation',
            'role_review_provenance':'explicit synthetic automated host declaration; not human/provider certification',
            'actual_provider_invocations_reported':None,'controller_caller_reported_invocations':response.get('caller_reported_invocations'),
            'controller_recorded_starts':response.get('recorded_invocation_starts'),
            'expected_generation_tasks':response.get('expected_generation_tasks'),
            'cli_steps':steps,'repeated_begin_without_new_permit':repeated,
            'runtime_identity':response.get('runtime_identity'),'terminal_kind':response.get('kind'),
            'native_inspection':inspection,'public_copy_sanitization':sanitized,
            'public_example_files':public_files,'public_example_sha256':{name:sha(public/name) for name in public_files},
            'private_work':str(work),'private_pptx':str(private),'public_example_directory':str(public),
            'limits':['Not fresh model generation, human role review, fidelity evaluation, Office editing acceptance, or end-to-end model timing.']}
    write_json(work/'smoke-report.json',report)
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True,type=Path)
    parser.add_argument('--work',required=True,type=Path)
    parser.add_argument('--example-dir',type=Path,help='Optional empty destination for the four sanitized public example files')
    args=parser.parse_args(argv)
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    try:result=run_smoke(args.runtime,args.work,args.example_dir)
    except (OSError,ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
        print(json.dumps({'passed':False,'error':str(error),'provider_image_calls':0,
                          'instruction':'Preserve this private work directory; no provider or retry is authorized.'},ensure_ascii=False))
        return 2
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
