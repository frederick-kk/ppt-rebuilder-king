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


class RegistrationRequired(ValueError):
    """Existing output needs completed host observations, not another call."""


class ReviewOutputHashRequired(RegistrationRequired):
    """A positive review used this output's pixel digest as its file digest."""
    kind='review_output_sha256_is_pixel_digest'


class RoleMismatch(ValueError):
    """A viewed output explicitly failed its assigned role or subjects."""


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
    review={'role':task['role'],'output_sha256':task.get('registration',{}).get('sha256',''),'looked_at_return':None,'matches_task':None,'observed':''}
    if task['role']=='background':review['foreground_text_absent']=None
    elif task['role']=='foreground':review['page_elements_match']=None
    elif task['role']=='wordart':review.update(lettering_matches=None,only_assigned_title=None)
    elif task['role']=='board':
        members=(task.get('board') or {}).get('members')
        review.update(matched_members=[member['id'] for member in members] if members is not None else None,
                      ordinary_text_removed=None)
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
    if review.get('observed_cell_members') is not None:
        fields.add('observed_cell_members')
    result={}
    for key in fields & review.keys():
        value=review[key]
        if type(value) is bool or value is None:result[key]=value
        elif isinstance(value,str):result[key]=value[:4000]
        elif isinstance(value,list) and len(value)<=100 and all(isinstance(v,str) and len(v)<=2000 for v in value):
            result[key]=list(value)
    return result


def _known_output_id(value):
    if isinstance(value,str) and value.strip().casefold() in {'','unknown','null','none','n/a'}:
        return None
    return value


def _merge_role_reviews(first,second):
    """Combine compatible observations without inventing or reversing checks."""
    if not isinstance(first,dict) or not isinstance(second,dict):
        return copy.deepcopy(first),False
    merged=copy.deepcopy(first)
    for field,value in second.items():
        current=merged.get(field)
        empty_string=field in {'role','output_sha256','observed'}
        if current is None or (empty_string and current==''):
            merged[field]=copy.deepcopy(value)
            continue
        if value is None or (empty_string and value==''):
            continue
        if field=='observed' and isinstance(current,str) and isinstance(value,str):
            continue
        if field=='notes' and isinstance(current,list) and isinstance(value,list):
            for note in value:
                if note not in current:current.append(copy.deepcopy(note))
            continue
        if field=='matched_members':
            if (isinstance(current,list) and isinstance(value,list)
                    and all(isinstance(member,str) for member in current+value)
                    and len(current)==len(value) and set(current)==set(value)):
                continue
            return merged,False
        if type(current) is not type(value) or current!=value:
            return merged,False
    return merged,True


def normalize_known_returns(data):
    """Never ignore a declared output list because a template count was stale."""
    data=copy.deepcopy(data)
    data.pop('_returned_outputs_inconsistent',None)
    data.pop('tool_output_id_declarations',None)
    data.pop('role_review_declarations',None)
    if 'returned_outputs' not in data:return data
    outputs=data['returned_outputs']
    empty_failure=(outputs==[] and data.get('outcome')=='failed'
                   and data.get('invocation_count')==1 and data.get('output_count')==0
                   and not data.get('file') and not data.get('returned_files'))
    valid=isinstance(outputs,list) and (len(outputs)>=1 or empty_failure)
    entries=outputs if isinstance(outputs,list) else []
    paths=[entry['file'] for entry in entries if isinstance(entry,dict)
           and isinstance(entry.get('file'),str) and entry['file']]
    reported_count=data.get('output_count')
    count_matches=reported_count==len(entries) or (len(entries)==1 and reported_count is None)
    valid=valid and len(paths)==len(entries) and count_matches
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
    if len(entries)==1 and isinstance(entries[0],dict):
        declarations={}
        if 'tool_output_id' in data:declarations['top_level']=copy.deepcopy(data['tool_output_id'])
        if 'tool_output_id' in entries[0]:declarations['returned_output']=copy.deepcopy(entries[0]['tool_output_id'])
        if declarations:data['tool_output_id_declarations']=declarations
        reviews={}
        if 'role_review' in data:reviews['top_level']=copy.deepcopy(data['role_review'])
        if 'role_review' in entries[0]:reviews['returned_output']=copy.deepcopy(entries[0]['role_review'])
        if reviews:data['role_review_declarations']=reviews
        for field in ('role_review','tool','tool_output_id'):
            nested=entries[0].get(field)
            current=data.get(field)
            if current is None:
                if nested is not None:data[field]=copy.deepcopy(nested)
            elif nested is not None:
                if field=='tool_output_id':
                    current_id=_known_output_id(current)
                    nested_id=_known_output_id(nested)
                    if current_id is None:
                        data[field]=copy.deepcopy(nested)
                    elif nested_id is not None:
                        valid=valid and current_id==nested_id
                elif field=='role_review':
                    merged,compatible=_merge_role_reviews(current,nested)
                    data[field]=merged
                    valid=valid and compatible
                else:
                    valid=valid and current==nested
    data['_returned_outputs_inconsistent']=not valid
    return data


