"""Keep injection, observable activation and independently graded utility separate."""


def activation(*, injected=False, signal=None, provenance=None, evidence_ref=None):
    explicit=signal in ('read','invocation') and provenance=='native_field' and bool(evidence_ref)
    return dict(available=True if injected or explicit else None,activated=True if explicit else None,
                activation_evidence=evidence_ref if explicit else None,
                missing_reason=None if explicit else 'runtime_activation_unobserved')


def utility(records):
    groups={}; tasks={}; roots={}
    for record in records:
        condition=record['condition']
        if condition not in ('natural','preload','negative_routing','transfer'):
            raise ValueError('unknown routing condition')
        group=groups.setdefault(condition,dict(tasks=0,roots=0,records=0,unknown_identity_records=0,verified_outcome_records=0,successes=0,
                                             observable_activations=0,critical_regressions=0))
        group['records']+=1
        task=record.get('task_id'); root=record.get('root_id')
        if isinstance(task,str) and task:
            tasks.setdefault(condition,set()).add(task); group['tasks']=len(tasks[condition])
        else:
            group['unknown_identity_records']+=1
        if isinstance(root,str) and root:
            roots.setdefault(condition,set()).add(root); group['roots']=len(roots[condition])
        group['observable_activations']+=record.get('activated') is True
        if type(record.get('outcome')) is bool and record.get('outcome_provenance')=='executable_grader':
            group['verified_outcome_records']+=1; group['successes']+=record['outcome']
        group['critical_regressions']+=record.get('critical_regression') is True
    return dict(conditions=groups,causal_effect='requires_frozen_paired_experiment',
                disposition='rejected' if any(g['critical_regressions'] for g in groups.values()) else 'uncertified',
                automatic_compilation=False)


def compile_procedure(value, *, enabled=False):
    if not enabled: return dict(status='disabled')
    required=('procedure','prerequisites','activate_when','do_not_activate_when','counterexamples','evidence_refs')
    if (value.get('approved') is not True or value.get('outcome_provenance')!='executable_grader'
            or value.get('verified_success') is not True or value.get('scope')!='project'
            or any(not value.get(k) for k in required)):
        raise ValueError('skill compilation requires approved verified procedures, exclusions and counterexamples')
    # Returns a hypothesis, never installs a skill or grants authority.
    import json
    if len(json.dumps(value))>16384: raise ValueError('procedure exceeds compilation bound')
    return dict(status='hypothesis',procedure={k:value[k] for k in required},
                needs_independent_evaluation=True,global_install=False,automatic_promotion=False)


TRANSFER_SCHEMA = 'skill-paired-transfer-v1'
ARMS = ('absent', 'present')
DECLARABLE = ('natural', 'transfer', 'negative_routing')


def _transfer_policy(policy):
    if not isinstance(policy, dict) or set(policy) != {'target_conditions', 'control_conditions', 'max_control_loss'}:
        raise ValueError('transfer policy declares exactly target_conditions, control_conditions and max_control_loss')
    roles = {}
    for key in ('target_conditions', 'control_conditions'):
        value = policy[key]
        if not isinstance(value, (list, tuple)) or not value or any(c not in DECLARABLE for c in value):
            raise ValueError(f'{key} must list natural, transfer or negative_routing; preload is diagnostic only')
        roles[key] = sorted(set(value))
    if set(roles['target_conditions']) & set(roles['control_conditions']):
        raise ValueError('a condition cannot be both a target and a control')
    loss = policy['max_control_loss']
    if type(loss) not in (int, float) or not 0 <= loss <= 1:
        raise ValueError('max_control_loss must be a number from 0 to 1')
    return dict(roles, max_control_loss=loss)


def _observed(record):
    return record.get('activated') is True and bool(record.get('activation_evidence'))


