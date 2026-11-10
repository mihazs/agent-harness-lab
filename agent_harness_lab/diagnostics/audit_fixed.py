"""Opt-in hard-budget audit of a caller-supplied finite frame, with no authority claims."""
import hashlib
import math

from . import audit_sampling as audit

PLAN_PROFILE = 'audit-fixed-plan-v1'
RECEIPT_PROFILE = 'audit-fixed-receipt-v1'
REPORT_PROFILE = 'audit-fixed-estimate-v1'
ALPHA = .05
LIMITATIONS = ('frame_origin_unauthenticated', 'sampling_plan_timing_unauthenticated',
               'reviewer_labels_unauthenticated', 'hash_rank_exchangeability_unverified',
               'capture_outside_frame_unknown', 'unit_independence_declared_not_verified')


def _plan(value, frozen):
    audit._fields(value,'schema_version profile frame_sha256 seed sample_size review_budget policy_sha256 model_sha256 decision_timestamp',
                  'invalid fixed audit plan')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PLAN_PROFILE
            or audit._sha(value['frame_sha256'])!=frozen['frame_sha256']
            or type(value['seed']) is not int or value['seed']<0
            or type(value['sample_size']) is not int or type(value['review_budget']) is not int
            or not 1<=value['sample_size']<=value['review_budget']
            or value['sample_size']>len(frozen['eligible'])):
        raise ValueError('invalid fixed audit plan, frame or budget')
    audit._sha(value['policy_sha256'],True);audit._sha(value['model_sha256'],True)
    audit._time(value['decision_timestamp'])


def _rank(seed, frame_sha, unit_sha):
    return hashlib.sha256(f'audit-fixed-v1:{seed}:{frame_sha}:{unit_sha}'.encode()).digest()


def draw(frame, plan):
    """Select exactly k unique units by hash rank; collisions are refused, never redrawn."""
    frozen=audit.freeze(frame);_plan(plan,frozen); ranks=[]; seen=set()
    for key,value in frozen['eligible'].items():
        rank=_rank(plan['seed'],frozen['frame_sha256'],key)
        if rank in seen:raise ValueError('fixed audit hash-rank collision')
        seen.add(rank);ranks.append((rank,key,value['alerted']))
    selected=sorted(ranks)[:plan['sample_size']]
    body=dict(schema_version=1,profile=RECEIPT_PROFILE,estimand=audit.ESTIMAND,
        frame_sha256=frozen['frame_sha256'],plan_sha256=audit._digest(plan),scope=frozen['scope'],
        window=frozen['window'],frame=frozen['summary'],sample_size=plan['sample_size'],
        review_budget=plan['review_budget'],inclusion_probability=dict(numerator=plan['sample_size'],
            denominator=len(frozen['eligible'])),
        draws=[dict(unit_sha256=key,alerted=alerted) for _,key,alerted in selected],
        limitations=list(LIMITATIONS),publication_allowed=False,automatic_policy_update=False)
    return audit._seal(body)


def review_queue(receipt, frame, plan):
    """Recompute the complete selection against caller-supplied inputs; no origin is authenticated."""
    if receipt!=draw(frame,plan):raise ValueError('fixed audit receipt differs from frame and plan replay')
    return dict(receipt_sha256=receipt['receipt_sha256'],prediction_masked=True,
                units=sorted(row['unit_sha256'] for row in receipt['draws']),publication_allowed=False)


def _interval(count, sample_size, population):
    """Two-sided bounded-outcome Hoeffding bound; union bound covers two endpoints."""
    rate=count/sample_size
    radius=0.0 if sample_size==population else math.sqrt(math.log(4/ALPHA)/(2*sample_size))
    return [max(0.0,rate-radius),min(1.0,rate+radius)]


def _recall_interval(prevalence,missed):
    if prevalence[1]==0:return [0.0,1.0]
    return [0.0 if prevalence[0]==0 else max(0.0,1-missed[1]/prevalence[0]),
            min(1.0,1-missed[0]/prevalence[1])]


def _reviewed(receipt,batch):
    """Extract reviewed labels without filling missing or nonresponse outcomes."""
    labels=audit._labels(batch,receipt);draws=receipt['draws'];rows=[];pending=0
    for item in draws:
        status,label=labels.get(item['unit_sha256'],('pending',None))
        if status!='reviewed':pending+=1
        else:rows.append(dict(item,incident=label!=audit.NO_INCIDENT))
    sampled={item['unit_sha256'] for item in draws}
    return rows,pending,sum(key not in sampled for key in labels)


def estimate(receipt, frame, plan, batch):
    """Report fixed-frame rates, conditional on exchangeable ranks and complete reviewed labels."""
    review_queue(receipt,frame,plan)
    draws=receipt['draws'];rows,pending,outside=_reviewed(receipt,batch)
    result=dict(schema_version=1,profile=REPORT_PROFILE,receipt_sha256=receipt['receipt_sha256'],
        estimand=audit.ESTIMAND,population_units=receipt['frame']['eligible_units'],sample_size=len(draws),
        reviewed=len(rows),pending_or_nonresponse=pending,outside_probability_sample=outside,
        inclusion_probability=receipt['inclusion_probability'],limitations=list(LIMITATIONS),
        publication_allowed=False,automatic_policy_update=False)
    if pending:
        return dict(result,status='inconclusive',reason='missing_review_labels',estimates=None)
    n=len(rows);population=receipt['frame']['eligible_units']
    incidents=sum(row['incident'] for row in rows)
    missed=sum(row['incident'] and not row['alerted'] for row in rows)
    prevalence=incidents/n;missed_rate=missed/n
    pi=_interval(incidents,n,population);mi=_interval(missed,n,population)
    recall=None if not incidents else (incidents-missed)/incidents
    recall_interval=_recall_interval(pi,mi)
    if recall is not None:
        # A census has no sampling uncertainty; use one computation for both
        # point and endpoints. Otherwise only widen the derived interval.
        recall_interval=([recall,recall] if n==population else
                         [min(recall_interval[0],recall),max(recall_interval[1],recall)])
    return dict(result,status='estimated',reason=None,estimates=dict(
        prevalence=dict(estimate=prevalence,ci95=pi),missed_incident_rate=dict(estimate=missed_rate,ci95=mi),
        triage_recall=dict(estimate=recall,ci95=recall_interval),
        interval='simultaneous_hoeffding_without_replacement',alpha=ALPHA))
