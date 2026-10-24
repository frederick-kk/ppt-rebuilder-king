"""Local output ownership and caller-declared role review, never provider proof."""
import copy
import hashlib
import json
from pathlib import Path

from PIL import Image

from .storage import relative_file,sha256

DELIVERY_POLICY='reviewed-existing-output/1'
PROVENANCE='local_task_binding_and_host_visual_declaration_not_provider_verified'
REVIEW_SCOPE='Assigned role and subjects only; not complete reconstruction fidelity, automatic vision certification, or provider identity proof.'


class BindingViolation(ValueError):
    """Source substitution or contradictory local task ownership is not salvage."""


def validate_policy(policy):
    if policy not in (None,DELIVERY_POLICY):raise ValueError('Unsupported delivery policy.')
    return policy


def enabled(state):return validate_policy(state.get('delivery_policy'))==DELIVERY_POLICY


def frozen_policy(work,state,requested=None):
    policy=validate_policy(state.get('delivery_policy'));validate_policy(requested)
    if requested is not None and requested!=policy:raise ValueError('A saved job cannot change delivery policy.')
    marker=Path(work)/'initialization.json'
    marker_policy=validate_policy(json.loads(marker.read_text(encoding='utf-8')).get('delivery_policy')) if marker.is_file() else None
    if marker_policy!=policy:raise ValueError('The frozen delivery policy differs from its initialization marker.')
    if any(task.get('delivery_policy')!=policy for task in state.get('tasks',{}).values()):
        raise ValueError('A task differs from the frozen delivery policy; no downgrade or migration is permitted.')
    if policy is not None and state.get('execution_policy')!='explicit-start/1':
        raise ValueError('The delivery policy requires explicit execution records.')


def review_template(task):
    review={'role':task['role'],'output_sha256':'','looked_at_return':False,'matches_task':False,'observed':''}
    if task['role']=='background':review['foreground_text_absent']=False
    elif task['role']=='foreground':review['page_elements_match']=False
    elif task['role']=='wordart':review.update(lettering_matches=False,only_assigned_title=False)
    elif task['role']=='board':review.update(matched_members=[],ordinary_text_removed=False)
    return review


def decorate_task(task,public):
    public['delivery_policy']=DELIVERY_POLICY
    public['role_review_template']=review_template(task)
    public['role_review_scope']=REVIEW_SCOPE
    public['result_template']['request_sha256']=task.get('execution',{}).get('request_sha256','')
    if task.get('execution',{}).get('request_sha256'):
        public['result_template']['request_sha256']=task['execution']['request_sha256']
    public['execution_contract']['instructions']+=' Inspect this task\'s actual saved return before registration. Bind its request SHA and output SHA; report role_review from the viewed pixels, not from a caption or prompt. Wrong-role/absent-subject returns are real failures. Role matching is not complete fidelity certification. Preserve observed defects in notes.'
    public['execution_contract']['instructions']=public['execution_contract']['instructions'].replace(
        'If the tool returns extra images, preserve the known files, report their count and stop.',
        'If one actual invocation unexpectedly returns multiple images, preserve all originals in actual return order and submit returned_outputs with independent role_review for each. The frozen delivery policy may adopt the first usable existing return; this never authorizes an extra call. Multiple or unknown invocations still pause.')
    return public


def decorate_permit(task,permit):
    permit['result_template']['request_sha256']=task['execution']['request_sha256']
    permit['role_review_template']=review_template(task)
    permit['role_review_scope']=REVIEW_SCOPE
    permit['delivery_policy']=DELIVERY_POLICY
    return permit


def pixel_digest(path):
    try:
        with Image.open(path) as im:
            im.load();rgba=im.convert('RGBA')
            h=hashlib.sha256();h.update(str(rgba.size).encode('ascii'));h.update(rgba.tobytes())
            return h.hexdigest()
    except Image.DecompressionBombError as exc:
        raise ValueError('Image dimensions exceed safe decoding limits for result binding.') from exc


