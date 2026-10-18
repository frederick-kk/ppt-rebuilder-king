"""A one-way reconstruction queue. Preparation never means visual acceptance."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import zipfile

from .artwork import prepare_boards, validate_output, extract_assets
from .layout import normalize_layout
from .renderer import build_presentation
from .source import prepare_source, pixel_identity, normalize_layer
from .prompts import layer_brief, board_brief
from .storage import fingerprint, sha256, read_state, save_state, write_json, relative_file, job_lock


ROOT = Path(__file__).resolve().parent.parent


def _runtime():
    import numpy as np
    from PIL import Image
    if int(np.array([1,2]).sum()) != 3 or Image.new('RGB',(1,1)).size != (1,1):
        raise ValueError('Image dependencies cannot perform basic operations.')
    node = shutil.which('node') or shutil.which('nodejs')
    if not node:
        raise ValueError('An existing Node runtime is required; nothing is installed automatically.')
    script = "const fs=require('fs');for(const [n,v] of Object.entries({pptxgenjs:'4.0.1',jszip:'3.10.1'})){require(n);if(JSON.parse(fs.readFileSync('node_modules/'+n+'/package.json','utf8')).version!==v)throw Error('Dependency version mismatch: '+n)}"
    try:
        result=subprocess.run([node,'-e',script],cwd=ROOT,capture_output=True,text=True,timeout=10)
    except subprocess.TimeoutExpired as exc:
        raise ValueError('The local Node dependency check timed out.') from exc
    if result.returncode:
        raise ValueError('The extracted project needs its bundled Node dependencies; no installation was attempted.')


def _task(work, role, identity, brief, references, **extra):
    task_id = 'task-' + fingerprint([role,identity,brief])[:20]
    path = Path(work) / 'briefs' / (task_id + '.txt')
    path.parent.mkdir(exist_ok=True)
    if path.exists() and path.read_text(encoding='utf-8') != brief:
        raise ValueError('An existing task brief changed.')
    if not path.exists(): path.write_text(brief,encoding='utf-8')
    refs={str(Path(p).resolve().relative_to(Path(work).resolve())).replace('\\','/'):sha256(p) for p in references}
    return {'id':task_id,'role':role,'status':'waiting','issued':False,'attempt':0,'attempts':[],
        'brief':path.relative_to(work).as_posix(),'brief_sha256':sha256(path),'references':refs,**extra}


def _public_task(work, task):
    value={k:task[k] for k in ('id','role','attempt')}
    value.update(prompt_file=str(relative_file(work,task['brief'])),
        references=[str(relative_file(work,p)) for p in task['references']],
        requested_outputs=1, transparent_background=False,
        result_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'file':''},
        failure_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'outcome':'failed','reason':''})
    if task['role']=='board':
        value.update(size=task['board']['size'],key=task['board']['key'],
            members=[{'id':m['id'],'description':m['description'],**({'lettering':m['lettering']} if m.get('lettering') else {})}
                     for m in task['board']['members']])
    else: value['size']=[1920,1080]
    return value


def _integrity(work, state):
    source=state['source']
    for name,key in (('original','byte_sha256'),('working','working_sha256'),('guide','guide_sha256')):
        if sha256(relative_file(work,source[name])) != source[key]:
            raise ValueError('A saved source file changed: '+source[name])
    if state.get('layout'):
        layout=json.loads((Path(work)/'layout.json').read_text(encoding='utf-8'))
        if layout != state['layout']: raise ValueError('The single submitted page description changed.')
    for task in state['tasks'].values():
        pairs={task['brief']:task['brief_sha256'],**task['references']}
        if task.get('result'):
            result=task['result'];pairs[result['raw']]=result['sha256']
            if result.get('normalized'): pairs[result['normalized']]=result['normalized_sha256']
        for observation in task.get('attempts',[]):
            if observation.get('raw'):pairs[observation['raw']]=observation['sha256']
        for path,digest in pairs.items():
            if sha256(relative_file(work,path)) != digest:
                raise ValueError('A recorded task file changed: '+path)
    for asset in state.get('assets',[]):
        if sha256(asset['path']) != asset['sha256']: raise ValueError('An extracted asset changed.')


def _done(state):
    receipt=state['done']
    if not Path(receipt['pptx']).is_file() or sha256(receipt['pptx']) != receipt['sha256']:
        raise ValueError('The completed presentation is missing or changed; the job is not reopened.')
    return copy.deepcopy(receipt)


def _check_pptx(path):
    with zipfile.ZipFile(path) as z:
        if z.testzip() or not {'ppt/presentation.xml','ppt/slides/slide1.xml'}.issubset(z.namelist()):
            raise ValueError('The existing presentation is incomplete.')


def _finish(work, state):
    build=Path(work)/'build';build.mkdir(exist_ok=True)
    for task in state['tasks'].values():
        if task['role']!='board' or task.get('processed'): continue
        started=time.perf_counter()
        result=extract_assets(task['board'],relative_file(work,task['result']['raw']),
                              Path(work)/'assets'/task['id'])
        for asset in result['assets']:
            asset['sha256']=sha256(asset['path'])
        state['assets'].extend(result['assets'])
        state['warnings'].extend(result['warnings'])
        task['processed']=True
        state['processing_seconds'].append({'task_id':task['id'],'seconds':time.perf_counter()-started})
        save_state(work,state,'board_extracted')
    compiled=build/'compiled.pptx'; record=build/'compiled.report.json'
    if compiled.exists():
        _check_pptx(compiled)
        if record.exists():
            rendering=json.loads(record.read_text(encoding='utf-8'))
            if rendering.get('compiled_sha256') != sha256(compiled):
                raise ValueError('An existing compiled presentation changed.')
        else:
            rendering={'recovered_existing_container':True,'warnings':[],
                       'object_inspection':'not_recorded_before_interruption','compiled_sha256':sha256(compiled)}
            write_json(record,rendering)
    else:
        background=next(t for t in state['tasks'].values() if t['role']=='background')
        rendering=build_presentation(state['layout'],relative_file(work,background['result']['normalized']),
                                     state['assets'],compiled)
        _check_pptx(compiled)
        rendering['compiled_sha256']=sha256(compiled)
        write_json(record,rendering)
    destination=Path(work)/'output';destination.mkdir(exist_ok=True)
    final=destination/'PPT Rebuilder King.pptx'
    if final.exists():
        if sha256(final)!=sha256(compiled): raise ValueError('Refusing to replace an unrelated output presentation.')
    else:
        temporary=destination/'presentation.tmp'
        shutil.copyfile(compiled,temporary)
        _check_pptx(temporary)
        # A hard link publishes without replacing a file created by another process.
        os.link(temporary,final)
        temporary.unlink()
    state['warnings'].extend(rendering.get('warnings',[]))
    native_skipped=rendering.get('skipped',[])
    completed=datetime.now(timezone.utc)
    started=datetime.fromisoformat(state['started_at_utc'])
    receipt={'kind':'done','terminal':True,'pptx':str(final.resolve()),'sha256':sha256(final),
        'quality':'not_evaluated','expected_generation_tasks':len(state['tasks']),
        'warnings':state['warnings'],'skipped':native_skipped,
        'controller_wall_seconds':(completed-started).total_seconds(),
        'user_end_to_end_seconds':None,'within_600_seconds':None,
        'instructions':'Deliver this PowerPoint now and stop. No preview, visual check or further generation is part of this workflow.'}
    counts=[]
    for task in state['tasks'].values():
        for attempt in range(task['attempt']+1):
            known={r['invocation_count'] for r in task['attempts']
                   if r['attempt']==attempt and r.get('invocation_count') is not None}
            counts.append(next(iter(known)) if len(known)==1 else None)
    receipt['caller_reported_invocations']=sum(counts) if all(type(n) is int for n in counts) else None
    write_json(Path(work)/'receipt.json',receipt)
    state.update(phase='done',done=receipt)
    save_state(work,state,'presentation_published')
    return copy.deepcopy(receipt)


def _drive(work,state):
    failed=[t['id'] for t in state['tasks'].values() if t['status']=='failed']
    if failed:
        return {'kind':'paused','tasks':failed,'generate_more':False,
            'instructions':'Keep completed files. Retry registration with an existing real output, or reopen a failed task only after the user requests another generation.'}
    if state['phase']=='layers':
        layers=[t for t in state['tasks'].values() if t['role'] in {'background','foreground'}]
        unissued=[t for t in layers if not t['issued']]
        if unissued:
            for task in unissued: task['issued']=True
            save_state(work,state,'layers_dispatched')
            return {'kind':'generate','jobs':[_public_task(work,t) for t in unissued],
                'source':state['source'],'instructions':'Generate each layer once. Parallel calls are optional when the host supports them; register background before foreground.'}
        waiting=[t['id'] for t in layers if t['status']!='recorded']
        if waiting: return {'kind':'await_results','tasks':waiting,'generate_more':False,
            'pending_results':[_public_task(work,t) for t in layers if t['id'] in waiting]}
        state['phase']='describe';save_state(work,state,'layers_ready')
    if state['phase']=='describe':
        return {'kind':'describe','files':[str(relative_file(work,state['source'][k])) for k in ('working','guide')],
            'reply_template':{'kind':'layout','layout':{'items':[]}},
            'instructions':'Read only these same-source images. Describe structures, each visible native text line, and independent artwork once. Artistic lettering stays an art item; generated foreground is not a source of text or geometry facts.'}
    if state['phase']=='boards':
        active=[state['tasks'][i] for i in state.get('active_batch',[])]
        waiting=[t['id'] for t in active if t['status']!='recorded']
        if waiting: return {'kind':'await_results','tasks':waiting,'generate_more':False,
            'pending_results':[_public_task(work,t) for t in active if t['id'] in waiting]}
        tasks=[t for t in state['tasks'].values() if t['role']=='board' and not t['issued']]
        if tasks:
            batch=tasks[:3];state['active_batch']=[t['id'] for t in batch]
            for task in batch:task['issued']=True
            save_state(work,state,'boards_dispatched')
            return {'kind':'generate','jobs':[_public_task(work,t) for t in batch],
                'instructions':'Generate each board once; at most these three may be active. Register results serially. Do not generate a new batch until all current results are recorded.'}
        return _finish(work,state)
    raise ValueError('The job phase cannot be continued.')


def _submit(work,state,data):
    if not isinstance(data,dict): raise ValueError('Input must be a JSON object.')
    if data.get('kind')=='layout':
        layout=normalize_layout(data.get('layout',{}))
        if state.get('layout'):
            if layout != state['layout']: raise ValueError('The one-pass page description is already frozen.')
            return
        if state['phase']!='describe': raise ValueError('Record both layers before describing the page.')
        arts=[item for item in layout['items'] if item['kind']=='art']
        front=next(t for t in state['tasks'].values() if t['role']=='foreground')
        boards=prepare_boards(relative_file(work,state['source']['working']),
            relative_file(work,front['result']['normalized']),arts,Path(work)/'boards')
        state.update(layout=layout,layout_source_sha256=state['source']['working_sha256'],phase='boards')
        write_json(Path(work)/'layout.json',layout)
        for board in boards:
            task=_task(work,'board',board['signature'],board_brief(board),[board['image']],board=board)
            state['tasks'][task['id']]=task
        save_state(work,state,'description_frozen')
        return
    task=state['tasks'].get(data.get('task_id'))
    if task is None or task['role']!=data.get('role'):
        raise ValueError('The result must identify its exact task and role.')
    if not task['issued']: raise ValueError('This task has not been dispatched.')
    if data.get('attempt',0)!=task['attempt']:
        raise ValueError('The result attempt differs from the currently dispatched invocation.')
    count=data.get('invocation_count')
    if count is not None and (type(count) is not int or count<1):
        raise ValueError('Invocation count must be an actual positive integer, or omitted when unknown.')
    observation={'attempt':task['attempt'],'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        **{k:data.get(k) for k in ('tool','tool_call_id','tool_output_id','invocation_count','executed_prompt')}}
    if task['role']=='foreground' and any(t['role']=='background' and t['status']!='recorded' for t in state['tasks'].values()):
        raise ValueError('Keep this foreground file and register the background first.')
    if data.get('outcome')=='failed':
        if task.get('result'): raise ValueError('A recorded first result cannot be replaced by a failure.')
        if not isinstance(data.get('reason'),str) or not data['reason'].strip() or len(data['reason'])>1000:
            raise ValueError('A failed invocation needs a short sanitized reason.')
        if task['status']=='failed' and task.get('failure')==data['reason']:return
        task.update(status='failed',failure=data['reason'])
        task['attempts'].append({**observation,'outcome':'failed','reason':data['reason']})
        save_state(work,state,'generation_paused');return
    if not data.get('file'): raise ValueError('Record an actual saved image file.')
    path=Path(data['file']).resolve()
    digest=sha256(path)
    if task.get('result'):
        if digest!=task['result']['sha256']: raise ValueError('The first registered output is immutable.')
        return
    key='magenta' if task['role']=='foreground' else task['board']['key'] if task['role']=='board' else None
    try:
        validation=validate_output(path,key=key)
    except (ValueError,OSError) as exc:
        task.update(status='failed',failure='Image registration: '+str(exc)[:500])
        preserved=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}-unusable.bin"
        preserved.parent.mkdir(exist_ok=True)
        if not preserved.exists():preserved.write_bytes(path.read_bytes())
        if sha256(preserved)!=digest:raise ValueError('The unusable output changed while being preserved.')
        task['attempts'].append({**observation,'outcome':'unreadable','sha256':digest,
            'raw':preserved.relative_to(work).as_posix(),'reason':task['failure']})
        save_state(work,state,'image_unreadable')
        raise ValueError('Image registration stopped. Preserve existing files; no automatic generation is authorized. '+str(exc)) from exc
    raw=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}.png"
    raw.parent.mkdir(exist_ok=True)
    if raw.exists() and sha256(raw)!=digest:raise ValueError('A stored output was changed.')
    if not raw.exists():raw.write_bytes(path.read_bytes())
    if sha256(raw)!=digest or sha256(path)!=digest:raise ValueError('The returned file changed while recording.')
    result={'raw':raw.relative_to(work).as_posix(),'sha256':digest,
        'tool':data.get('tool'),'tool_call_id':data.get('tool_call_id'),
        'tool_output_id':data.get('tool_output_id'),'identity_scope':'local_file' if not data.get('tool_output_id') else 'caller_reported',
        'invocation_count':data.get('invocation_count'),'executed_prompt':data.get('executed_prompt'),
        'validation':validation}
    if task['role']!='board':
        normalized=Path(work)/'layers'/(task['role']+'.png')
        normalize_layer(raw,task['role'],state['source']['transform'],normalized)
        result.update(normalized=normalized.relative_to(work).as_posix(),normalized_sha256=sha256(normalized))
    task.update(status='recorded',result=result)
    task['attempts'].append({**observation,'outcome':'recorded','raw':result['raw'],'sha256':digest})
    task.pop('failure',None)
    state['warnings'].extend(validation.get('warnings',[]))
    save_state(work,state,'image_registered')


def run(work, *, source=None, data=None, retry_task=None):
    """Advance only explicitly identified work; external generation stays external."""
    if sum(value is not None for value in (source,data,retry_task))>1:
        raise ValueError('Use a source, a submission, or an explicit retry, one at a time.')
    work=Path(work).resolve()
    if not (work/'state.json').exists() and source is None and not (work/'initialization.json').exists():
        raise ValueError('A new job requires the current user image; no example is selected.')
    with job_lock(work):
        if (work/'state.json').exists():
            state=read_state(work)
            if source is not None and pixel_identity(source)!=state['source']['pixel_sha256']:
                raise ValueError('This image differs from the job source. Use a new empty directory.')
            if state.get('done'):return _done(state)
            _integrity(work,state)
        else:
            initialization=work/'initialization.json'
            if initialization.exists():
                marker=json.loads(initialization.read_text(encoding='utf-8'))
                allowed={'.lock','initialization.json','input','briefs'}
                if marker.get('format')!='newpath.start/1' or any(p.name not in allowed for p in work.iterdir()):
                    raise ValueError('The interrupted initialization directory cannot be safely resumed.')
                if source is not None and pixel_identity(source)!=marker['pixel_sha256']:
                    raise ValueError('This image differs from the interrupted job source.')
                snapshot=relative_file(work,marker['snapshot'])
                source=snapshot if snapshot.exists() else Path(marker['input_path'])
                if sha256(source)!=marker['byte_sha256']:
                    raise ValueError('The initialization source is missing or changed.')
            else:
                if any(p.name!='.lock' for p in work.iterdir()):raise ValueError('A new job needs an empty directory.')
                source=Path(source).resolve()
                byte_hash=sha256(source);pixel_hash=pixel_identity(source)
                if sha256(source)!=byte_hash:raise ValueError('The source changed while initializing.')
                marker={'format':'newpath.start/1','input_path':str(source),
                    'snapshot':'input/original'+(source.suffix.lower() or '.bin'),
                    'created_at_utc':datetime.now(timezone.utc).isoformat(),
                    'byte_sha256':byte_hash,'pixel_sha256':pixel_hash}
                write_json(initialization,marker)
            _runtime()
            src=prepare_source(source,work)
            if src['byte_sha256']!=marker['byte_sha256'] or src['pixel_sha256']!=marker['pixel_sha256']:
                raise ValueError('The source changed after initialization.')
            src['input_path']=marker['input_path']
            state={'format':'newpath.job/1','phase':'layers','source':src,'tasks':{},'layout':None,
                'assets':[],'warnings':[],'processing_seconds':[],'active_batch':[],
                'started_at_utc':marker.get('created_at_utc',datetime.now(timezone.utc).isoformat()),'events':[]}
            for role in ('background','foreground'):
                task=_task(work,role,src['pixel_sha256'],layer_brief(role,src['transform']),[relative_file(work,src['working'])])
                state['tasks'][task['id']]=task
            save_state(work,state,'source_prepared')
        if retry_task is not None:
            task=state['tasks'].get(retry_task)
            if not task or task['status']=='recorded':raise ValueError('Only an unfinished task can be explicitly reopened.')
            if task['role']=='board' and task['id'] not in state['active_batch']:
                raise ValueError('Retry only an unfinished member of the active batch.')
            task.update(status='waiting',issued=True,attempt=task['attempt']+1)
            task.pop('failure',None)
            save_state(work,state,'explicit_retry')
            return {'kind':'generate','jobs':[_public_task(work,task)],'instructions':'Explicitly reopened task only; keep every completed result.'}
        if data is not None:_submit(work,state,data)
        return _drive(work,state)
