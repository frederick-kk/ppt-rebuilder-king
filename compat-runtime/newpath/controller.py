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
from .wordart import MODE as WORDART_MODE, select_primary_wordart, validate_wordart_output, extract_wordart_asset
from .layout import normalize_layout
from .renderer import build_presentation
from .source import prepare_source, pixel_identity, normalize_layer
from .prompts import layer_brief, board_brief, wordart_brief
from .native import MODE as MATERIAL_MODE, validate_native_output
from . import execution, version, binding
from .storage import fingerprint, sha256, read_state, save_state, write_json, relative_file, job_lock


ROOT = Path(__file__).resolve().parent.parent
GENERATION_POLICY = 'single-call-single-output/1'


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


def _native_material_task(task):
    if task['role'] not in {'foreground','board'}:
        return False
    mode=task.get('background_mode')
    board_mode=task.get('board',{}).get('background_mode')
    if any(value not in (None,MATERIAL_MODE) for value in (mode,board_mode)):
        raise ValueError('Unsupported material background mode')
    if task['role']=='board' and mode==MATERIAL_MODE and board_mode!=MATERIAL_MODE:
        raise ValueError('Inconsistent material board mode')
    return mode==MATERIAL_MODE or board_mode==MATERIAL_MODE


def _material_integrity(state):
    mode=state.get('material_background_mode')
    if mode not in (None,MATERIAL_MODE):
        raise ValueError('Unsupported material background mode')
    for task in state['tasks'].values():
        if task['role'] in {'foreground','board'}:
            if _native_material_task(task)!=(mode==MATERIAL_MODE):
                raise ValueError('Inconsistent material background mode')


def _public_task(work, task):
    value={k:task[k] for k in ('id','role','attempt')}
    value.update(prompt_file=str(relative_file(work,task['brief'])),
        references=[str(relative_file(work,p)) for p in task['references']],
        requested_outputs=1, transparent_background=False,
        execution_contract={'policy':GENERATION_POLICY,'max_invocations':1,'max_outputs':1,
            'provider_output_count':1,'automatic_retry':False,
            'count_provenance':'caller_reported_not_provider_verified',
            'instructions':'Use one image-tool call requesting one output. Immediately save and register its first technically usable result. Do not generate variants, compare candidates or call again while awaiting or correcting registration. If the tool returns extra images, preserve the known files, report their count and stop.'},
        result_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'file':''},
        failure_template={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'outcome':'failed','reason':''})
    if task['role']=='board':
        value.update(size=task['board']['size'],key=task['board']['key'],
            members=[{'id':m['id'],'description':m['description'],**({'lettering':m['lettering']} if m.get('lettering') else {})}
                     for m in task['board']['members']])
        value['execution_contract'].update(reference_inputs_are_not_outputs=True)
        value['execution_contract']['instructions']+=' Every reference input is a placement or appearance aid, not a generated result. Register the actual saved board output; never submit a reference input, renamed copy or re-encoded copy. If no real board output is available, leave this task unfinished and report the missing output.'
    elif task['role']=='wordart':
        value.update(size=[1536,1024],transparent_background=True,background_mode=WORDART_MODE,
                     member=copy.deepcopy(task['member']))
    else: value['size']=[1920,1080]
    if _native_material_task(task):
        value.update(transparent_background=True,background_mode=MATERIAL_MODE)
    value=execution.decorate(work,task,value)
    if task.get('delivery_policy')==binding.DELIVERY_POLICY:value=binding.decorate_task(task,value)
    return value


def _strict_generation(state):
    # Frozen older jobs keep their recorded recovery semantics. New jobs opt in
    # explicitly; an old receipt is never rewritten to claim new validation.
    return state.get('generation_policy') == GENERATION_POLICY


def _registration_task(work, task, strict=True):
    if not strict:
        return _public_task(work, task)
    value={'id':task['id'],'role':task['role'],'attempt':task['attempt'],
            'action':'register_existing_output_only',
            'result_template':{'task_id':task['id'],'role':task['role'],
                               'attempt':task['attempt'],'file':''},
            'failure_template':{'task_id':task['id'],'role':task['role'],
                                'attempt':task['attempt'],'outcome':'failed','reason':''}}
    if task['role']=='board':
        value['instructions']='Register only an existing actual board output. A reference input, renamed copy or re-encoded copy is not an output. If it is missing, keep this task unfinished; this registration request does not authorize another image call.'
    if task.get('delivery_policy')==binding.DELIVERY_POLICY:
        value['result_template']['request_sha256']=task['execution'].get('request_sha256','')
        value.update(role_review_template=binding.review_template(task),role_review_scope=binding.REVIEW_SCOPE)
    return value


def _reject_reference_board_return(work, task, path, digest):
    """Detect known input substitution; this is not provider identity proof."""
    if task['role']!='board':
        return
    from PIL import Image
    candidate_pixels=None
    try:
        for name, recorded_digest in task['references'].items():
            reference=relative_file(work,name).resolve()
            if path==reference or digest==recorded_digest or digest==sha256(reference):
                raise ValueError('A board reference input cannot be registered as a generated output.')
            if candidate_pixels is None:
                with Image.open(path) as image:
                    candidate_pixels=(image.size,image.convert('RGBA').tobytes())
            with Image.open(reference) as image:
                if image.size==candidate_pixels[0] and image.convert('RGBA').tobytes()==candidate_pixels[1]:
                    raise ValueError('A re-encoded board reference input cannot be registered as a generated output.')
    except Image.DecompressionBombError as exc:
        raise ValueError('Image dimensions exceed safe decoding limits.') from exc


def _pending_generation_violations(state):
    return [task['id'] for task in state['tasks'].values()
            if any(not entry.get('acknowledged') for entry in task.get('generation_violations',[]))]