def review_evidence(review):
    """Keep only documented, bounded declaration fields, including false ones."""
    if not isinstance(review,dict):return None
    fields={'role','output_sha256','looked_at_return','matches_task','observed','foreground_text_absent',
            'page_elements_match','lettering_matches','only_assigned_title','ordinary_text_removed','matched_members','notes'}
    result={}
    for key in fields & review.keys():
        value=review[key]
        if type(value) is bool or value is None:result[key]=value
        elif isinstance(value,str):result[key]=value[:4000]
        elif isinstance(value,list) and len(value)<=100 and all(isinstance(v,str) and len(v)<=2000 for v in value):
            result[key]=list(value)
    return result


def normalize_known_returns(data):
    """Never ignore a declared output list because a template count was stale."""
    data=copy.deepcopy(data)
    data.pop('_returned_outputs_inconsistent',None)
    if 'returned_outputs' not in data:return data
    outputs=data['returned_outputs']
    valid=isinstance(outputs,list) and len(outputs)>=2
    entries=outputs if isinstance(outputs,list) else []
    paths=[entry['file'] for entry in entries if isinstance(entry,dict)
           and isinstance(entry.get('file'),str) and entry['file']]
    valid=valid and len(paths)==len(entries) and data.get('output_count')==len(entries)
    given=data.get('returned_files')
    if given is None:data['returned_files']=list(paths)
    elif isinstance(given,list) and all(isinstance(p,str) for p in given):
        valid=valid and [str(Path(p).resolve()) for p in given]==[str(Path(p).resolve()) for p in paths]
        data['returned_files']=list(given)+[p for p in paths if p not in given]
    else:
        valid=False;data['returned_files']=list(paths)
    if data.get('file') and paths:
        valid=valid and str(Path(data['file']).resolve())==str(Path(paths[0]).resolve())
    elif paths:data['file']=paths[0]
    data['_returned_outputs_inconsistent']=not valid
    return data


def claim_return(work,state,task,data,path,digest):
    """Bind every declared existing return, including rejected/unselected ones."""
    if data.get('request_sha256')!=task['execution']['request_sha256']:
        raise BindingViolation('The result request binding differs from this exact task invocation.')
    request=json.loads(relative_file(work,task['execution']['request_file']).read_text(encoding='utf-8'))
    tool=request['request']['tool']
    if data.get('tool') is not None and data['tool']!=tool:
        raise BindingViolation('The reported result tool differs from this task request binding.')
    output_id=data.get('tool_output_id')
    if output_id is not None and (not isinstance(output_id,str) or not output_id.strip() or len(output_id)>512):
        raise BindingViolation('tool_output_id must be a known nonempty identifier, or unknown/null.')
    decode_error=None
    try:pixels=pixel_digest(path)
    except (OSError,ValueError) as exc:pixels=None;decode_error=exc
    references=dict(task['references'])
    for key in ('original','working','guide'):
        name=state.get('source',{}).get(key)
        if isinstance(name,str):references[name]=sha256(relative_file(work,name))
    for name,reference_sha in references.items():
        reference=relative_file(work,name)
        if digest==reference_sha or (pixels is not None and pixels==pixel_digest(reference)):
            raise BindingViolation('A source/reference input cannot be used as a generated output, including re-encoded input.')
    for other in state['tasks'].values():
        if other['id']==task['id']:continue
        identities=list(other.get('return_bindings',[]))
        if other.get('result'):identities.append(other['result'])
        for identity in identities:
            other_sha=identity.get('output_sha256',identity.get('sha256'));other_pixels=identity.get('pixel_sha256')
            if other_sha==digest or (pixels is not None and other_pixels==pixels):
                raise BindingViolation('This output is already bound to another task (cross-task file/pixel reuse).')
            if output_id is not None and identity.get('tool_output_id')==output_id:
                if identity.get('binding_tool',identity.get('tool'))==tool:
                    raise BindingViolation('This tool output identifier is already bound to another task.')
    claims=task.setdefault('return_bindings',[])
    identity={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
              'output_sha256':digest,'pixel_sha256':pixels,'binding_tool':tool,'tool_output_id':output_id,
              'request_sha256':task['execution']['request_sha256'],'provenance':PROVENANCE}
    if identity not in claims:claims.append(identity)
    if decode_error is not None:raise decode_error
    return pixels,tool,request


