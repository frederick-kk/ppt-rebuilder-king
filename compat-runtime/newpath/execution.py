"""Explicit, caller-declared image invocation starts; no provider telemetry."""
import copy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import re

from .storage import fingerprint,read_state,relative_file,sha256,write_json
from .prompts import BACKGROUND_CONTAINER_POLICY_ID, BOARD_REFERENCE_POLICY, SOURCE_BOARD_REFERENCE_POLICY
from .prompts import _background_layer_allocation
from .source import TITLE_REFERENCE_POLICY

MODE='explicit-start/1'
POST_RETURN_PROMPT_POLICY='audit-existing-return/1'
PROVENANCE='caller_declared_before_invocation_not_provider_verified'
PROMPT_AUDIT_PROVENANCE='caller_reported_executed_prompt_not_provider_verified'
STATES={'not_started','invoking','returned','recorded','failed'}

def _invocation_instructions(task,request):
    role=task['role']
    mode='真实透明PNG（空区alpha 0）' if request['transparent_background'] else '不透明PNG'
    containers_in_background=task.get('decorative_background_policy')==BACKGROUND_CONTAINER_POLICY_ID
    scope=task.get('source_layer_scope')
    if role in ('background','foreground') and scope is not None:
        allocation=task['scene_allocation']
        scenes=json.dumps(allocation['background_regions'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        regions=allocation['foreground_regions']
        if role=='background':
            containers=('保留未分配为前景art的所有层级空纸底、金框、卡片轮廓及分隔。' if containers_in_background else
                        '移除分配给前景art的纸框和卡片。')
            background_payload={'scene_allocation':_background_layer_allocation(allocation),'source_layer_scope':scope}
            goal=('只生成空背景。'+containers+'按冻结background_regions原位、比例和轮廓保留场景、固有图内文字及固定品牌标识。'
                  '已分配的空底托、容器、分隔和原生装饰图形按背景清单保留，平面或简单轮廓也保留。'
                  '清除完整art组，包括完整红牌、刷底、徽章背板及主标题自带圆弧和笔刷，不能留下去字后的空红条或空徽章。'
                  '清除普通页面文字、独立图片完整矩形及图内文字和人物；未分配给背景的原生图形、连接线、箭头及表格内容清除，恢复下方已分配背景或空容器。'
                  '所有box为[x,y,width,height]，单位是1920×1080工作图像素。'
                  '冻结背景与完整art清除清单'+json.dumps(background_payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'。')
        else:
            by_id={region['id']:region for region in regions}
            required=[by_id[identity] for identity in scope['board_member_ids']]
            goal=('仅生成组件板参考成员，保持原工作位置、比例、完整主体和自带衬底。'
                  '不生成独立主标题、普通正文、原生几何、连接线、箭头及表格；独立图片保持完整矩形和图内文字、人物及全部内容。'
                  '仅保留本成员分配的纸框或卡片，移除背景固定容器；清空同一背景融景'+scenes+'。'
                  '参考字形差异记入observed，后续按冻结layout及真实原图生成最终字样和材质，不能仅因参考字形差异再生成前景。'
                  '主体缺失、身份错误、严重错位导致指定ROI不可用，或背景人物侵入ROI污染主体，仍须如实判false。'
                  '本次参考成员'+json.dumps(required,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'。')
    elif role=='background':
        if containers_in_background:
            goal=('只生成空背景。保留分配给底图的融合场景，以及所有层级的空纸底、金框、卡片轮廓及分隔，'
                  '按原图原位、比例和轮廓保留。清除页面文字、完整艺术字及所属圆弧、刷痕和衬底、刷标、图标和独立内容图片，'
                  '清除处恢复为空白背景或纸底。')
        else:
            goal=('只生成分配的场景背景，移除分配给独立层的纸框、卡片、页面文字、完整艺术字及自带衬底、'
                  '刷标、图标和独立内容图片。')
        goal+='简单原生几何、连接线、箭头及表格内容均清除，已分配背景的装饰轮廓和分隔保留。'
    elif role=='foreground':
        allocation=('移除分配给背景的场景和固定容器。' if containers_in_background else
                    '保留本任务分配的纸框、卡片及页面内容，移除背景场景。')
        goal=('只生成分配的独立页面内容，保留完整图片、文字、艺术字及自带衬底、标签和图标。'+allocation+
              '保留简单原生几何、连接线、箭头及表格内容的参考外观，供后续原生制作。')
    elif role=='wordart':
        member=task.get('member') or {}
        lettering=member.get('lettering')
        title='指定完整标题“'+lettering+'”' if lettering else '本任务指定完整标题'
        goal='只生成'+title+'，保留自带圆弧、笔刷和衬底，按原图整组比例生成。'
        goal+='主体实色保持实色，不淡化文字。'
        if request['transparent_background']:
            goal+='实色笔画alpha 255，主体之外alpha 0，四边保留透明空隙，完整笔画不得触边。'
        else:
            goal+='四边保留锁定请求规定的底色空隙，完整笔画不得触边。'
        if task.get('source_detail') is not None:
            if task['source_detail'].get('reference_policy') == TITLE_REFERENCE_POLICY:
                goal+='调用前实际查看本任务唯一参考。它按source_parts保留真实源像素，保留区外已透明，区内可能仍有纸底或装饰。'
                goal+='只生成指定标题完整组，按真实字样、完整组比例及源行数保留自身圆弧笔刷，源为单行时保持单行横排。'
                goal+='生成主体需清除剩余纸底、字幕、相邻栏目和整页场景，保留原有白描边，不添加宽白边或光晕。'
            else:
                goal+='调用前实际查看本任务references。第一图是真实原图局部，提供完整组的字样、字形及字与圆弧笔刷比例。'
                goal+='第二图是完整工作原图，提供身份和原位置。按源保持行数，源为单行时保持单行横排。'
                goal+='只使用本任务references作为生成参考，局部内的其他文段和物件保持在指定组之外。'
                goal+='保留原有白描边，不添加宽白边或光晕。'
    elif role=='board':
        board=task.get('board') or {}
        grid=board.get('grid') or {}
        geometry=(str(grid['rows'])+'行'+str(grid['columns'])+'列') if 'rows' in grid and 'columns' in grid else '当前'
        members=[]
        for member in board.get('members',[]):
            label=str(member.get('id',''))
            if member.get('lettering'):label+='（写“'+member['lettering']+'”）'
            members.append(label)
        goal='只生成'+geometry+'格网指定的完整成员，保持槽位、完整边缘和安全空隙，清除无关页面内容。'
        if members:goal+='成员为'+'、'.join(members)+'。'
        if board.get('source_board_reference_policy') == SOURCE_BOARD_REFERENCE_POLICY:
            goal+='第一图由真实原图裁片等比排成参考格网，第二图是完整真实原图，两者不能作为生成结果或交付素材。'
            goal+='按真实源字样和外观生成新的组件板；轮廓素材去掉源场景底，完整矩形图片的图内文字和全部内容保留。'
        if board.get('board_reference_policy') == BOARD_REFERENCE_POLICY:
            ownership = [{'id': member['id'], 'context_exclusions': member.get('context_exclusions', [])}
                         for member in board.get('members', []) if member.get('embedded_image') is True]
            goal+='完整矩形图片按自身source_box生成，其他独立ID的标题或标签不并入该图片，即使原图边缘有叠压也不补成图内标签。'
            goal+='真实图内文字、logo和人物仍完整保留，不添加caption面板。各图片独立归属'+json.dumps(
                ownership,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'。'
    else:
        goal='只生成当前锁定请求指定的内容。'
    allocation=task.get('scene_allocation')
    if role in ('background','foreground') and allocation is not None and scope is None:
        scenes=json.dumps(allocation['background_regions'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        content=json.dumps(allocation['foreground_regions'],ensure_ascii=False,sort_keys=True,separators=(',',':'))
        scene_action='融景保留' if role=='background' else '融景清空'
        content_action='独立内容移除' if role=='background' else '独立内容保留'
        image_action='一并移除并恢复下方背景' if role=='background' else '一并保留'
        goal+='按冻结分层清单执行，区域坐标为工作图[x,y,width,height]。'+scene_action+scenes+'。'+content_action+content+'。'
        goal+='同一融景中的人物、物件和图内文字随融景归属。独立图片完整矩形及图内全部内容'+image_action+'。'
    if role in ('foreground','board') and not request['transparent_background']:
        goal+='空区使用锁定请求规定的纯色底。'
    if task.get('keyed_material_policy') == 'keyed-materials/1':
        colours={'magenta':'RGB(255,0,255)','green':'RGB(0,255,0)',
                 'cyan':'RGB(0,255,255)','yellow':'RGB(255,255,0)'}
        goal+='空区统一为'+colours[task['matte_key']]+'，不绘制白灰棋盘或整页底图，程序会把该底色转成实际透明。'
    if role == 'background' and task.get('background_scene_emphasis'):
        goal+='原图与边缘融为一体的场景人物、书桌、书堆、室内环境及景物按原位保留，清除独立内容和页面文字。'
    if role == 'background' and task.get('background_repair_reference'):
        goal=('在第一张待修背景上只修复锁定提示列出的具体缺陷，其余已清空区域保留。'
              '第二张原图事实仅用于找回遗漏场景及原位置，页面标题、正文和独立内容图片保持清空。'+goal)
    references=[]
    for index,path in enumerate(request['references'],1):
        if role=='background' and task.get('background_repair_reference'):
            use='待修背景编辑目标' if index==1 else '原图事实定位'
        elif role=='wordart' and task.get('source_detail') is not None:
            if task['source_detail'].get('reference_policy') == TITLE_REFERENCE_POLICY:
                use='唯一source_parts真实源标题参考及完整组比例'
            else:
                use='真实源局部外观和完整组比例' if index==1 else '完整原图身份和位置'
        else:
            use=('当前格网布局' if index==1 else '原图外观') if role=='board' else '工作原图外观'
        references.append('参考'+str(index)+'（'+Path(path).name+'）提供'+use)
    reference_text='，'.join(references)+'。' if references else ''
    return ('本轮直接调用当前'+role+'任务一次，输出1张'+mode+'。'+goal+reference_text+
            '获得回图后保存、实际查看并登记该次结果，按控制器下一动作继续。'
            '显式prompt及参考字段可用时传锁定原文与原参考顺序，通过宿主真实支持的机制提供参考，字段按request实际值执行。'
            '未暴露信息保留unknown，executed_prompt仅记服务实际暴露的原文。'
            '上下文无法隔离时先登记再下一任务，独立绑定且宿主支持时可并行。'
            '原许可范围保持一次调用，重复begin或waiting只等待、保存或登记已有回图。')


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
        if 'prompt' in arguments and arguments['prompt'] is not None and arguments['prompt']!=prompt:
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
        if 'prompt' in arguments and arguments['prompt'] is None:
            unverified.append('prompt')
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
            'instructions':_invocation_instructions(task,normalized)}


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
