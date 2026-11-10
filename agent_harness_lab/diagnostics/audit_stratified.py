"""Private hard-budget stratified audit; conclusions depend on declared sampling assumptions."""
import hashlib
import math
from fractions import Fraction

from . import audit_fixed as fixed, audit_sampling as audit

PLAN_PROFILE='audit-stratified-plan-v1'
RECEIPT_PROFILE='audit-stratified-receipt-v1'
REPORT_PROFILE='audit-stratified-estimate-v1'
LIMITATIONS=('frame_origin_unauthenticated','sampling_plan_timing_unauthenticated',
             'reviewer_labels_unauthenticated','hash_rank_exchangeability_unverified',
             'capture_outside_frame_unknown','unit_lineage_unverified')


def _strata(frozen):
    groups={}
    for key,unit in frozen['eligible'].items():
        name='+'.join(['alerted' if unit['alerted'] else 'nonalerted',*unit['flags']])
        groups.setdefault(name,[]).append((key,unit['alerted']))
    return groups


def layout(frame):
    """Expose only stratum sizes for the caller to set positive quotas."""
    frozen=audit.freeze(frame);groups=_strata(frozen)
    return dict(frame_sha256=frozen['frame_sha256'],frame=frozen['summary'],
                strata={name:len(rows) for name,rows in sorted(groups.items())},publication_allowed=False)


def _plan(plan,frozen,groups):
    audit._fields(plan,'schema_version profile frame_sha256 seed quotas review_budget policy_sha256 model_sha256 decision_timestamp',
                  'invalid stratified audit plan')
    quotas=plan['quotas']
    if (type(plan['schema_version']) is not int or plan['schema_version']!=1 or plan['profile']!=PLAN_PROFILE
            or audit._sha(plan['frame_sha256'])!=frozen['frame_sha256'] or type(plan['seed']) is not int or plan['seed']<0
            or type(plan['review_budget']) is not int or plan['review_budget']<1 or not isinstance(quotas,dict)
            or set(quotas)!=set(groups)
            or any(type(quota) is not int or not 1<=quota<=len(groups[name]) for name,quota in quotas.items())
            or sum(quotas.values())>plan['review_budget']):
        raise ValueError('invalid stratified audit frame, quotas or budget')
    audit._sha(plan['policy_sha256'],True);audit._sha(plan['model_sha256'],True);audit._time(plan['decision_timestamp'])


def _rank(seed,frame_sha,unit_sha):
    return hashlib.sha256(f'audit-stratified-v1:{seed}:{frame_sha}:{unit_sha}'.encode()).digest()


def draw(frame,plan):
    """Draw each disjoint stratum without replacement; reject collisions without redraw."""
    frozen=audit.freeze(frame);groups=_strata(frozen);_plan(plan,frozen,groups)
    strata=[];draws=[]
    for name,units in sorted(groups.items()):
        ranks=[];seen=set();quota=plan['quotas'][name]
        for key,alerted in units:
            rank=_rank(plan['seed'],frozen['frame_sha256'],key)
            if rank in seen:raise ValueError('stratified audit hash-rank collision')
            seen.add(rank);ranks.append((rank,key,alerted))
        strata.append(dict(stratum=name,units=len(units),sample_size=quota,
            inclusion_probability=dict(numerator=quota,denominator=len(units))))
        draws.extend(dict(unit_sha256=key,alerted=alerted,stratum=name) for _,key,alerted in sorted(ranks)[:quota])
    return audit._seal(dict(schema_version=1,profile=RECEIPT_PROFILE,estimand=audit.ESTIMAND,
        frame_sha256=frozen['frame_sha256'],plan_sha256=audit._digest(plan),scope=frozen['scope'],window=frozen['window'],
        frame=frozen['summary'],strata=strata,draws=draws,review_budget=plan['review_budget'],
        limitations=list(LIMITATIONS),publication_allowed=False,automatic_policy_update=False))


def review_queue(receipt,frame,plan):
    if receipt!=draw(frame,plan):raise ValueError('stratified audit receipt differs from frame and plan replay')
    return dict(receipt_sha256=receipt['receipt_sha256'],prediction_masked=True,
                units=sorted(item['unit_sha256'] for item in receipt['draws']),publication_allowed=False)


def _bounds(count,size,population,strata):
    point=count/size
    radius=0.0 if size==population else math.sqrt(math.log(4*strata/fixed.ALPHA)/(2*size))
    return max(0.0,point-radius),min(1.0,point+radius)


def estimate(receipt,frame,plan,batch):
    """Weighted sample rates with simultaneous conservative finite-population bounds."""
    review_queue(receipt,frame,plan);draws=receipt['draws'];rows,pending,outside=fixed._reviewed(receipt,batch)
    result=dict(schema_version=1,profile=REPORT_PROFILE,receipt_sha256=receipt['receipt_sha256'],estimand=audit.ESTIMAND,
        population_units=receipt['frame']['eligible_units'],sample_size=len(draws),review_budget=receipt['review_budget'],reviewed=len(rows),
        pending_or_nonresponse=pending,outside_probability_sample=outside,limitations=list(LIMITATIONS),
        publication_allowed=False,automatic_policy_update=False)
    if pending:return dict(result,status='inconclusive',reason='missing_review_labels',estimates=None)
    population=receipt['frame']['eligible_units'];h=len(receipt['strata']);points=[Fraction(),Fraction()]
    lows=[[],[]];highs=[[],[]];census=True
    for stratum in receipt['strata']:
        subset=[row for row in rows if row['stratum']==stratum['stratum']]
        size=stratum['sample_size'];units=stratum['units'];census &= size==units
        counts=(sum(row['incident'] for row in subset),sum(row['incident'] and not row['alerted'] for row in subset))
        for index,count in enumerate(counts):
            points[index]+=Fraction(units*count,population*size)
            low,high=_bounds(count,size,units,h)
            lows[index].append(units*low/population);highs[index].append(units*high/population)
    values=[float(point) for point in points]
    intervals=([[value,value] for value in values] if census else
        [[max(0.0,math.nextafter(math.fsum(lows[i]),-math.inf)),
          min(1.0,math.nextafter(math.fsum(highs[i]),math.inf))] for i in range(2)])
    recall=None if not points[0] else float(1-points[1]/points[0])
    recall_interval=fixed._recall_interval(intervals[0],intervals[1])
    if recall is not None:
        recall_interval=([recall,recall] if census else
                         [min(recall_interval[0],recall),max(recall_interval[1],recall)])
    return dict(result,status='estimated',reason=None,estimates=dict(
        prevalence=dict(estimate=values[0],ci95=intervals[0]),
        missed_incident_rate=dict(estimate=values[1],ci95=intervals[1]),
        triage_recall=dict(estimate=recall,ci95=recall_interval),
        interval='stratified_bonferroni_hoeffding_without_replacement',alpha=fixed.ALPHA))