def _derived_inputs(work, state):
    """Identify saved local derivatives, including an interrupted keyed preparation."""
    known = {}
    for owner in state['tasks'].values():
        result = owner.get('result') or {}
        for field, hash_field in (('material_file', 'material_sha256'), ('normalized', 'normalized_sha256')):
            name = result.get(field)
            if isinstance(name, str):
                derived = relative_file(work, name)
                digest = sha256(derived) if derived.is_file() else result.get(hash_field)
                known[(owner['id'], str(derived))] = (owner['id'], derived, digest, result.get('sha256'))
        if not owner.get('keyed_material_policy'):
            continue
        directory = relative_file(work, 'materials')
        prefix = owner['id'] + '-'
        for derived in directory.glob(prefix + '*.png'):
            attempt, separator, source_prefix = derived.stem[len(prefix):].partition('-')
            if (separator and attempt.isdecimal() and len(source_prefix) == 16
                    and all(character in '0123456789abcdef' for character in source_prefix)
                    and derived.is_file()):
                known.setdefault((owner['id'], str(derived)),
                                 (owner['id'], derived, sha256(derived), source_prefix))
        if owner['role'] in ('background', 'foreground'):
            derived = relative_file(work, 'layers/' + owner['role'] + '.png')
            if derived.is_file():
                known.setdefault((owner['id'], str(derived)),
                                 (owner['id'], derived, sha256(derived), None))
    return list(known.values())


