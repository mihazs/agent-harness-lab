"""Keep injection, observable activation and independently graded utility separate."""


def activation(*, injected=False, signal=None, provenance=None, evidence_ref=None):
    explicit=signal in ('read','invocation') and provenance=='native_field' and bool(evidence_ref)
    return dict(available=bool(injected),activated=True if explicit else None,
                activation_evidence=evidence_ref if explicit else None,
                missing_reason=None if explicit else 'runtime_activation_unobserved')


def utility(records):
    groups={}
    for record in records:
        condition=record['condition']
        if condition not in ('natural','preload','negative_routing','transfer'):
            raise ValueError('unknown routing condition')
        group=groups.setdefault(condition,dict(tasks=0,verified_outcomes=0,successes=0,
                                             observable_activations=0,critical_regressions=0))
        group['tasks']+=1
        group['observable_activations']+=record.get('activated') is True
        if type(record.get('outcome')) is bool and record.get('outcome_provenance')=='executable_grader':
            group['verified_outcomes']+=1; group['successes']+=record['outcome']
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