def validate_return(work,state,task,data,path,digest):
    pixels,tool,request=claim_return(work,state,task,data,path,digest)
    review=data.get('role_review')
    if not isinstance(review,dict):raise ValueError('A role_review based on the actual saved return is required.')
    if review.get('role')!=task['role'] or review.get('output_sha256')!=digest:
        raise ValueError('The role review is not bound to this role and actual output SHA.')
    if any(entry['attempt']==task['attempt'] and (
            entry['output_sha256']==digest
            or (pixels is not None and entry.get('pixel_sha256')==pixels)
            or (data.get('tool_output_id') is not None
                and entry.get('tool_output_id')==data['tool_output_id']
                and entry.get('binding_tool')==tool))
           for entry in task.get('role_review_rejections',[])):
        raise ValueError('This actual output was explicitly viewed and rejected for this task subject; a later true declaration cannot erase that failure.')
    required={'background':['foreground_text_absent'],'foreground':['page_elements_match'],
              'wordart':['lettering_matches','only_assigned_title'],'board':['ordinary_text_removed']}[task['role']]
    if review.get('looked_at_return') is True and (review.get('matches_task') is False or any(review.get(key) is False for key in required)):
        rejection={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'output_sha256':digest,
                   'pixel_sha256':pixels,'binding_tool':tool,'tool_output_id':data.get('tool_output_id'),
                   'matches_task':review.get('matches_task'),'looked_at_return':True,'provenance':PROVENANCE,
                   'observed':review.get('observed')[:4000] if isinstance(review.get('observed'),str) else None,
                   'explicit_false_checks':[key for key in ['matches_task',*required] if review.get(key) is False]}
        task.setdefault('role_review_rejections',[]).append(rejection)
        raise ValueError('The viewed actual return failed its role/subject review; preserve this real failure.')
    if review.get('looked_at_return') is not True or review.get('matches_task') is not True:
        raise ValueError('The actual return did not pass the host role/subject review; preserve this real failure.')
    observed=review.get('observed')
    if not isinstance(observed,str) or not observed.strip() or len(observed)>4000:
        raise ValueError('The role review needs a short concrete observation of the saved return.')
    if observed in (request['request']['prompt'],data.get('executed_prompt')):
        raise ValueError('A role review must describe the viewed return, not copy a prompt or tool caption.')
    if any(review.get(field) is not True for field in required):
        raise ValueError('The saved return failed required role review checks: '+', '.join(required))
    if task['role']=='board':
        members=[m['id'] for m in task['board']['members']];matched=review.get('matched_members')
        if not isinstance(matched,list) or any(not isinstance(v,str) for v in matched) or len(matched)!=len(members) or set(matched)!=set(members):
            raise ValueError('The role review must match every assigned board member and no other member.')
    notes=review.get('notes',[])
    if not isinstance(notes,list) or len(notes)>20 or any(not isinstance(n,str) or not n.strip() or len(n)>2000 for n in notes):
        raise ValueError('Role review notes must be a short list of actual observed limitations.')
    fields=['role','output_sha256','looked_at_return','matches_task','observed',*required]
    if task['role']=='board':fields.append('matched_members')
    normalized={key:copy.deepcopy(review[key]) for key in fields};normalized['notes']=list(notes)
    return {'pixel_sha256':pixels,'binding_tool':tool,'role_review':normalized,
            'role_review_provenance':PROVENANCE,'role_review_scope':REVIEW_SCOPE,
            'request_sha256':task['execution']['request_sha256']}


def summary(state):
    entries=[{'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
              'output_sha256':task['result']['sha256'],'role_review':copy.deepcopy(task['result']['role_review'])}
             for task in state['tasks'].values() if task.get('result',{}).get('role_review')]
    return {'policy':DELIVERY_POLICY,'provenance':PROVENANCE,'scope':REVIEW_SCOPE,'entries':entries,
            'reviewed_outputs':len(entries),'surplus_resolutions':[{'task_id':t['id'],**copy.deepcopy(t['surplus_resolution'])}
                for t in state['tasks'].values() if t.get('surplus_resolution')]}