def _authorization(value, name):
    if not isinstance(value,str) or not value.strip() or len(value)>2000:
        raise ValueError(name+' must quote the explicit user request (1-2000 characters); caller assertions are not identity verification.')
    return value.strip()


def _returned_file_evidence(data):
    files=data.get('returned_files',[])
    if not isinstance(files,list) or any(not isinstance(path,str) or not path for path in files):
        raise ValueError('returned_files must list only known returned image paths, or be omitted.')
    paths=[]
    for value in ([data['file']] if data.get('file') else [])+files:
        path=Path(value).resolve()
        if path not in paths:paths.append(path)
    known=[(path,sha256(path)) for path in paths if path.is_file()]
    return {'paths':paths,'known':known,'missing':[str(path) for path in paths if not path.is_file()]}


def _generation_violation(work, state, task, data, observation, evidence):
    """Preserve known returns, even when an over-budget result is not accepted.

    Counts and filenames are host reports. This controller cannot observe or
    stop an image-tool call that the host makes outside the returned contract.
    """
    paths=evidence['paths'];unique={digest for _,digest in evidence['known']}
    exceeded=(data.get('invocation_count',0) or 0)>1 or (data.get('output_count',0) or 0)>1 or len(unique)>1 or bool(data.get('_returned_outputs_inconsistent'))
    if not exceeded:return None
    preserved=[];missing=evidence['missing']
    for path,digest in evidence['known']:
        destination=Path(work)/'returned'/f"{task['id']}-{task['attempt']}-{digest[:16]}-generation-violation.bin"
        destination.parent.mkdir(exist_ok=True)
        if not destination.exists():destination.write_bytes(path.read_bytes())
        if sha256(destination)!=digest or sha256(path)!=digest:
            raise ValueError('A reported extra return changed while being preserved.')
        if not any(entry['sha256']==digest for entry in preserved):
            preserved.append({'raw':destination.relative_to(work).as_posix(),'sha256':digest})
    binding_errors=[]
    if binding.enabled(state):
        entries=data.get('returned_outputs') if isinstance(data.get('returned_outputs'),list) else []
        for path,digest in evidence['known']:
            supplied=next((entry for entry in entries if isinstance(entry,dict)
                and isinstance(entry.get('file'),str) and Path(entry['file']).resolve()==path),{})
            claim={**data,'tool':supplied.get('tool',data.get('tool')),
                   'tool_output_id':supplied.get('tool_output_id')}
            try:binding.claim_return(work,state,task,claim,path,digest)
            except binding.BindingViolation as exc:binding_errors.append({'sha256':digest,'reason':str(exc)[:500]})
            except (ValueError,OSError):pass
    identity=fingerprint([task['attempt'],data.get('invocation_count'),data.get('output_count'),
                          [entry['sha256'] for entry in preserved],missing,len(paths)])
    entries=task.setdefault('generation_violations',[])
    existing=next((entry for entry in entries if entry['id']==identity),None)
    if existing:return existing
    entry={**observation,'id':identity,'invocation_count':data.get('invocation_count'),
           'output_count':data.get('output_count'),'observed_returned_files':len(unique),
           'observed_returned_paths':len(paths),'observed_unique_returned_files':len(unique),
           'preserved_files':preserved,'unavailable_reported_files':missing,'acknowledged':False,
           'count_provenance':'caller_reported_not_provider_verified'}
    if binding_errors:entry['binding_errors']=binding_errors
    if data.get('_returned_outputs_inconsistent'):entry['returned_outputs_inconsistent']=True
    entries.append(entry)
    if not task.get('result'):
        task.update(status='failed',failure='Generation budget exceeded; preserve returns and request user direction.')
    save_state(work,state,'generation_budget_exceeded')
    return entry


def _acknowledge_violations(task, authorization, resolution):
    for entry in task.get('generation_violations',[]):
        if not entry.get('acknowledged'):
            entry.update(acknowledged=True,authorization=authorization,resolution=resolution,
                         authorization_provenance='caller_reported_user_request_not_verified')


def _reported_counts(state, key):
    totals=[];conflicts=[]
    for task in state['tasks'].values():
        for attempt in range(task['attempt']+1):
            reports=task.get('attempts',[])+task.get('generation_violations',[])
            known={report[key] for report in reports if report.get('attempt')==attempt
                   and type(report.get(key)) is int}
            if len(known)>1:conflicts.append({'task_id':task['id'],'attempt':attempt,'field':key,'reported_values':sorted(known)})
            hashes={report['sha256'] for report in task.get('attempts',[])
                    if report.get('attempt')==attempt and report.get('sha256')}
            hashes.update(preserved['sha256'] for report in task.get('generation_violations',[])
                          if report.get('attempt')==attempt for preserved in report.get('preserved_files',[]))
            if key=='output_count' and known and len(hashes)>max(known):
                conflicts.append({'task_id':task['id'],'attempt':attempt,'field':key,
                    'reported_values':sorted(known),'observed_unique_files_lower_bound':len(hashes),
                    'reason':'declared_output_count_below_known_file_count'})
            totals.append(max(known) if known else None)
    contradiction=any(entry.get('reason')=='declared_output_count_below_known_file_count' for entry in conflicts)
    return (sum(totals) if not contradiction and all(type(value) is int for value in totals) else None,
            sum(value for value in totals if type(value) is int),conflicts)


