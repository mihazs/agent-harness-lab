"""Private, non-authorizing comparison of separately observed byte boundaries."""
from agent_harness_lab.audit import chain
from agent_harness_lab.sources import harness_inventory

PROFILE='ahl-codex-budget-boundary-v1'


def report(inventory,task_cwd,limit,totals,boundaries):
    result=dict(profile=PROFILE,status='unknown',additional_findings=[],
                snapshot_relation='separate_resolver_observation',observed_loading='unknown',
                runtime_settings='caller_declared_or_assumed',evidence_authentication='none',publication_allowed=False)
    if (type(inventory) is not harness_inventory.HarnessInventory or inventory.status!='declared'
            or inventory.runtime!='codex' or not isinstance(totals,dict) or not isinstance(boundaries,dict)):
        return result
    reasons=totals.get('reasons')
    if (not isinstance(reasons,list) or any(reason!='truncated_chain_not_counted' for reason in reasons)
            or totals.get('status') != ('unknown' if reasons else 'declared')): return result
    missing=[]
    def lookup(path,size):
        value=boundaries.get((path,size))
        if type(value) is not bool: missing.append(True);return None
        return value
    original=chain.codex(inventory,task_cwd,declared_limit=limit)
    resolved=chain.codex(inventory,task_cwd,declared_limit=limit,blank_prefix=lookup)
    if missing: return result
    result.update(status='modeled',additional_findings=[dict(path=path,reason=reason,measures=dict(measures))
        for path,(reason,measures) in sorted(resolved.items()) if original.get(path)!=(reason,measures)])
    return result
