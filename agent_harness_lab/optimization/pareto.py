"""Safety is feasibility; missing costs are unknown, never zero."""
import math


def frontier(candidates):
    eligible=[]
    required=set().union(*(set(c.get('metrics',{})) for c in candidates)) if candidates else set()
    for candidate in candidates:
        if candidate.get('safe') is not True:
            continue
        metrics=candidate.get('metrics',{})
        if not metrics or set(metrics)!=required or any(type(v) not in (int,float) or not math.isfinite(v) for v in metrics.values()):
            continue
        eligible.append(candidate)
    result=[]
    for candidate in eligible:
        values=candidate['metrics']
        dominated=False
        for other in eligible:
            if other is candidate or other['metrics'].keys()!=values.keys():
                continue
            # All metrics are benefits (cost must be explicitly negated by caller).
            if all(other['metrics'][k]>=v for k,v in values.items()) and any(other['metrics'][k]>v for k,v in values.items()):
                dominated=True; break
        if not dominated:
            result.append(candidate)
    return sorted(result,key=lambda c:c['candidate_id'])