def _integrity(work, state):
    execution.integrity(work,state)
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
        for violation in task.get('generation_violations',[]):
            for preserved in violation.get('preserved_files',[]):
                pairs[preserved['raw']]=preserved['sha256']
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
        if task['role'] not in {'board','wordart'} or task.get('processed'): continue
        started=time.perf_counter()
        if task['role']=='wordart':
            result=extract_wordart_asset(task['member'],relative_file(work,task['result']['raw']),
                                        Path(work)/'assets'/task['id'])
        else:
            result=extract_assets(task['board'],relative_file(work,task['result']['raw']),
                                  Path(work)/'assets'/task['id'])
        for asset in result['assets']:
            asset['sha256']=sha256(asset['path'])
        state['assets'].extend(result['assets'])
        state['warnings'].extend(result['warnings'])
        if result.get('cell_mapping'):
            task['extraction']={'cell_mapping':result['cell_mapping']}
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
        render_options={}
        if execution.enabled(state):
            from .renderer import TEXT_FIT_POLICY
            render_options['textfit_policy']=TEXT_FIT_POLICY
        rendering=build_presentation(state['layout'],relative_file(work,background['result']['normalized']),
                                     state['assets'],compiled,**render_options)
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
    calls,known_calls,call_conflicts=_reported_counts(state,'invocation_count')
    outputs,known_outputs,output_conflicts=_reported_counts(state,'output_count')
    observed_hashes={report['sha256'] for task in state['tasks'].values()
                     for report in task.get('attempts',[]) if report.get('sha256')}
    observed_hashes.update(preserved['sha256'] for task in state['tasks'].values()
                          for violation in task.get('generation_violations',[])
                          for preserved in violation.get('preserved_files',[]))
    per_attempt=[]
    for task in state['tasks'].values():
        for attempt in range(task['attempt']+1):
            hashes={report['sha256'] for report in task.get('attempts',[])
                    if report.get('attempt')==attempt and report.get('sha256')}
            hashes.update(preserved['sha256'] for report in task.get('generation_violations',[])
                          if report.get('attempt')==attempt for preserved in report.get('preserved_files',[]))
            per_attempt.append({'task_id':task['id'],'attempt':attempt,'unique_known_files':len(hashes)})
    receipt.update(caller_reported_invocations=calls,caller_reported_outputs=outputs,
                   registered_outputs=sum(bool(task.get('result')) for task in state['tasks'].values()),
                   caller_reported_known_invocations=known_calls,caller_reported_known_outputs=known_outputs,
                   generation_count_provenance='caller_reported_not_provider_verified',
                   generation_count_method='maximum_reported_per_task_attempt; unknown attempts remain unknown',
                   observed_unique_returned_files_lower_bound=len(observed_hashes),
                   observed_file_count_scope='unique known raw file SHA256 across this job; not provider calls or billing',
                   observed_task_attempt_file_lower_bound=sum(entry['unique_known_files'] for entry in per_attempt),
                   observed_task_attempt_file_counts=per_attempt,
                   reported_count_conflicts=call_conflicts+output_conflicts,
                   generation_violations=[{'task_id':task['id'],**entry}
                       for task in state['tasks'].values() for entry in task.get('generation_violations',[])])
    if execution.enabled(state):
        receipt.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(state['runtime_identity']),
                       invocation_request_provenance=execution.PROVENANCE,
                       recorded_invocation_starts=sum(len(t.get('execution_history',[]))+
                           int(t['execution']['state']!='not_started') for t in state['tasks'].values()))
        if state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
            receipt.update(post_return_prompt_policy=execution.POST_RETURN_PROMPT_POLICY,
                           post_return_prompt_audit=execution.prompt_audit_summary(state))
        if binding.enabled(state):
            receipt.update(delivery_policy=binding.DELIVERY_POLICY,output_role_binding=binding.summary(state))
    write_json(Path(work)/'receipt.json',receipt)
    state.update(phase='done',done=receipt)
    save_state(work,state,'presentation_published')
    return copy.deepcopy(receipt)


def _pending_response(work,state,tasks):
    if execution.enabled(state):
        not_started=[t for t in tasks if t['execution']['state']=='not_started']
        if not_started:
            if state.get('execution_hold'):return _hold_response(not_started)
            return {'kind':'generate','jobs':[_public_task(work,t) for t in not_started],
                    'pending_results':[_registration_task(work,t,True) for t in tasks if t not in not_started],
                    'instructions':'Prepare only the explicitly not-started first invocations. Submit each begin_template before any image call. Already started tasks remain registration-only; no retry or extra call is permitted.'}
    return {'kind':'await_results','tasks':[t['id'] for t in tasks],'generate_more':False,
            'pending_results':[_registration_task(work,t,_strict_generation(state)) for t in tasks]}


def _response(state,value):
    if execution.enabled(state):
        value=copy.deepcopy(value)
        value.update(runtime_identity=copy.deepcopy(state['runtime_identity']),execution_policy=execution.MODE)
        if state.get('post_return_prompt_policy') is not None:
            value['post_return_prompt_policy']=state['post_return_prompt_policy']
        if binding.enabled(state):value['delivery_policy']=binding.DELIVERY_POLICY
        if state.get('execution_hold'):value['remaining_generation_stopped']=True
        if value.get('kind')=='generate':
            value['instructions']='These are preparation cards, not image-call permits. Submit begin_template and invoke only after the first invoke_once reply. '+value.get('instructions','')
    return value


def _hold_response(tasks):
    return {'kind':'paused','tasks':[t['id'] for t in tasks],'generate_more':False,
            'reason':'user_stopped_remaining_generation',
            'resume_template':{'kind':'resume_unstarted','authorization':''},
            'instructions':'The recorded user instruction stopped remaining generation. Keep all files; existing outputs may still be registered. Resume only after a new user request is quoted through resume_unstarted. No image invocation is authorized.'}


