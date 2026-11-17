"""Frozen image-task recovery budgets and preserved-return evidence."""
import copy
import json
from pathlib import Path

from . import execution
from .storage import relative_file, save_state, sha256

MODE = 'bounded-recovery/1'
MODE_V2 = 'bounded-existing-return-recovery/2'
RECOVERABLE = {'role_mismatch', 'decoding', 'material', 'generation_failure'}


def validate_policy(policy):
    if policy not in (None, MODE, MODE_V2):
        raise ValueError('Unsupported recovery policy.')
    return policy


def enabled(state):
    return validate_policy(state.get('recovery_policy')) in (MODE, MODE_V2)


def initial_budget(policy):
    if validate_policy(policy) == MODE_V2:
        return {'used': 0, 'limit': 2, 'per_task_limit': 1, 'entries': []}
    return {'used': 0, 'limit': 1}


def prepared_attempt(state, task):
    entry = task.get('bounded_recovery')
    if not isinstance(entry, dict):
        return False
    saved = state.get('recovery', {})
    policy = state.get('recovery_policy')
    if policy == MODE_V2:
        bound = entry in saved.get('entries', [])
    else:
        bound = saved.get('used') == 1 and entry == saved.get('entry')
    return (enabled(state) and bound and entry.get('policy') == policy
            and entry.get('task_id') == task['id'] and entry.get('from_attempt') == 0
            and type(entry.get('attempt')) is int and entry['attempt'] == task['attempt'] == 1)


def frozen_policy(work, state, requested=None):
    policy = validate_policy(state.get('recovery_policy'))
    validate_policy(requested)
    if requested is not None and requested != policy:
        raise ValueError('A saved job cannot change recovery policy or be migrated.')
    marker = Path(work) / 'initialization.json'
    saved = json.loads(marker.read_text(encoding='utf-8')) if marker.is_file() else {}
    if policy != validate_policy(saved.get('recovery_policy')):
        raise ValueError('The frozen recovery policy differs from its initialization marker.')
    if policy is None:
        if 'recovery' in state:
            raise ValueError('A saved recovery record cannot lose its recovery policy.')
        return
    require_dependencies(state.get('execution_policy'), state.get('delivery_policy'))
    budget = state.get('recovery')
    if policy == MODE_V2:
        entries = [task['bounded_recovery'] for task in state['tasks'].values() if task.get('bounded_recovery')]
        if (not isinstance(budget, dict) or budget.get('limit') != 2 or budget.get('per_task_limit') != 1
                or type(budget.get('used')) is not int or not 0 <= budget['used'] <= 2
                or not isinstance(budget.get('entries'), list) or len(entries) != budget['used']
                or len(budget['entries']) != budget['used']
                or any(not isinstance(entry, dict) for entry in budget['entries'])
                or sorted(entries, key=lambda entry: entry['task_id']) !=
                   sorted(budget['entries'], key=lambda entry: entry.get('task_id', ''))
                or len({entry['task_id'] for entry in entries}) != len(entries)):
            raise ValueError('Missing or inconsistent frozen per-task recovery budget.')
        for task in state['tasks'].values():
            if task.get('bounded_recovery'):
                entry = task['bounded_recovery']
                if (entry.get('policy') != MODE_V2 or entry.get('task_id') != task['id']
                        or entry.get('from_attempt') != 0 or entry.get('attempt') != 1
                        or entry.get('background_repair_reference') != task.get('background_repair_reference')):
                    raise ValueError('The saved recovery entry differs from its task.')
        return
    if not isinstance(budget, dict) or budget.get('limit') != 1 or type(budget.get('used')) is not int or budget['used'] not in (0, 1):
        raise ValueError('Missing or inconsistent frozen recovery budget.')
    entries = [task['bounded_recovery'] for task in state['tasks'].values() if task.get('bounded_recovery')]
    if len(entries) != budget['used'] or (entries and entries[0] != budget.get('entry')):
        raise ValueError('The saved recovery budget differs from its task attempt.')


def require_dependencies(execution_policy, delivery_policy):
    if execution_policy != execution.MODE or delivery_policy != 'reviewed-existing-output/1':
        raise ValueError('The recovery policy requires explicit execution and reviewed output binding policies.')


def preserve(work, task, path, digest):
    name = f"returned/{task['id']}-{task['attempt']}-{digest[:16]}-preserved.bin"
    destination = relative_file(work, name)
    destination.parent.mkdir(exist_ok=True)
    if not destination.exists():
        destination.write_bytes(Path(path).read_bytes())
    if sha256(destination) != digest or sha256(path) != digest:
        raise ValueError('A returned output changed while being preserved.')
    return name


def pending_return(task):
    """Recover current-attempt pending evidence even after incomplete followups."""
    if task['status'] != 'waiting':
        return None
    saved = task.get('registration', {})
    if saved.get('raw'):
        return copy.deepcopy(saved)
    for entry in reversed(task.get('attempts', [])):
        if entry.get('attempt') != task['attempt'] or not entry.get('raw'):
            continue
        if entry.get('outcome') != 'registration_required':
            return None
        return {key: entry.get(key) for key in
                ('raw', 'sha256', 'reason', 'invocation_count', 'output_count')}
    return None