def claim_return(work,state,task,data,path,digest,*,incomplete_registration=False):
    """Bind every declared existing return, including rejected/unselected ones."""
    missing_request=data.get('request_sha256') in (None,'')
    if data.get('request_sha256')!=task['execution']['request_sha256'] and not (incomplete_registration and missing_request):
        raise BindingViolation('The result request binding differs from this exact task invocation.')
    request=json.loads(relative_file(work,task['execution']['request_file']).read_text(encoding='utf-8'))
    tool=request['request']['tool']
    if data.get('tool') is not None and data['tool']!=tool:
        raise BindingViolation('The reported result tool differs from this task request binding.')
    reported_output_id=data.get('tool_output_id')
    output_id=_known_output_id(reported_output_id)
    if output_id is not None and (not isinstance(output_id,str) or not output_id.strip() or len(output_id)>512):
        invalid=RegistrationRequired if incomplete_registration else BindingViolation
        raise invalid('tool_output_id must be a known nonempty identifier, or unknown/null.')
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
            if output_id is not None and _known_output_id(identity.get('tool_output_id'))==output_id:
                if identity.get('binding_tool',identity.get('tool'))==tool:
                    raise BindingViolation('This tool output identifier is already bound to another task.')
    derivatives = _derived_inputs(work, state)
    own_raw_hashes = {entry.get('output_sha256') for entry in task.get('return_bindings', [])}
    if task.get('result'):
        own_raw_hashes.add(task['result'].get('sha256'))
    # A native pass-through can be byte-identical to its real input. Keep that
    # actual input usable outside the program's derivative paths.
    own_raw = digest in own_raw_hashes or any(
        owner_id == task['id'] and source_prefix and digest.startswith(source_prefix) and digest == derived_sha
        for owner_id, _, derived_sha, source_prefix in derivatives)
    for owner_id, derived, derived_sha, _ in derivatives:
        if Path(path).resolve() == derived.resolve():
            raise BindingViolation('A program-derived material or normalized layer cannot be a generated raw return.')
        if owner_id == task['id'] and own_raw:
            continue
        if digest == derived_sha or (pixels is not None and derived.is_file() and pixels == pixel_digest(derived)):
            raise BindingViolation('A known program-derived material or normalized layer cannot be reused as a generated raw return, including re-encoding.')
    if incomplete_registration and missing_request:
        if decode_error is not None:raise decode_error
        return pixels,tool,request
    claims=task.setdefault('return_bindings',[])
    identity={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
              'output_sha256':digest,'pixel_sha256':pixels,'binding_tool':tool,'tool_output_id':output_id,
              'request_sha256':task['execution']['request_sha256'],'provenance':PROVENANCE}
    if reported_output_id!=output_id:identity['reported_tool_output_id']=reported_output_id
    if data.get('tool_output_id_declarations'):
        identity['tool_output_id_declarations']=copy.deepcopy(data['tool_output_id_declarations'])
    if data.get('role_review_declarations'):
        identity['role_review_declarations']=copy.deepcopy(data['role_review_declarations'])
    if identity not in claims:claims.append(identity)
    if decode_error is not None:raise decode_error
    return pixels,tool,request


