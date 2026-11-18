"""Opt-in hard-budget audit that draws whole lineage clusters; conclusions depend on declared assumptions."""
import hashlib
import math
from fractions import Fraction

from . import audit_fixed as fixed, audit_sampling as audit

MAP_PROFILE='audit-cluster-map-v1'
PLAN_PROFILE='audit-cluster-plan-v1'
RECEIPT_PROFILE='audit-cluster-receipt-v1'
REPORT_PROFILE='audit-cluster-estimate-v1'
LIMITATIONS=('frame_origin_unauthenticated','cluster_map_origin_unauthenticated','sampling_plan_timing_unauthenticated',
             'reviewer_labels_unauthenticated','hash_rank_exchangeability_unverified','capture_outside_frame_unknown',
             'cluster_independence_declared_not_verified')


def cluster_sha256(cluster_id):
    """Receipts and reports carry cluster hashes only, never raw cluster identifiers."""
    return hashlib.sha256(f'audit-cluster-id-v1:{audit._text(cluster_id)}'.encode()).hexdigest()


def _groups(value,frozen):
    """Validate that the map assigns every eligible unit to exactly one declared cluster."""
    audit._fields(value,'schema_version profile frame_sha256 clusters','invalid audit cluster map')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=MAP_PROFILE
            or audit._sha(value['frame_sha256'])!=frozen['frame_sha256'] or not isinstance(value['clusters'],list)
            or not 0<len(value['clusters'])<=audit.MAX_UNITS):
        raise ValueError('invalid audit cluster map, frame or cluster list')
    groups={};owner=set()
    for raw in value['clusters']:
        audit._fields(raw,'cluster_id unit_ids','invalid audit cluster map entry')
        name=cluster_sha256(raw['cluster_id']);members=raw['unit_ids']
        if name in groups or not isinstance(members,list) or not members or len(members)>len(frozen['eligible']):
            raise ValueError('cluster identifiers must be unique and every cluster must list its units')
        keys=[audit.unit_sha256(audit._text(unit_id)) for unit_id in members]
        if any(key not in frozen['eligible'] for key in keys):
            raise ValueError('cluster map names a unit outside the captured eligible population')
        if owner&set(keys) or len(set(keys))!=len(keys):raise ValueError('a unit may belong to exactly one cluster')
        owner.update(keys);groups[name]=sorted(keys)
    if len(owner)!=len(frozen['eligible']):
        raise ValueError('every eligible unit needs a declared cluster; unknown lineage is never a singleton')
    return groups


def _map_sha(frozen,groups):
    return audit._digest(dict(frame_sha256=frozen['frame_sha256'],clusters=groups))


def layout(frame,cluster_map):
    """Expose the cluster count and sizes the caller needs to set a sample size and budget."""
    frozen=audit.freeze(frame);groups=_groups(cluster_map,frozen)
    return dict(frame_sha256=frozen['frame_sha256'],cluster_map_sha256=_map_sha(frozen,groups),frame=frozen['summary'],
                clusters=len(groups),largest_cluster_units=max(map(len,groups.values())),publication_allowed=False)


def _plan(plan,frozen,groups,map_sha):
    audit._fields(plan,'schema_version profile frame_sha256 cluster_map_sha256 seed cluster_sample_size review_budget policy_sha256 model_sha256 decision_timestamp',
                  'invalid cluster audit plan')
    size=plan['cluster_sample_size']
    if (type(plan['schema_version']) is not int or plan['schema_version']!=1 or plan['profile']!=PLAN_PROFILE
            or audit._sha(plan['frame_sha256'])!=frozen['frame_sha256'] or audit._sha(plan['cluster_map_sha256'])!=map_sha
            or type(plan['seed']) is not int or plan['seed']<0 or type(size) is not int or not 1<=size<=len(groups)
            or type(plan['review_budget']) is not int or plan['review_budget']<1):
        raise ValueError('invalid cluster audit plan, frame, cluster map or sample size')
    # Budget by the n largest clusters, so no seed can select more units than the cap and none is truncated.
    if sum(sorted(map(len,groups.values()),reverse=True)[:size])>plan['review_budget']:
        raise ValueError('the largest clusters of the sample size exceed the review budget')
    audit._sha(plan['policy_sha256'],True);audit._sha(plan['model_sha256'],True);audit._time(plan['decision_timestamp'])


def _rank(seed,map_sha,cluster):
    return hashlib.sha256(f'audit-cluster-v1:{seed}:{map_sha}:{cluster}'.encode()).digest()