def blocking_reason(state):
    """Explain the existing recovery eligibility rules without changing state."""
    if not enabled(state):
        return {'code': 'recovery_policy_disabled'}
    version_two = state.get('recovery_policy') == MODE_V2
    if state['recovery']['used'] >= (2 if version_two else 1):
        return {'code': 'recovery_budget_used'}
    if state.get('execution_hold'):
        return {'code': 'user_stopped_generation'}
    tasks = list(state['tasks'].values())
    for task in tasks:
        violations = task.get('generation_violations', [])
        blocked = any(not entry.get('acknowledged') for entry in violations) if version_two else bool(violations)
        if blocked:
            return {'code': 'generation_violation', 'task_id': task['id']}
        if (task.get('hard_binding_failure') or any(
                entry.get('outcome') in {'binding', 'binding_rejected'} for entry in task.get('attempts', []))):
            return {'code': 'binding_conflict', 'task_id': task['id']}
    failed = [task for task in tasks if task['status'] == 'failed']
    if not failed:
        return {'code': 'no_failed_task'}
    task = (next((task for task in failed if task['attempt'] == 0 and not task.get('bounded_recovery')), failed[0])
            if version_two else failed[0])
    if task.get('failure_kind') not in RECOVERABLE:
        return {'code': 'failure_not_recoverable', 'task_id': task['id']}
    if task['attempt'] != 0:
        return {'code': 'attempt_not_initial', 'task_id': task['id']}
    record = task.get('execution', {})
    observation = task.get('attempts', [{}])[-1]
    if not record.get('request_sha256'):
        return {'code': 'missing_request_binding', 'task_id': task['id']}
    count = observation.get('invocation_count')
    count_unknown = (type(count) is not int or count < 1) if version_two else count != 1
    if count_unknown:
        return {'code': 'invocation_count_unknown', 'task_id': task['id']}
    if not observation.get('raw') and observation.get('outcome') != 'generation_failure':
        return {'code': 'missing_failure_output', 'task_id': task['id']}
    return None


def prepare(work, state):
    """Reserve one eligible repair under the caller's existing job lock."""
    if blocking_reason(state) is not None:
        return False
    candidates = [task for task in state['tasks'].values() if task['status'] == 'failed']
    version_two = state.get('recovery_policy') == MODE_V2
    task = (next(task for task in candidates if task['attempt'] == 0 and not task.get('bounded_recovery'))
            if version_two else candidates[0])
    record = task['execution']
    observation = task['attempts'][-1]
    original = {'brief': task['brief'], 'brief_sha256': task['brief_sha256']}
    reason = observation.get('observed') or task.get('failure', 'Correct the saved output failure.')
    reason = ' '.join(str(reason).split())[:700]
    prompt = relative_file(work, task['brief']).read_text(encoding='utf-8')
    edit_reference = None
    if version_two and task['role'] == 'background' and observation.get('raw') and task['references']:
        from PIL import Image
        raw = relative_file(work, observation['raw'])
        try:
            with Image.open(raw) as image:
                is_png = image.format == 'PNG'
                image.verify()
        except (ValueError, OSError, SyntaxError):
            is_png = False
        if is_png:
            name = f"references/{task['id']}-failed-background-{observation['sha256'][:16]}.png"
            path = relative_file(work, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(raw.read_bytes())
            if sha256(path) != observation['sha256'] or sha256(raw) != observation['sha256']:
                raise ValueError('The saved background edit reference changed.')
            edit_reference = {'path': name, 'sha256': observation['sha256'],
                              'source_reference': next(iter(task['references']))}
            task.setdefault('references_history', []).append(copy.deepcopy(task['references']))
            task['references'] = {name: observation['sha256'], **task['references']}
            task['background_repair_reference'] = copy.deepcopy(edit_reference)
            prompt = ('Image 1 is the saved failed background to edit. The following original-source '
                      'reference supplies factual scene identity and placement. Correct only the observed '
                      'missing or extra content. Keep already cleared regions empty and preserve all '
                      'correct background areas. Do not refill original-source page text, independent '
                      'photos, icons or artwork. Apply the original background role below.\n\n' + prompt)
    prompt += '\n\nSingle bounded repair for the same assigned task and source. Correct this observed failure: ' + reason + '\n'
    name = f"briefs/{task['id']}-repair-1.txt"
    path = relative_file(work, name)
    if path.exists() and path.read_text(encoding='utf-8') != prompt:
        raise ValueError('The saved repair brief cannot be replaced.')
    if not path.exists():
        path.write_text(prompt, encoding='utf-8')
    entry = {'task_id': task['id'], 'from_attempt': 0, 'attempt': 1,
             'failure_kind': task['failure_kind'], 'reason': reason,
             'original_request_sha256': record['request_sha256'],
             'policy': state['recovery_policy'], 'authorization_scope': 'frozen_job_policy_not_a_new_user_request'}
    if edit_reference is not None:
        entry['background_repair_reference'] = copy.deepcopy(edit_reference)
    task.setdefault('brief_history', []).append(original)
    task.update(status='waiting', issued=True, attempt=1, brief=name,
                brief_sha256=sha256(path), bounded_recovery=copy.deepcopy(entry))
    execution.reset_for_retry(task)
    task.pop('failure', None)
    task.pop('failure_kind', None)
    task.pop('registration', None)
    if version_two:
        state['recovery']['used'] += 1
        state['recovery']['entries'].append(copy.deepcopy(entry))
    else:
        state['recovery'].update(used=1, entry=entry)
    save_state(work, state, 'bounded_recovery_prepared')
    return True
