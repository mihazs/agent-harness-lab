"""Fixed-plan analytic precision, not power or qualification of declared roots."""
import math
from agent_harness_lab.private_journal import record_bytes
from .cohorts import MAX_BYTES, _manifest
from .plan import ExperimentSpec
from .statistics import bound, radius

PROFILE = 'fixed-root-precision-v1'
MAX_TASKS = 1000


def preflight(spec, *, cohort_manifest_json=None, trial_limit=None, budget_seconds=None):
    if not isinstance(spec, ExperimentSpec): raise ValueError('a frozen experiment spec is required')
    spec.validate()
    if len(spec.tasks)>MAX_TASKS: raise ValueError('precision planning exceeds the 1000-task bank cap')
    if trial_limit is not None and (type(trial_limit) is not int or trial_limit<=0):
        raise ValueError('positive bounded caller trial ceiling required')
    if budget_seconds is not None:
        try:valid=type(budget_seconds) in (int,float) and budget_seconds>0 and math.isfinite(budget_seconds)
        except OverflowError:valid=False
        if not valid:raise ValueError('finite positive parent time ceiling required')
    families={family:{root for _,root,found in spec.tasks if found==family} for _,_,family in spec.tasks}
    roots={root for _,root,_ in spec.tasks}
    cohorts=[]
    if spec.cohort_manifest_sha256 is not None:
        parsed,pin=record_bytes(cohort_manifest_json,MAX_BYTES)
        if pin!=spec.cohort_manifest_sha256: raise ValueError('precision cohort bytes differ from frozen plan')
        manifest=_manifest(parsed,spec.tasks)
        if any(c['qualification']['plan_sha256']==spec.digest for c in manifest['cohorts']):
            raise ValueError('same-plan outcomes cannot qualify cohort membership')
        cohorts=manifest['cohorts']
    elif cohort_manifest_json is not None:
        raise ValueError('cohort bytes require a frozen cohort pin')
    required=sum(c['required'] for c in cohorts)
    count=1+len(families)+required
    alpha=spec.alpha/count
    endpoints={}
    def endpoint(name,weights,margin,strict,minimum_roots,required=True):
        item=dict(declared_roots=len(weights),required=required,margin=margin,strict=strict,
            min_roots=minimum_roots,per_endpoint_alpha=alpha if required else None,
            half_width=None,minimum_observed_effect=None,best_case_lower=None,criteria_possible=None)
        if required:
            try:
                width=radius(weights,alpha)
                if not math.isfinite(width): raise ValueError('unrepresentable precision')
                best=bound([1]*len(weights),weights,alpha)['low']
                item.update(half_width=width,minimum_observed_effect=margin+width,best_case_lower=best,
                    criteria_possible=len(weights)>=minimum_roots and (best>margin if strict else best>=margin))
            except (OverflowError,ZeroDivisionError,ValueError):
                item.update(criteria_possible=False,numeric_status='unrepresentable_by_frozen_method')
        endpoints[name]=item
    weights=dict(spec.family_weights)
    primary=[weight/len(families[family]) for family,weight in spec.family_weights for _ in families[family]]
    endpoint('primary',primary,spec.effect_margin,True,spec.min_roots)
    for family in sorted(families):
        endpoint('family:'+family,[1/len(families[family])]*len(families[family]),-spec.family_margin,False,1)
    for cohort in sorted(cohorts,key=lambda c:c['cohort_id']):
        selected={m['root_id'] for m in cohort['members']}
        endpoint('cohort:'+cohort['cohort_id'],[1/len(selected)]*len(selected),-cohort['margin'],False,
            spec.min_roots,cohort['required'])
    needed=[value for value in endpoints.values() if value['required']]
    target=spec.precision_half_width
    target_met=None if target is None else all(e['half_width'] is not None and e['half_width']<=target for e in needed)
    planned=2*len(spec.tasks)*spec.repeats
    budget_met=planned<=min(spec.max_trials,trial_limit if trial_limit is not None else spec.max_trials)
    possible=all(e['criteria_possible'] for e in needed) and budget_met
    return dict(schema_version=1,profile=PROFILE,plan_sha256=spec.digest,
        status='feasible' if possible and target_met is not False else 'infeasible',
        analysis_method='weighted_root_Hoeffding',analysis_mode='fixed_final',
        estimand='frozen_family_weighted_mean_of_selected_root_mean_paired_correctness_differences',
        selected_tasks=len(spec.tasks),declared_roots=len(roots),repetitions=spec.repeats,planned_trials=planned,
        max_trials=spec.max_trials,caller_trial_limit=trial_limit,budget_seconds=budget_seconds,
        trial_ceiling_met=budget_met,time_feasibility='unknown_without_a_preregistered_execution_cost_model',
        family_weights=[dict(family=family,weight=weight,roots=len(families[family]),per_root_weight=weight/len(families[family]))
            for family,weight in spec.family_weights],
        weight_sum=sum(weights.values()),weight_policy='frozen_weights_without_posthoc_renormalization',
        error_allocation=dict(method='Bonferroni',total_alpha=spec.alpha,endpoints=count,
            required_cohorts=required,per_endpoint_alpha=alpha),
        endpoints=endpoints,precision_half_width=target,precision_target_met=target_met,
        precision_target_scope='primary_and_all_required_family_and_cohort_endpoints',
        execution_allowed=budget_met and (target is None or possible and target_met is True),
        qualification_authority='not_validated_by_analytic_precision',root_independence='unverified',
        power='not_estimated',automatic_budget_change=False,automatic_promotion=False,publication_allowed=False,
        limitations=['Selected declared roots only; labels do not establish independence or current bank eligibility.',
            'Independent bounded root means are assumed; related tasks and all repeats remain within their original root.',
            'No sequential peeking, population generalization or adaptive final-set reuse is authorized.',
            'Unselected bank availability and wall-clock feasibility are unknown; no trials are automatically added.',
            'Feasible precision is not observed improvement, noninferiority or a promotion decision.'])