def _drive(work,state):
    violations=_pending_generation_violations(state)
    if violations:
        return {'kind':'paused','tasks':violations,'generate_more':False,
            'reason':'generation_budget_exceeded',
            'instructions':'Known extra generation or outputs were reported. Preserve all existing files and stop image calls. Ask the user whether to retain an existing result. A submission with violation_authorization quoting that user request accepts an existing output for the named task only; it does not authorize another invocation or complete other tasks. If the user prohibits remaining generation and an actual output is missing, keep the job unfinished and report it; never substitute reference inputs or manufacture completion.'}
    failed=[t['id'] for t in state['tasks'].values() if t['status']=='failed']
    if failed:
        return {'kind':'paused','tasks':failed,'generate_more':False,
            'instructions':'Keep completed files. Retry registration with an existing real output, or reopen a failed task only after the user requests another generation.'}
    if state['phase']=='layers':
        layers=[t for t in state['tasks'].values() if t['role'] in {'background','foreground'}]
        unissued=[t for t in layers if not t['issued']]
        if unissued:
            if execution.enabled(state) and state.get('execution_hold'):return _hold_response(unissued)
            for task in unissued: task['issued']=True
            save_state(work,state,'layers_dispatched')
            return {'kind':'generate','jobs':[_public_task(work,t) for t in unissued],
                'source':state['source'],'instructions':'Generate each layer once with one output per image-tool call. Save and register the first technically usable output immediately; do not compare or improve candidates. Parallel calls are optional when the host supports them; register background before foreground. Waiting or registration errors never authorize another image call.'}
        waiting=[t['id'] for t in layers if t['status']!='recorded']
        if waiting:return _pending_response(work,state,[t for t in layers if t['id'] in waiting])
        state['phase']='describe';save_state(work,state,'layers_ready')
    if state['phase']=='describe':
        return {'kind':'describe','files':[str(relative_file(work,state['source'][k])) for k in ('working','guide')],
            'reply_template':{'kind':'layout','layout':{'items':[]}},
            'instructions':'Read only these same-source images. Describe structures, each visible native text line, and independent artwork once. For artistic-writing items, fill lettering with all exact source characters and description with colours, brushwork, original outlines, material and arrangement. The largest artistic-writing group alone will use standalone true-alpha generation; all other art keeps the existing board route. Artistic lettering stays an art item; generated foreground is not a source of text or geometry facts. Complete this in the existing one-pass description, with no extra analysis call.'}
    if state['phase']=='boards':
        active=[state['tasks'][i] for i in state.get('active_batch',[])]
        waiting=[t['id'] for t in active if t['status']!='recorded']
        if waiting:return _pending_response(work,state,[t for t in active if t['id'] in waiting])
        tasks=[t for t in state['tasks'].values() if t['role'] in {'board','wordart'} and not t['issued']]
        tasks.sort(key=lambda task:task['role']!='wordart')
        if tasks:
            if execution.enabled(state) and state.get('execution_hold'):return _hold_response(tasks)
            batch=tasks[:3];state['active_batch']=[t['id'] for t in batch]
            for task in batch:task['issued']=True
            save_state(work,state,'boards_dispatched')
            field_instruction=('board jobs also require true PNG transparency.'
                if state.get('material_background_mode')==MATERIAL_MODE
                else 'board jobs retain their existing opaque field.')
            return {'kind':'generate','jobs':[_public_task(work,t) for t in batch],
                'instructions':'Generate each listed image once with one output per tool call. A wordart job is the standalone primary title and requires true transparency; '+field_instruction+' At most these three jobs may be active. Register first usable results serially and immediately; do not generate alternatives or retry while waiting. Do not generate a new batch until all current results are recorded.'}
        return _finish(work,state)
    raise ValueError('The job phase cannot be continued.')