def paired_transfer(records, *, policy):
    """Compare skill-present with skill-absent arms over matched task roots under a predeclared policy.

    The main contrast covers every eligible root, whatever activation was observed; an
    unobserved activation stays unknown and never filters, confirms or refutes a pair.
    """
    import hashlib
    import json
    from fractions import Fraction
    declared = _transfer_policy(policy)
    role = {c: 'target' for c in declared['target_conditions']}
    role.update({c: 'control' for c in declared['control_conditions']})
    excluded = dict(unknown_identity_records=0, unverified_records=0)
    roots, groups = {}, {}
    activated = dict(records=0, successes=0)
    for record in records:
        condition, arm = record.get('condition'), record.get('arm')
        if condition not in ('natural', 'preload', 'negative_routing', 'transfer'):
            raise ValueError('unknown routing condition')
        if arm not in ARMS:
            raise ValueError('arm must be absent or present')
        group = groups.setdefault(condition, dict(
            role=role.get(condition, 'diagnostic' if condition == 'preload' else 'undeclared'),
            matched_roots=0, unmatched_roots=0, improved_roots=0, unchanged_roots=0, harmed_roots=0,
            mean_delta=None, present_observed_activations=0, present_unknown_activations=0,
            absent_observed_activations=0, critical_regressions=0))
        if arm == 'present':
            group['present_observed_activations' if _observed(record) else 'present_unknown_activations'] += 1
            group['critical_regressions'] += record.get('critical_regression') is True
        else:
            group['absent_observed_activations'] += _observed(record)
        task, root = record.get('task_id'), record.get('root_id')
        if not (isinstance(task, str) and task and isinstance(root, str) and root):
            excluded['unknown_identity_records'] += 1; continue
        outcome = record.get('outcome')
        if type(outcome) is not bool or record.get('outcome_provenance') != 'executable_grader':
            excluded['unverified_records'] += 1; continue
        roots.setdefault((condition, task, root), {}).setdefault(arm, []).append(outcome)
        if arm == 'present' and _observed(record):
            activated['records'] += 1; activated['successes'] += outcome
    deltas = {}
    for (condition, _task, _root), arms in roots.items():
        group = groups[condition]
        if len(arms) < 2:
            group['unmatched_roots'] += 1; continue
        delta = Fraction(sum(arms['present']), len(arms['present'])) - Fraction(sum(arms['absent']), len(arms['absent']))
        deltas.setdefault(condition, []).append(delta)
        group['matched_roots'] += 1
        group['improved_roots' if delta > 0 else 'harmed_roots' if delta < 0 else 'unchanged_roots'] += 1
    for condition, values in deltas.items():
        groups[condition]['mean_delta'] = float(sum(values) / len(values))
    tolerance = Fraction(str(declared['max_control_loss']))
    if any(g['critical_regressions'] for g in groups.values()):
        disposition, reason = 'rejected', 'critical_regression'
    elif any(c in deltas and sum(deltas[c]) / len(deltas[c]) < -tolerance for c in declared['control_conditions']):
        disposition, reason = 'rejected', 'negative_transfer'
    elif any(g['absent_observed_activations'] for g in groups.values()):
        disposition, reason = 'inconclusive', 'absent_arm_activation'
    elif not any(c in deltas for c in declared['target_conditions']):
        disposition, reason = 'inconclusive', 'no_matched_target_roots'
    elif not all(c in deltas for c in declared['control_conditions']):
        disposition, reason = 'inconclusive', 'no_matched_control_roots'
    else:
        disposition, reason = 'uncertified', 'requires_frozen_paired_experiment'
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return dict(schema=TRANSFER_SCHEMA, policy=declared, policy_sha256=digest, conditions=groups, excluded=excluded,
                false_activations=sum(groups[c]['present_observed_activations'] for c in declared['control_conditions'] if c in groups),
                conditional_on_observed_activation=dict(activated, interpretation='descriptive_not_causal'),
                disposition=disposition, reason=reason, causal_effect='requires_frozen_paired_experiment',
                automatic_promotion=False)