def draw(frame,cluster_map,plan):
    """Draw whole clusters without replacement by hash rank; collisions are refused, never redrawn."""
    frozen=audit.freeze(frame);groups=_groups(cluster_map,frozen);map_sha=_map_sha(frozen,groups)
    _plan(plan,frozen,groups,map_sha);ranks=[];seen=set()
    for name in groups:
        rank=_rank(plan['seed'],map_sha,name)
        if rank in seen:raise ValueError('cluster audit hash-rank collision')
        seen.add(rank);ranks.append((rank,name))
    chosen=[name for _,name in sorted(ranks)[:plan['cluster_sample_size']]]
    return audit._seal(dict(schema_version=1,profile=RECEIPT_PROFILE,estimand=audit.ESTIMAND,
        frame_sha256=frozen['frame_sha256'],cluster_map_sha256=map_sha,plan_sha256=audit._digest(plan),scope=frozen['scope'],
        window=frozen['window'],frame=frozen['summary'],review_budget=plan['review_budget'],
        clusters=dict(total=len(groups),sample_size=len(chosen),largest_cluster_units=max(map(len,groups.values())),
            inclusion_probability=dict(numerator=len(chosen),denominator=len(groups))),
        draws=[dict(unit_sha256=key,alerted=frozen['eligible'][key]['alerted'],cluster_sha256=name)
               for name in chosen for key in groups[name]],
        limitations=list(LIMITATIONS),publication_allowed=False,automatic_policy_update=False))


def review_queue(receipt,frame,cluster_map,plan):
    """Recompute the complete selection against caller-supplied inputs; no origin is authenticated."""
    if receipt!=draw(frame,cluster_map,plan):raise ValueError('cluster audit receipt differs from frame, map and plan replay')
    return dict(receipt_sha256=receipt['receipt_sha256'],prediction_masked=True,
                units=sorted(row['unit_sha256'] for row in receipt['draws']),publication_allowed=False)


def _bounds(point,radius):
    return [min(1.0,max(0.0,point-radius)),min(1.0,max(0.0,point+radius))]


def estimate(receipt,frame,cluster_map,plan,batch):
    """Cluster-total estimates; every unit of a drawn cluster must be reviewed, or the result is inconclusive."""
    review_queue(receipt,frame,cluster_map,plan);rows,pending,outside=fixed._reviewed(receipt,batch)
    info=receipt['clusters'];total,size,units=info['total'],info['sample_size'],receipt['frame']['eligible_units']
    result=dict(schema_version=1,profile=REPORT_PROFILE,receipt_sha256=receipt['receipt_sha256'],estimand=audit.ESTIMAND,
        population_units=units,population_clusters=total,sampled_clusters=size,sample_units=len(receipt['draws']),
        review_budget=receipt['review_budget'],reviewed=len(rows),pending_or_nonresponse=pending,
        outside_probability_sample=outside,inclusion_probability=info['inclusion_probability'],
        limitations=list(LIMITATIONS),publication_allowed=False,automatic_policy_update=False)
    if pending:return dict(result,status='inconclusive',reason='missing_review_labels',estimates=None)
    incidents=sum(row['incident'] for row in rows);missed=sum(row['incident'] and not row['alerted'] for row in rows)
    census=size==total
    # A cluster total is bounded by the largest cluster, so Hoeffding applies to N*y_c/U over the sampled clusters;
    # the union covers both endpoints of both rates. Units inside a cluster are never treated as independent.
    radius=0.0 if census else total*info['largest_cluster_units']/units*math.sqrt(math.log(4/fixed.ALPHA)/(2*size))
    points=[float(Fraction(total*count,size*units)) for count in (incidents,missed)]
    prevalence,missed_rate=_bounds(points[0],radius),_bounds(points[1],radius)
    recall=None if not incidents else (incidents-missed)/incidents
    recall_interval=fixed._recall_interval(prevalence,missed_rate)
    if recall is not None:
        recall_interval=[recall,recall] if census else [min(recall_interval[0],recall),max(recall_interval[1],recall)]
    return dict(result,status='estimated',reason=None,estimates=dict(
        prevalence=dict(estimate=min(1.0,points[0]),ci95=prevalence),
        missed_incident_rate=dict(estimate=min(1.0,points[1]),ci95=missed_rate),
        triage_recall=dict(estimate=recall,ci95=recall_interval),
        interval='simultaneous_cluster_hoeffding_without_replacement',alpha=fixed.ALPHA))
