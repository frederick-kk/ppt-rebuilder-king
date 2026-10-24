"""Explicit, caller-declared image invocation starts; no provider telemetry."""
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re

from .storage import fingerprint,read_state,relative_file,sha256,write_json

MODE='explicit-start/1'
POST_RETURN_PROMPT_POLICY='audit-existing-return/1'
PROVENANCE='caller_declared_before_invocation_not_provider_verified'
PROMPT_AUDIT_PROVENANCE='caller_reported_executed_prompt_not_provider_verified'
STATES={'not_started','invoking','returned','recorded','failed'}


def enabled(state):
    policy=state.get('execution_policy')
    if policy not in (None,MODE):raise ValueError('Unsupported execution policy.')
    if policy is None and (state.get('runtime_identity') is not None
            or any('execution' in task for task in state.get('tasks',{}).values())):
        raise ValueError('Explicit execution metadata cannot lose its execution policy or downgrade to a legacy job.')
    return policy==MODE


def enroll(task):
    task['execution']={'state':'not_started','attempt':task['attempt']}


def validate_prompt_policy(policy):
    if policy not in (None,POST_RETURN_PROMPT_POLICY):
        raise ValueError('Unsupported post-return prompt policy.')
    return policy


def frozen_prompt_policy(work,state,requested=None):
    """An absent old policy remains strict; a saved policy cannot be changed."""
    policy=validate_prompt_policy(state.get('post_return_prompt_policy'))
    validate_prompt_policy(requested)
    if requested is not None and requested!=policy:
        raise ValueError('A saved job cannot change its post-return prompt policy.')
    marker=Path(work)/'initialization.json'
    marker_policy=validate_prompt_policy(json.loads(marker.read_text(encoding='utf-8')).get('post_return_prompt_policy')) if marker.is_file() else None
    if policy!=marker_policy:
        raise ValueError('The frozen post-return prompt policy differs from the initialization marker.')
    if policy is not None and not enabled(state):
        raise ValueError('The post-return prompt policy requires explicit execution records.')
    return policy


def integrity(work,state):
    if not enabled(state):return
    for task in state['tasks'].values():
        current=task.get('execution')
        if not isinstance(current,dict) or current.get('state') not in STATES or current.get('attempt')!=task['attempt']:
            raise ValueError('Missing or inconsistent explicit execution record.')
        if task['status']=='recorded' and current['state']!='recorded':
            raise ValueError('A recorded image has inconsistent execution state.')
        for record in [*task.get('execution_history',[]),current]:
            if record.get('state')=='not_started':
                if record.get('request_file'):raise ValueError('An unstarted execution cannot own an invocation record.')
                continue
            path=record.get('request_file');digest=record.get('request_sha256')
            if not path or not digest or sha256(relative_file(work,path))!=digest:
                raise ValueError('A saved execution request is missing or changed.')
            saved=json.loads(relative_file(work,path).read_text(encoding='utf-8'))
            if (saved.get('task_id')!=task['id'] or saved.get('attempt')!=record['attempt']
                    or saved.get('request_fingerprint')!=record.get('request_fingerprint')
                    or fingerprint(saved.get('request'))!=record.get('request_fingerprint')):
                raise ValueError('An execution request no longer matches its saved task.')


def decorate(work,task,public):
    if 'execution' not in task:return public
    public['action']='prepare_first_invocation'
    public['execution_policy']=MODE
    public['execution_state']=task['execution']['state']
    public['begin_template']={'kind':'begin_generation','task_id':task['id'],'role':task['role'],
        'attempt':task['attempt'],'request':{'tool':'',
          'prompt':relative_file(work,task['brief']).read_text(encoding='utf-8'),
          'references':list(public['references']),'size':list(public['size']),
          'transparent_background':public['transparent_background'],'requested_outputs':1}}
    public['execution_contract']['instructions']+=' This is a preparation card, not permission to invoke the image tool yet. Submit begin_template with the exact actual request and tool name. Only the first invoke_once reply permits that one call. Repeated begin submissions and waiting never permit another call.'
    return public


def _safe_arguments(value):
    forbidden={'authorization','apikey','xapikey','accesstoken','bearertoken','token','password',
               'secret','clientsecret','headers','extraheaders','httpheaders','key','credentials','xauthtoken'}
    def visit(item):
        if isinstance(item,dict):
            for key,child in item.items():
                if not isinstance(key,str) or re.sub(r'[^a-z0-9]','',key.casefold()) in forbidden:
                    raise ValueError('Do not include credentials in image request records.')
                visit(child)
        elif isinstance(item,list):
            for child in item:visit(child)
        elif isinstance(item,str):
            if item.lstrip().startswith('data:') or len(item)>1000000:
                raise ValueError('Inline image or oversized data is not accepted in request records.')
            if re.search(r'(?i)\b(?:bearer|basic)\s+[a-z0-9._~+/=-]{8,}',item):
                raise ValueError('Do not include credentials in image request records.')
        elif item is not None and type(item) not in (bool,int,float):
            raise ValueError('Tool arguments must be ordinary JSON values.')
    visit(value)
    try:json.dumps(value,allow_nan=False)
    except (TypeError,ValueError) as exc:raise ValueError('Tool arguments must be finite JSON values.') from exc