def _adopt_single_call_surplus(work,state,task,data,observation):
    """Select in declared return order, never invoke or rank for appearance."""
    outputs=data.get('returned_outputs');count=data.get('output_count')
    if data.get('_returned_outputs_inconsistent'):return False
    if (data.get('invocation_count')!=1 or type(count) is not int or count<2
            or not isinstance(outputs,list) or len(outputs)!=count or state.get('execution_hold')
            or data.get('stop_remaining_generation')):return False
    if any(identity!=task['id'] for identity in _pending_generation_violations(state)):return False
    if any(not v.get('acknowledged') and v.get('invocation_count')!=1 for v in task.get('generation_violations',[])):return False
    if any(not isinstance(entry,dict) or not isinstance(entry.get('file'),str)
           or not Path(entry['file']).is_file() or not isinstance(entry.get('role_review'),dict) for entry in outputs):return False
    paths=[str(Path(entry['file']).resolve()) for entry in outputs]
    if data.get('returned_files') is not None and [str(Path(p).resolve()) for p in data['returned_files']]!=paths:return False
    if data.get('file') and str(Path(data['file']).resolve())!=paths[0]:return False
    original={**data,'returned_files':paths,'file':paths[0]}
    evidence=_returned_file_evidence(original)
    violation=_generation_violation(work,state,task,original,observation,evidence)
    if violation is None:return False
    preserved={entry['sha256']:entry['raw'] for entry in violation['preserved_files']}
    returns=[{'index':i,'sha256':sha256(path),'raw':preserved[sha256(path)],
              'tool':outputs[i].get('tool',data.get('tool')),'tool_output_id':outputs[i].get('tool_output_id'),
              'declared_role_review':binding.review_evidence(outputs[i]['role_review'])} for i,path in enumerate(paths)]
    # Claim every known return before choosing one. Unselected files are still
    # from this declared invocation and cannot be relabelled as another task.
    binding_failed=False
    for index,entry in enumerate(outputs):
        claim={**data,'tool':entry.get('tool',data.get('tool')),'tool_output_id':entry.get('tool_output_id')}
        try:binding.claim_return(work,state,task,claim,Path(paths[index]),returns[index]['sha256'])
        except binding.BindingViolation as exc:
            binding_failed=True
            task.update(status='failed',failure='Image result binding: '+str(exc))
            task['attempts'].append({**observation,'outcome':'binding_rejected','sha256':returns[index]['sha256'],
                                     'raw':returns[index]['raw'],'reason':task['failure']})
            returns[index].update(outcome='binding_rejected',reason=task['failure'])
        except (ValueError,OSError):
            pass  # Broken images remain owned/preserved and fail selection below.
    if binding_failed:
        for entry in returns:entry.setdefault('outcome','preserved_not_selected')
        task['surplus_resolution']={'policy':binding.DELIVERY_POLICY,'selected_index':None,'returns':returns,
            'invocation_count':1,'output_count':count,'hard_binding_failure':True,
            'provenance':'caller_declared_single_invocation_and_return_order_not_provider_verified',
            'selection':'All known files retained and ownership checked; source substitution or ownership contradiction forbids selection.'}
        save_state(work,state,'surplus_binding_rejected');return True
    previous=task.get('surplus_resolution')
    if previous is not None:
        if [(e['index'],e['sha256']) for e in previous['returns']]!=[(e['index'],e['sha256']) for e in returns]:
            raise ValueError('The first surplus-return order and output ownership are immutable.')
        selected=previous.get('selected_index')
        if selected is None:return True
        entry=outputs[selected]
        selected_data={k:v for k,v in data.items() if k not in {'returned_outputs','returned_files','role_review','tool_output_id'}}
        selected_data.update(file=paths[selected],role_review=entry['role_review'],tool_output_id=entry.get('tool_output_id'))
        if entry.get('tool') is not None:selected_data['tool']=entry['tool']
        _submit(work,state,selected_data,_surplus=True);return True
    selected=None
    for index,entry in enumerate(outputs):
        candidate={k:v for k,v in data.items() if k not in {'returned_outputs','returned_files','role_review','tool_output_id'}}
        # Only output-specific evidence may differ; task identity and locked
        # request binding remain the top-level invocation's immutable values.
        candidate.update(file=paths[index],role_review=entry['role_review'],tool_output_id=entry.get('tool_output_id'))
        if entry.get('tool') is not None:candidate['tool']=entry['tool']
        try:
            _submit(work,state,candidate,_surplus=True)
        except (ValueError,OSError) as exc:
            returns[index]['outcome']='rejected';returns[index]['reason']=str(exc)[:500]
            continue
        selected=index;returns[index]['outcome']='selected';break
    for entry in returns:
        entry.setdefault('outcome','preserved_not_selected')
    task['surplus_resolution']={'policy':binding.DELIVERY_POLICY,'selected_index':selected,'returns':returns,
        'invocation_count':1,'output_count':count,'provenance':'caller_declared_single_invocation_and_return_order_not_provider_verified',
        'selection':'first technically valid, source-safe and host-role-reviewed existing return; no appearance ranking or new invocation'}
    if selected is not None:
        violation.update(acknowledged=True,resolution='first_usable_existing_return_by_frozen_policy',
                         resolution_policy=binding.DELIVERY_POLICY)
        state['warnings'].append({'code':'single_call_surplus_adopted','task_id':task['id'],
            'attempt':task['attempt'],'selected_index':selected,'actual_reported_outputs':count,
            'message':'One caller-reported invocation returned extra images. All known originals were preserved; the first usable existing return was adopted without another call.'})
    else:
        task.update(status='failed',failure='All existing returns failed technical, source or role review; preserve them without automatic generation.')
    save_state(work,state,'single_call_surplus_reviewed');return True


