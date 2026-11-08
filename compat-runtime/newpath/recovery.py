"""Frozen, once-per-job recovery for a declared first image invocation."""
import copy
import json
from pathlib import Path

from . import execution
from .storage import relative_file, save_state, sha256

MODE = 'bounded-recovery/1'
RECOVERABLE = {'role_mismatch', 'decoding', 'material', 'generation_failure'}


def validate_policy(policy):
    if policy not in (None, MODE):
        raise ValueError('Unsupported recovery policy.')
    return policy


def enabled(state):
    return validate_policy(state.get('recovery_policy')) == MODE


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


def prepare(work, state):
    """Reserve the sole extra attempt under the caller's existing job lock."""
    if not enabled(state) or state['recovery']['used'] or state.get('execution_hold'):
        return False
    tasks = list(state['tasks'].values())
    if any(task.get('generation_violations') or task.get('hard_binding_failure')
           or any(entry.get('outcome') in {'binding', 'binding_rejected'} for entry in task.get('attempts', []))
           for task in tasks):
        return False
    failed = [task for task in tasks if task['status'] == 'failed']
    if not failed:
        return False
    task = failed[0]
    if task.get('failure_kind') not in RECOVERABLE or task['attempt'] != 0:
        return False
    record = task.get('execution', {})
    observation = task.get('attempts', [{}])[-1]
    if not record.get('request_sha256') or observation.get('invocation_count') != 1:
        return False
    if not observation.get('raw') and observation.get('outcome') != 'generation_failure':
        return False
    original = {'brief': task['brief'], 'brief_sha256': task['brief_sha256']}
    reason = observation.get('observed') or task.get('failure', 'Correct the saved output failure.')
    reason = ' '.join(str(reason).split())[:700]
    prompt = relative_file(work, task['brief']).read_text(encoding='utf-8')
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
             'policy': MODE, 'authorization_scope': 'frozen_job_policy_not_a_new_user_request'}
    task.setdefault('brief_history', []).append(original)
    task.update(status='waiting', issued=True, attempt=1, brief=name,
                brief_sha256=sha256(path), bounded_recovery=copy.deepcopy(entry))
    execution.reset_for_retry(task)
    task.pop('failure', None)
    task.pop('failure_kind', None)
    task.pop('registration', None)
    state['recovery'].update(used=1, entry=entry)
    save_state(work, state, 'bounded_recovery_prepared')
    return True