def validate_return(work,state,task,data,path,digest,*,incomplete_registration=False):
    pixels,tool,request=claim_return(work,state,task,data,path,digest,
                                   incomplete_registration=incomplete_registration)
    review=data.get('role_review')
    output_id=_known_output_id(data.get('tool_output_id'))
    incomplete=RegistrationRequired if incomplete_registration else ValueError
    if not isinstance(review,dict):raise incomplete('A role_review based on the actual saved return is required.')
    required={'background':['foreground_text_absent'],'foreground':['page_elements_match'],
              'wordart':['lettering_matches','only_assigned_title'],'board':['ordinary_text_removed']}[task['role']]
    if incomplete_registration and review.get('role') not in (None,'',task['role']):
        raise BindingViolation('The role review contradicts this exact role or actual output SHA binding.')
    if incomplete_registration and review.get('output_sha256') not in (None,'',digest):
        if (pixels is not None and review.get('output_sha256')==pixels and review.get('role')==task['role']
                and all(review.get(field) is True for field in ['looked_at_return','matches_task',*required])):
            raise ReviewOutputHashRequired('The positive role review used this file\'s pixel digest. Complete output_sha256 with the actual file SHA and register the same saved bytes.')
        raise BindingViolation('The role review contradicts this exact role or actual output SHA binding.')
    if not incomplete_registration and (review.get('role')!=task['role'] or review.get('output_sha256')!=digest):
        raise ValueError('The role review is not bound to this role and actual output SHA.')
    if any(entry['attempt']==task['attempt'] and (
            entry['output_sha256']==digest
            or (pixels is not None and entry.get('pixel_sha256')==pixels)
            or (output_id is not None
                and _known_output_id(entry.get('tool_output_id'))==output_id
                and entry.get('binding_tool')==tool))
           for entry in task.get('role_review_rejections',[])):
        raise RoleMismatch('This actual output was explicitly viewed and rejected for this task subject; a later true declaration cannot erase that failure.')
    if review.get('looked_at_return') is True and (review.get('matches_task') is False or any(review.get(key) is False for key in required)):
        rejection={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],'output_sha256':digest,
                   'pixel_sha256':pixels,'binding_tool':tool,'tool_output_id':output_id,
                   'matches_task':review.get('matches_task'),'looked_at_return':True,'provenance':PROVENANCE,
                   'observed':review.get('observed')[:4000] if isinstance(review.get('observed'),str) else None,
                   'explicit_false_checks':[key for key in ['matches_task',*required] if review.get(key) is False]}
        task.setdefault('role_review_rejections',[]).append(rejection)
        raise RoleMismatch('The viewed actual return failed its role/subject review; preserve this real failure.')
    if incomplete_registration and (not review.get('role') or not review.get('output_sha256')):
        raise RegistrationRequired('Complete the role review role and actual output SHA binding.')
    if incomplete_registration and not data.get('request_sha256'):
        raise RegistrationRequired('Complete the result request_sha256 using the recorded invocation hash.')
    if review.get('looked_at_return') is not True or review.get('matches_task') is not True:
        raise incomplete('The actual return needs a completed host role/subject review.')
    observed=review.get('observed')
    if not isinstance(observed,str) or not observed.strip() or len(observed)>4000:
        raise incomplete('The role review needs a short concrete observation of the saved return.')
    if observed in (request['request']['prompt'],data.get('executed_prompt')):
        raise incomplete('A role review must describe the viewed return, not copy a prompt or tool caption.')
    if any(review.get(field) is not True for field in required):
        raise incomplete('Complete required role review checks: '+', '.join(required))
    if task['role']=='board':
        members=[m['id'] for m in task['board']['members']];matched=review.get('matched_members')
        if not isinstance(matched,list) or any(not isinstance(v,str) for v in matched) or len(matched)!=len(members) or set(matched)!=set(members):
            if incomplete_registration and isinstance(matched,list) and matched:
                raise RoleMismatch('The viewed board does not match every assigned member.')
            raise incomplete('The role review must match every assigned board member and no other member.')
        observed_cells=review.get('observed_cell_members')
        if observed_cells is not None:
            if (not isinstance(observed_cells,list) or len(observed_cells)!=len(members)
                    or any(not isinstance(member,str) for member in observed_cells)
                    or len(set(observed_cells))!=len(observed_cells) or set(observed_cells)!=set(members)):
                raise RegistrationRequired('observed_cell_members must list each assigned board member exactly once in observed row-major cell order.')
    notes=review.get('notes',[])
    if not isinstance(notes,list) or len(notes)>20 or any(not isinstance(n,str) or not n.strip() or len(n)>2000 for n in notes):
        raise incomplete('Role review notes must be a short list of actual observed limitations.')
    fields=['role','output_sha256','looked_at_return','matches_task','observed',*required]
    if task['role']=='board':fields.append('matched_members')
    if task['role']=='board' and review.get('observed_cell_members') is not None:
        fields.append('observed_cell_members')
    normalized={key:copy.deepcopy(review[key]) for key in fields};normalized['notes']=list(notes)
    result={'pixel_sha256':pixels,'binding_tool':tool,'role_review':normalized,
            'tool_output_id':output_id,'identity_scope':'local_file' if output_id is None else 'caller_reported',
            'role_review_provenance':PROVENANCE,'role_review_scope':REVIEW_SCOPE,
            'request_sha256':task['execution']['request_sha256']}
    if data.get('tool_output_id')!=output_id:result['reported_tool_output_id']=data['tool_output_id']
    if data.get('tool_output_id_declarations'):
        result['tool_output_id_declarations']=copy.deepcopy(data['tool_output_id_declarations'])
    if data.get('role_review_declarations'):
        result['role_review_declarations']=copy.deepcopy(data['role_review_declarations'])
    return result


def summary(state):
    entries=[{'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
              'output_sha256':task['result']['sha256'],'role_review':copy.deepcopy(task['result']['role_review'])}
             for task in state['tasks'].values() if task.get('result',{}).get('role_review')]
    return {'policy':DELIVERY_POLICY,'provenance':PROVENANCE,'scope':REVIEW_SCOPE,'entries':entries,
            'reviewed_outputs':len(entries),'surplus_resolutions':[{'task_id':t['id'],**copy.deepcopy(t['surplus_resolution'])}
                for t in state['tasks'].values() if t.get('surplus_resolution')]}