def _submit(work,state,data,_surplus=False):
    if not isinstance(data,dict): raise ValueError('Input must be a JSON object.')
    if data.get('kind')=='resume_unstarted':
        if not execution.enabled(state) or not state.get('execution_hold'):
            raise ValueError('There is no recorded remaining-generation stop to resume.')
        authorization=_authorization(data.get('authorization'),'authorization')
        state.setdefault('execution_resume_authorizations',[]).append({'authorization':authorization,
            'at_utc':datetime.now(timezone.utc).isoformat(),'provenance':'caller_reported_user_request_not_verified'})
        state.pop('execution_hold')
        save_state(work,state,'remaining_generation_resumed');return
    if data.get('kind')=='layout':
        layout=normalize_layout(data.get('layout',{}))
        if state.get('layout'):
            if layout != state['layout']: raise ValueError('The one-pass page description is already frozen.')
            return
        if state['phase']!='describe': raise ValueError('Record both layers before describing the page.')
        arts=[item for item in layout['items'] if item['kind']=='art']
        primary,other_arts=select_primary_wordart(arts)
        front=next(t for t in state['tasks'].values() if t['role']=='foreground')
        boards=prepare_boards(relative_file(work,state['source']['working']),
            relative_file(work,front['result']['normalized']),other_arts,Path(work)/'boards',
            **({'background_mode':MATERIAL_MODE} if state.get('material_background_mode')==MATERIAL_MODE else {}))
        state.update(layout=layout,layout_source_sha256=state['source']['working_sha256'],phase='boards')
        write_json(Path(work)/'layout.json',layout)
        if primary is not None:
            identity={'policy':WORDART_MODE,'source_pixel_sha256':state['source']['pixel_sha256'],'member':primary}
            task=_task(work,'wordart',identity,wordart_brief(primary),
                       [relative_file(work,state['source']['working'])],member=primary,background_mode=WORDART_MODE)
            if execution.enabled(state):execution.enroll(task)
            if binding.enabled(state):task['delivery_policy']=binding.DELIVERY_POLICY
            state['tasks'][task['id']]=task
        for board in boards:
            state['warnings'].extend({'board':board['id'],**note} for note in board.get('reference_warnings',[]))
            # The generated board defines placement; the unchanged current
            # source defines appearance. Do not promote a generated white halo
            # or nearby caption to an authoritative part of the artwork.
            task=_task(work,'board',board['signature'],board_brief(board),
                       [board['image'],relative_file(work,state['source']['working'])],board=board,
                       **({'background_mode':MATERIAL_MODE} if board.get('background_mode')==MATERIAL_MODE else {}))
            if execution.enabled(state):execution.enroll(task)
            if binding.enabled(state):task['delivery_policy']=binding.DELIVERY_POLICY
            state['tasks'][task['id']]=task
        save_state(work,state,'description_frozen')
        return
    task=state['tasks'].get(data.get('task_id'))
    if task is None or task['role']!=data.get('role'):
        raise ValueError('The result must identify its exact task and role.')
    if not task['issued']: raise ValueError('This task has not been dispatched.')
    if data.get('attempt',0)!=task['attempt']:
        raise ValueError('The result attempt differs from the currently dispatched invocation.')
    if data.get('kind')=='begin_generation':
        if not execution.enabled(state):raise ValueError('begin_generation requires an explicit execution job; old jobs are not migrated.')
        if _pending_generation_violations(state):raise ValueError('The job is paused for a generation violation; no image call can begin.')
        if state.get('execution_hold'):raise ValueError('Remaining generation is paused by the user; resume_unstarted with a new user request first.')
        if any(t['status']=='failed' for t in state['tasks'].values()):
            raise ValueError('The job is paused for a failed task; correct its existing registration or obtain an explicit retry authorization.')
        if task['role'] in {'board','wordart'} and task['id'] not in state['active_batch']:
            raise ValueError('Begin only a member of the active batch.')
        permit=execution.begin(work,task,_public_task(work,task),data.get('request'))
        if permit is None:
            return {'kind':'await_results','generate_more':False,'tasks':[task['id']],
                    'pending_results':[_registration_task(work,task,True)],
                    'instructions':'This invocation was already begun. Do not call again; register its existing output or wait.'}
        if binding.enabled(state):permit=binding.decorate_permit(task,permit)
        save_state(work,state,'invocation_started')
        return permit
    if execution.enabled(state):execution.require_started(task)
    if binding.enabled(state) and not _surplus:data=binding.normalize_known_returns(data)
    if 'stop_remaining_generation' in data and type(data['stop_remaining_generation']) is not bool:
        raise ValueError('stop_remaining_generation must be a boolean user-direction declaration.')
    for field in ('invocation_count','output_count'):
        count=data.get(field)
        if count is not None and (type(count) is not int or count<1):
            raise ValueError(field+' must be an actual positive integer, or omitted when unknown.')
    observation={'attempt':task['attempt'],'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        **{k:data.get(k) for k in ('tool','tool_call_id','tool_output_id','invocation_count','output_count','executed_prompt')}}
    if binding.enabled(state) and not _surplus:
        if data.get('stop_remaining_generation'):
            quote=_authorization(data.get('violation_authorization'),'violation_authorization')
            state['execution_hold']={'authorization':quote,'provenance':'caller_reported_user_request_not_verified'}
            save_state(work,state,'remaining_generation_stopped')
        if _adopt_single_call_surplus(work,state,task,data,observation):return
    if execution.enabled(state) and data.get('file') and not task.get('result'):
        task['execution']['state']='returned'
    authorization=None;evidence=None
    if _strict_generation(state) and not _surplus:
        if data.get('violation_authorization') is not None:
            authorization=_authorization(data['violation_authorization'],'violation_authorization')
        evidence=_returned_file_evidence(data)
        _generation_violation(work,state,task,data,observation,evidence)
        pending=any(not entry.get('acknowledged') for entry in task.get('generation_violations',[]))
        if binding.enabled(state) and pending and (state.get('execution_hold') or data.get('stop_remaining_generation')):return
        if pending and authorization is None:return
        if authorization is not None and not pending:
            if any(entry.get('acknowledged') and entry.get('authorization')==authorization
                   for entry in task.get('generation_violations',[])):
                authorization=None  # Re-registering the identical accepted file is idempotent.
            else:
                raise ValueError('No pending generation violation belongs to this task; authorization cannot apply to another task.')
    if execution.enabled(state) and data.get('stop_remaining_generation'):
        quote=_authorization(data.get('violation_authorization'),'violation_authorization')
        hold={'authorization':quote,'provenance':'caller_reported_user_request_not_verified'}
        if state.get('execution_hold')!=hold:
            state['execution_hold']=hold;save_state(work,state,'remaining_generation_stopped')
    if task['role']=='foreground' and any(t['role']=='background' and t['status']!='recorded' for t in state['tasks'].values()):
        raise ValueError('Keep this foreground file and register the background first.')
    if data.get('outcome')=='failed':
        if task.get('result'): raise ValueError('A recorded first result cannot be replaced by a failure.')
        if not isinstance(data.get('reason'),str) or not data['reason'].strip() or len(data['reason'])>1000:
            raise ValueError('A failed invocation needs a short sanitized reason.')
        if task['status']=='failed' and task.get('failure')==data['reason']:return
        task.update(status='failed',failure=data['reason'])
        if execution.enabled(state):task['execution']['state']='failed'
        task['attempts'].append({**observation,'outcome':'failed','reason':data['reason']})
        save_state(work,state,'generation_paused');return
    if not data.get('file'): raise ValueError('Record an actual saved image file.')
    path=Path(data['file']).resolve()
    digest=sha256(path)
    if task.get('result'):
        if digest!=task['result']['sha256']: raise ValueError('The first registered output is immutable.')
        if binding.enabled(state):binding.validate_return(work,state,task,data,path,digest)
        if authorization is not None:
            _acknowledge_violations(task,authorization,'accept_existing_output_without_generation')
            save_state(work,state,'generation_violation_acknowledged')
        if execution.enabled(state) and state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
            before=len(task.get('prompt_followup_reports',[]))
            execution.audit_returned_prompt(work,task,data,digest)
            if len(task.get('prompt_followup_reports',[]))!=before:
                report=task['prompt_followup_reports'][-1]
                state['warnings'].append({'code':'post_return_prompt_followup','task_id':task['id'],
                    'attempt':task['attempt'],'output_sha256':digest,'kind':report['kind'],
                    'reported_prompt_sha256':report['reported_prompt_sha256'],
                    'provenance':execution.PROMPT_AUDIT_PROVENANCE,
                    'message':'A later caller-reported executed prompt was retained separately. The first audit snapshot and output remain unchanged; no new image call is authorized.'})
                save_state(work,state,'prompt_report_supplemented')
        return
    key='magenta' if task['role']=='foreground' else task['board']['key'] if task['role']=='board' else None
    native_normalized=None
    bound=None
    try:
        if execution.enabled(state) and state.get('post_return_prompt_policy')!=execution.POST_RETURN_PROMPT_POLICY:
            execution.check_reported_prompt(work,task,data)
        _reject_reference_board_return(work,task,path,digest)
        if binding.enabled(state):bound=binding.validate_return(work,state,task,data,path,digest)
        validation=(validate_wordart_output(path) if task['role']=='wordart' else
                    validate_native_output(path,role=task['role']) if _native_material_task(task) else
                    validate_output(path,key=key))
        if task['role']=='foreground' and _native_material_task(task):
            native_normalized=Path(work)/'layers'/'foreground.png'
            normalize_layer(path,task['role'],state['source']['transform'],native_normalized,
                            background_mode=MATERIAL_MODE)
            validate_native_output(native_normalized,role='foreground')
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
        'output_count':data.get('output_count'),
        'validation':validation}
    if bound is not None:result.update(bound)
    if evidence is not None:
        result.update(observed_returned_paths=len(evidence['paths']),
                      observed_unique_returned_files=len({digest for _,digest in evidence['known']}))
    if task['role'] in {'background','foreground'}:
        normalized=Path(work)/'layers'/(task['role']+'.png')
        if native_normalized is None:
            normalize_layer(raw,task['role'],state['source']['transform'],normalized)
        result.update(normalized=normalized.relative_to(work).as_posix(),normalized_sha256=sha256(normalized))
    if execution.enabled(state) and state.get('post_return_prompt_policy')==execution.POST_RETURN_PROMPT_POLICY:
        audit=execution.audit_returned_prompt(work,task,data,digest)
        result['post_return_prompt_status']=audit['status']
        if audit['status']=='mismatch':
            state['warnings'].append({'code':'post_return_prompt_mismatch','task_id':task['id'],
                'attempt':task['attempt'],'output_sha256':digest,'provenance':execution.PROMPT_AUDIT_PROVENANCE,
                'message':'The caller-reported executed prompt differs from the locked request. This existing technically valid output was retained without a new image call; exact prompt compliance is not established.'})
    task.update(status='recorded',result=result)
    if execution.enabled(state):
        task['execution']['state']='recorded'
        result.update(invocation_request_file=task['execution']['request_file'],
                      invocation_request_sha256=task['execution']['request_sha256'],
                      request_provenance=execution.PROVENANCE)
    if authorization is not None:
        _acknowledge_violations(task,authorization,'accept_existing_output_without_generation')
    task['attempts'].append({**observation,'outcome':'recorded','raw':result['raw'],'sha256':digest})
    task.pop('failure',None)
    state['warnings'].extend(validation.get('warnings',[]))
    save_state(work,state,'image_registered')