def normalize_request(work,task,public,request):
    if not isinstance(request,dict):raise ValueError('An actual image request object is required before begin.')
    required={'tool','prompt','references','size','transparent_background','requested_outputs'}
    if not required<=set(request):raise ValueError('The image request lacks required execution parameters.')
    if set(request)-required-{'tool_arguments'}:raise ValueError('Unrecognized normalized image request fields.')
    tool=request['tool']
    if not isinstance(tool,str) or not tool.strip() or len(tool)>200:
        raise ValueError('Record the actual image tool name before begin.')
    prompt=relative_file(work,task['brief']).read_text(encoding='utf-8')
    if request['prompt']!=prompt:raise ValueError('The actual prompt must exactly match the frozen prompt_file.')
    refs=request['references']
    if not isinstance(refs,list) or any(not isinstance(p,str) for p in refs):
        raise ValueError('The actual request must retain every reference image path in order.')
    resolved=[str(Path(p).resolve()) for p in refs]
    if resolved!=public['references']:raise ValueError('The actual request references differ from the frozen task.')
    if (not isinstance(request['size'],list) or len(request['size'])!=2
            or any(type(v) is not int for v in request['size']) or request['size']!=public['size']):
        raise ValueError('The actual request size differs from the planned image canvas.')
    if type(request['transparent_background']) is not bool or request['transparent_background']!=public['transparent_background']:
        raise ValueError('The actual transparent_background parameter differs from the task.')
    if type(request['requested_outputs']) is not int or request['requested_outputs']!=1:
        raise ValueError('The actual request must ask for one output only.')
    result={'tool':tool.strip(),'prompt':prompt,'references':resolved,'size':list(request['size']),
            'transparent_background':request['transparent_background'],'requested_outputs':1}
    if 'tool_arguments' in request:
        arguments=request['tool_arguments']
        if not isinstance(arguments,dict):raise ValueError('tool_arguments must be a JSON object when available.')
        _safe_arguments(arguments)
        if 'prompt' in arguments and arguments['prompt']!=prompt:
            raise ValueError('The recorded tool prompt contradicts the normalized prompt.')
        for key in ('n','num_images','num_outputs','requested_outputs'):
            if key in arguments and (type(arguments[key]) is not int or arguments[key]!=1):
                raise ValueError('The recorded tool output count contradicts the single-output contract.')
        if 'transparent_background' in arguments and (type(arguments['transparent_background']) is not bool
                or arguments['transparent_background']!=result['transparent_background']):
            raise ValueError('Recorded tool transparency contradicts the normalized request.')
        for key in ('references','referenced_image_paths'):
            if key in arguments:
                values=arguments[key]
                if (not isinstance(values,list) or any(not isinstance(v,str) for v in values)
                        or [str(Path(v).resolve()) for v in values]!=resolved):
                    raise ValueError('Recorded tool reference paths contradict the normalized request.')
        unverified=[key for key in arguments if key not in {'prompt','n','num_images','num_outputs',
                    'requested_outputs','transparent_background','references','referenced_image_paths',
                    'size','width','height'}]
        size=arguments.get('size')
        parsed=(list(size) if isinstance(size,(list,tuple)) else
                [int(v) for v in re.fullmatch(r'(\d+)x(\d+)',size).groups()]
                if isinstance(size,str) and re.fullmatch(r'(\d+)x(\d+)',size) else None)
        if parsed is not None and parsed!=result['size']:
            raise ValueError('Recorded tool size contradicts the normalized request.')
        if 'size' in arguments and parsed is None:unverified.append('size')
        for key,index in [('width',0),('height',1)]:
            if key in arguments and arguments[key]!=result['size'][index]:
                raise ValueError('Recorded tool dimensions contradict the normalized request.')
        result['tool_arguments']=copy.deepcopy(arguments)
        result['unverified_tool_argument_fields']=unverified
    return result


def begin(work,task,public,request):
    normalized=normalize_request(work,task,public,request)
    digest=fingerprint(normalized);current=task['execution']
    if current['state']!='not_started':
        if current.get('request_fingerprint')!=digest:
            raise ValueError('The first begun request is immutable; no new call is authorized.')
        return None
    relative=f"requests/{task['id']}-{task['attempt']}.json"
    path=relative_file(work,relative)
    record={'format':'newpath.invocation-request/1','task_id':task['id'],'role':task['role'],
            'attempt':task['attempt'],'request':normalized,'request_fingerprint':digest,
            'reference_sha256':{p:sha256(p) for p in normalized['references']},
            'provenance':PROVENANCE,'tool_arguments_recorded':'tool_arguments' in normalized}
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8'))!=record:
            raise ValueError('A saved invocation request cannot be replaced.')
    else:write_json(path,record)
    task['execution']={'state':'invoking','attempt':task['attempt'],'request_file':relative,
                       'request_sha256':sha256(path),'request_fingerprint':digest,
                       'started_at_utc':datetime.now(timezone.utc).isoformat(),'provenance':PROVENANCE}
    return {'kind':'invoke_once','task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
            'request':copy.deepcopy(normalized),'request_file':str(path),'request_sha256':sha256(path),
            'result_template':copy.deepcopy(public['result_template']),
            'failure_template':copy.deepcopy(public['failure_template']),
            'invocation_provenance':PROVENANCE,
            'instructions':'Invoke the recorded image request exactly once now, then save and register its first result. This transition reply alone permits that call. Never replay it, shorten the prompt, omit references, or invoke again while waiting. This record is a caller declaration, not provider telemetry.'}


def require_started(task):
    if task['execution']['state']=='not_started':
        raise ValueError('Submit begin_generation before an image invocation is started or its result is registered.')


def check_reported_prompt(work,task,data):
    record=json.loads(relative_file(work,task['execution']['request_file']).read_text(encoding='utf-8'))
    expected=record['request']['prompt']
    reports=[entry.get('executed_prompt') for entry in task.get('attempts',[])
             if entry.get('attempt')==task['attempt']]
    reports.append(data.get('executed_prompt'))
    if any(prompt is not None and prompt!=expected for prompt in reports):
        raise ValueError('Reported executed_prompt differs from the recorded full prompt; preserve this existing output and pause.')


def audit_returned_prompt(work,task,data,output_sha256):
    """Record only an already validated existing return, never provider proof."""
    entries=task.setdefault('prompt_audits',[])
    existing=next((entry for entry in entries if entry['attempt']==task['attempt']),None)
    reported=data.get('executed_prompt')
    if reported is not None and (not isinstance(reported,str) or not reported or len(reported)>1000000):
        raise ValueError('A reported executed_prompt must be a nonempty full string, or unknown/null.')
    digest=lambda value:hashlib.sha256(value.encode('utf-8')).hexdigest() if value is not None else None
    if existing:
        if existing['output_sha256']!=output_sha256:
            raise ValueError('A prompt audit cannot be attached to a replacement output.')
        # The first validated declaration is immutable. Later evidence remains
        # separately visible; omission never retracts an earlier declaration.
        if reported is not None and reported!=existing['reported_prompt']:
            following=task.setdefault('prompt_followup_reports',[])
            if not any(r['attempt']==task['attempt'] and r['reported_prompt']==reported for r in following):
                following.append({'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
                    'output_sha256':output_sha256,
                    'kind':'supplement' if existing['reported_prompt'] is None else 'conflict',
                    'status':'match' if reported==existing['expected_prompt'] else 'mismatch',
                    'reported_prompt':reported,'reported_prompt_sha256':digest(reported),
                    'expected_prompt_sha256':existing['expected_prompt_sha256'],
                    'first_reported_prompt_sha256':existing['reported_prompt_sha256'],
                    'provenance':PROMPT_AUDIT_PROVENANCE})
        return existing
    record=json.loads(relative_file(work,task['execution']['request_file']).read_text(encoding='utf-8'))
    expected=record['request']['prompt']
    entry={'task_id':task['id'],'role':task['role'],'attempt':task['attempt'],
           'output_sha256':output_sha256,'status':'unknown' if reported is None else 'match' if reported==expected else 'mismatch',
           'expected_prompt':expected,'expected_prompt_sha256':digest(expected),
           'reported_prompt':reported,'reported_prompt_sha256':digest(reported),
           'provenance':PROMPT_AUDIT_PROVENANCE,
           'scope':'Equality of caller-reported executed prompt and locked request; not provider compliance or semantic image quality.'}
    entries.append(entry)
    return entry


def prompt_audit_summary(state):
    entries=[copy.deepcopy(entry) for task in state['tasks'].values() for entry in task.get('prompt_audits',[])]
    following=[copy.deepcopy(entry) for task in state['tasks'].values() for entry in task.get('prompt_followup_reports',[])]
    return {'policy':POST_RETURN_PROMPT_POLICY,'provenance':PROMPT_AUDIT_PROVENANCE,
            'mismatch_count':sum(entry['status']=='mismatch' for entry in entries),
            'match_count':sum(entry['status']=='match' for entry in entries),
            'unknown_count':sum(entry['status']=='unknown' for entry in entries),'entries':entries,
            'additional_reports':following,
            'conflict_count':sum(entry['kind']=='conflict' for entry in following),
            'supplement_count':sum(entry['kind']=='supplement' for entry in following),
            'effective_mismatch_count':len({(entry['task_id'],entry['attempt']) for entry in entries+following
                                            if entry['status']=='mismatch'}),
            'count_scope':'match/mismatch/unknown count first validated declarations; additional reports never replace them; effective_mismatch counts attempts with any reported mismatch.'}


def reset_for_retry(task):
    task.setdefault('execution_history',[]).append(copy.deepcopy(task['execution']))
    enroll(task)