def run(work, *, source=None, data=None, retry_task=None, retry_authorization=None,
        execution_policy=None,expected_package=None,post_return_prompt_policy=None,delivery_policy=None):
    """Advance only explicitly identified work; external generation stays external."""
    if sum(value is not None for value in (source,data,retry_task))>1:
        raise ValueError('Use a source, a submission, or an explicit retry, one at a time.')
    if retry_authorization is not None and retry_task is None:
        raise ValueError('retry_authorization applies only to the named --retry-task.')
    if execution_policy not in (None,execution.MODE):raise ValueError('Unsupported execution policy.')
    execution.validate_prompt_policy(post_return_prompt_policy)
    binding.validate_policy(delivery_policy)
    work=Path(work).resolve()
    if post_return_prompt_policy is not None and not (work/'state.json').exists() and not (work/'initialization.json').exists() and execution_policy!=execution.MODE:
        raise ValueError('The post-return prompt policy requires an explicit execution policy.')
    if delivery_policy is not None and not (work/'state.json').exists() and not (work/'initialization.json').exists() and execution_policy!=execution.MODE:
        raise ValueError('The delivery policy requires an explicit execution policy.')
    initial_identity=None
    if execution_policy==execution.MODE and not (work/'state.json').exists():
        if work.is_relative_to(ROOT):raise ValueError('Keep the job directory outside the runtime program directory.')
        initial_identity=version.runtime_identity(expected_package)
    elif expected_package is not None:
        initial_identity=version.runtime_identity(expected_package)
    if not (work/'state.json').exists() and source is None and not (work/'initialization.json').exists():
        raise ValueError('A new job requires the current user image; no example is selected.')
    with job_lock(work):
        if (work/'state.json').exists():
            state=read_state(work)
            if execution_policy is not None and state.get('execution_policy')!=execution_policy:
                raise ValueError('A saved job cannot change execution policy or be silently migrated.')
            execution.frozen_prompt_policy(work,state,post_return_prompt_policy)
            binding.frozen_policy(work,state,delivery_policy)
            if execution.enabled(state):
                if version.runtime_identity(expected_package)!=state.get('runtime_identity'):
                    raise ValueError('The runtime identity changed; use this job with its original verified package.')
                execution.integrity(work,state)
            _material_integrity(state)
            if source is not None and pixel_identity(source)!=state['source']['pixel_sha256']:
                raise ValueError('This image differs from the job source. Use a new empty directory.')
            if state.get('done'):return _response(state,_done(state))
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
                marker_policy=marker.get('execution_policy')
                if marker_policy not in (None,execution.MODE):raise ValueError('Unsupported initialization execution policy.')
                if execution_policy is not None and execution_policy!=marker_policy:
                    raise ValueError('An interrupted initialization cannot change execution policy.')
                execution_policy=marker_policy
                marker_prompt_policy=execution.validate_prompt_policy(marker.get('post_return_prompt_policy'))
                if post_return_prompt_policy is not None and post_return_prompt_policy!=marker_prompt_policy:
                    raise ValueError('An interrupted initialization cannot change post-return prompt policy.')
                post_return_prompt_policy=marker_prompt_policy
                marker_delivery_policy=binding.validate_policy(marker.get('delivery_policy'))
                if delivery_policy is not None and delivery_policy!=marker_delivery_policy:
                    raise ValueError('An interrupted initialization cannot change delivery policy.')
                delivery_policy=marker_delivery_policy
                if delivery_policy is not None and execution_policy!=execution.MODE:
                    raise ValueError('The delivery policy requires explicit execution records.')
                if post_return_prompt_policy is not None and execution_policy!=execution.MODE:
                    raise ValueError('The post-return prompt policy requires explicit execution records.')
                if execution_policy==execution.MODE:
                    if work.is_relative_to(ROOT):raise ValueError('Keep the job directory outside the runtime program directory.')
                    initial_identity=version.runtime_identity(expected_package)
                    if initial_identity!=marker.get('runtime_identity'):
                        raise ValueError('The initialization runtime identity changed.')
            else:
                if any(p.name!='.lock' for p in work.iterdir()):raise ValueError('A new job needs an empty directory.')
                source=Path(source).resolve()
                byte_hash=sha256(source);pixel_hash=pixel_identity(source)
                if sha256(source)!=byte_hash:raise ValueError('The source changed while initializing.')
                marker={'format':'newpath.start/1','input_path':str(source),
                    'snapshot':'input/original'+(source.suffix.lower() or '.bin'),
                    'created_at_utc':datetime.now(timezone.utc).isoformat(),
                    'byte_sha256':byte_hash,'pixel_sha256':pixel_hash}
                if execution_policy==execution.MODE:
                    marker.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(initial_identity))
                if post_return_prompt_policy is not None:
                    marker['post_return_prompt_policy']=post_return_prompt_policy
                if delivery_policy is not None:marker['delivery_policy']=delivery_policy
                write_json(initialization,marker)
            _runtime()
            src=prepare_source(source,work)
            if src['byte_sha256']!=marker['byte_sha256'] or src['pixel_sha256']!=marker['pixel_sha256']:
                raise ValueError('The source changed after initialization.')
            src['input_path']=marker['input_path']
            state={'format':'newpath.job/1','phase':'layers','source':src,'tasks':{},'layout':None,
                'generation_policy':GENERATION_POLICY,
                'material_background_mode':MATERIAL_MODE,
                'assets':[],'warnings':[],'processing_seconds':[],'active_batch':[],
                'started_at_utc':marker.get('created_at_utc',datetime.now(timezone.utc).isoformat()),'events':[]}
            if execution_policy==execution.MODE:
                state.update(execution_policy=execution.MODE,runtime_identity=copy.deepcopy(initial_identity))
            if post_return_prompt_policy is not None:
                state['post_return_prompt_policy']=post_return_prompt_policy
            if delivery_policy is not None:state['delivery_policy']=delivery_policy
            for role in ('background','foreground'):
                mode={'background_mode':MATERIAL_MODE} if role=='foreground' else {}
                identity={'source_pixel_sha256':src['pixel_sha256'],**mode} if mode else src['pixel_sha256']
                task=_task(work,role,identity,layer_brief(role,src['transform'],**mode),
                           [relative_file(work,src['working'])],**mode)
                if execution.enabled(state):execution.enroll(task)
                if binding.enabled(state):task['delivery_policy']=binding.DELIVERY_POLICY
                state['tasks'][task['id']]=task
            save_state(work,state,'source_prepared')
        if retry_task is not None:
            if execution.enabled(state) and state.get('execution_hold'):
                raise ValueError('Remaining generation is paused by the user; resume_unstarted before a retry.')
            task=state['tasks'].get(retry_task)
            if not task or task['status']=='recorded':raise ValueError('Only an unfinished task can be explicitly reopened.')
            if task['role'] in {'board','wordart'} and task['id'] not in state['active_batch']:
                raise ValueError('Retry only an unfinished member of the active batch.')
            if _strict_generation(state):
                if task['status']!='failed':
                    raise ValueError('Only a failed task may be retried; an issued waiting task must be awaited or registered.')
                authorization=_authorization(retry_authorization,'retry_authorization')
                if any(identity!=task['id'] for identity in _pending_generation_violations(state)):
                    raise ValueError('Resolve the other task generation violation before authorizing a new invocation.')
                task.setdefault('retry_authorizations',[]).append({'attempt':task['attempt']+1,
                    'authorization':authorization,'provenance':'caller_reported_user_request_not_verified'})
                _acknowledge_violations(task,authorization,'explicit_user_authorized_retry')
            task.update(status='waiting',issued=True,attempt=task['attempt']+1)
            if execution.enabled(state):execution.reset_for_retry(task)
            task.pop('failure',None)
            save_state(work,state,'explicit_retry')
            return _response(state,{'kind':'generate','jobs':[_public_task(work,task)],'instructions':'Explicitly reopened task only; keep every completed result.'})
        if data is not None:
            immediate=_submit(work,state,data)
            if immediate is not None:return _response(state,immediate)
        return _response(state,_drive(work,state))
