"""Private local audit-to-corpus observations; declarations are not authenticated authority."""
from copy import deepcopy

from . import audit_sampling as audit
from .corpus import CorpusStore, _eligible
from agent_harness_lab.evaluation.task_bank import identifier

PROFILE = 'audit-corpus-assignment-v1'
MAX_ASSIGNMENTS = 128


def _assignment(receipt, value):
    audit._fields(value,'schema_version profile receipt_sha256 rubric_sha256 assignments publication_allowed assignment_sha256',
                  'invalid audit corpus assignment')
    body={key:item for key,item in value.items() if key!='assignment_sha256'}
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PROFILE
            or value['receipt_sha256']!=receipt['receipt_sha256'] or value['publication_allowed'] is not False
            or audit._digest(body)!=value['assignment_sha256']):
        raise ValueError('audit corpus assignment differs')
    audit._sha(value['rubric_sha256'])
    rows=value['assignments']
    if not isinstance(rows,list) or not 0<len(rows)<=MAX_ASSIGNMENTS:
        raise ValueError('bounded nonempty audit assignments required')
    units=set();cases=set()
    for row in rows:
        audit._fields(row,'unit_sha256 case_id pending_event_sha256','invalid audit corpus assignment row')
        for item in row.values():audit._sha(item)
        if row['unit_sha256'] in units or row['case_id'] in cases:
            raise ValueError('duplicate audit assignment unit or case')
        units.add(row['unit_sha256']);cases.add(row['case_id'])
    if units!={item['unit_sha256'] for item in receipt['draws']}:
        raise ValueError('audit assignment does not cover exactly the draws')
    return rows


def rubric(value):
    """Validate explicit marker-to-class declarations; this is not semantic adjudication."""
    audit._fields(value,'schema_version profile markers','invalid audit corpus rubric')
    markers=value['markers']
    if (type(value['schema_version']) is not int or value['schema_version']!=1
            or value['profile']!='audit-corpus-rubric-v1' or not isinstance(markers,dict)
            or not 2<=len(markers)<=64):
        raise ValueError('invalid audit corpus rubric')
    for marker,label in markers.items():
        identifier(marker); audit._text(marker); audit._text(label)
    if audit.NO_INCIDENT not in markers.values() or not any(label!=audit.NO_INCIDENT for label in markers.values()):
        raise ValueError('explicit incident and no-incident markers required')
    return audit._digest(value)


def _current(store):
    rows,_,_=store._snapshot()
    return {key:opened for key,opened in rows}


def _queue(receipt,frame,trusted_heads,plan):
    """Replay only the explicitly supported design before touching corpus state."""
    if frame is None or not isinstance(receipt,dict):
        raise ValueError('explicit frame and supported audit receipt required')
    if receipt.get('profile')==audit.RECEIPT_PROFILE_V2 and plan is None:
        return audit.review_queue(receipt,trusted_heads,frame=frame)
    from . import audit_stratified
    if receipt.get('profile')==audit_stratified.RECEIPT_PROFILE and plan is not None and trusted_heads is None:
        return audit_stratified.review_queue(receipt,frame,plan)
    raise ValueError('explicit frame and supported audit receipt with matching plan required')


def prepare(receipt, frame, store_path, mapping, rubric_value, trusted_heads=None, *, plan=None):
    """Bind pending cases to a replayed sample, without proving assignment time."""
    _queue(receipt,frame,trusted_heads,plan)
    if (not isinstance(mapping,list) or not 0<len(mapping)<=MAX_ASSIGNMENTS):
        raise ValueError('bounded nonempty audit assignment mapping required')
    rubric_sha=rubric(rubric_value);store=CorpusStore(store_path);assignments=[]
    with store.locked():
        current=_current(store);opened={}
        for row in mapping:
            audit._fields(row,'unit_sha256 case_id','invalid audit assignment mapping')
            audit._sha(row['unit_sha256']);audit._sha(row['case_id'])
            if row['case_id'] not in current:raise ValueError('corpus case is not current')
            case,inputs,events,_,_=current[row['case_id']]
            assigned=len(events)>1 and events[1][0]['kind']=='audit_assigned'
            if ((not assigned and (len(events)!=1 or events[-1][0]['state']!='pending'))
                    or inputs['rubric_sha256']!=rubric_sha
                    or store._retirement(row['case_id'],inputs,events) is not None
                    or audit.unit_sha256(inputs['root_id'])!=row['unit_sha256']):
                raise ValueError('pending corpus case and matching root required')
            assignments.append(dict(row,pending_event_sha256=events[0][1]))
            opened[row['case_id']]=(case,events)
        body=dict(schema_version=1,profile=PROFILE,receipt_sha256=receipt['receipt_sha256'],rubric_sha256=rubric_sha,
                  assignments=sorted(assignments,key=lambda row:row['unit_sha256']),publication_allowed=False)
        result=dict(body,assignment_sha256=audit._digest(body))
        _assignment(receipt,result)
        # Each assignment event is durable before return. An interruption leaves a
        # partial batch that cannot be consumed; the identical batch can resume.
        pending=[]
        for row in result['assignments']:
            case,events=opened[row['case_id']]
            payload=dict(assignment_sha256=result['assignment_sha256'],receipt_sha256=receipt['receipt_sha256'],
                         rubric_sha256=rubric_sha,unit_sha256=row['unit_sha256'])
            if len(events)>1:
                if events[1][0]['payload']!=payload:raise ValueError('different existing audit assignment')
            else:pending.append((case,events,payload))
        for case,events,payload in pending:store.append(case,events,'audit_assigned','pending',payload)
        return result


def _observed(store, rows, binding):
    current=_current(store);observations=[]
    for row in rows:
        if row['case_id'] not in current:raise ValueError('assigned corpus case is not current')
        _,inputs,events,submissions,final=current[row['case_id']]
        if (events[0][1]!=row['pending_event_sha256'] or events[0][0]['state']!='pending'
                or inputs['rubric_sha256']!=binding['rubric_sha256']
                or audit.unit_sha256(inputs['root_id'])!=row['unit_sha256']):
            raise ValueError('assigned corpus case, rubric or pending event differs')
        expected=dict(assignment_sha256=binding['assignment_sha256'],receipt_sha256=binding['receipt_sha256'],
                      rubric_sha256=binding['rubric_sha256'],unit_sha256=row['unit_sha256'])
        if len(events)<2 or events[1][0]['kind']!='audit_assigned' or events[1][0]['payload']!=expected:
            raise ValueError('complete corpus assignment event required')
        summary=store._summary(row['case_id'],inputs,events,submissions,final)
        if store._retirement(row['case_id'],inputs,events) is not None:
            raise ValueError('assigned corpus case was exposed')
        observations.append((row,summary,events[-1][1],final,_eligible(inputs),events[-1][0]['kind']))
    return observations


def inspect(receipt, frame, store_path, binding, trusted_heads=None, *, plan=None):
    """Read local review lifecycle without inferring labels or authority."""
    _queue(receipt,frame,trusted_heads,plan)
    rows=_assignment(receipt,binding);store=CorpusStore(store_path)
    with store.locked():
        observed=_observed(store,rows,binding)
        observations=[dict(unit_sha256=row['unit_sha256'],case_id=row['case_id'],event_sha256=head,
                           state=summary['state'],disposition=summary['disposition'],latest_event=kind)
                      for row,summary,head,_,_,kind in observed]
    return dict(profile='audit-corpus-observation-v1',assignment_sha256=binding['assignment_sha256'],
        observations=deepcopy(observations),assignment_timing='unauthenticated',review_authentication='operator_attested',
        reviewer_independence='unverified',frame_origin='unauthenticated',incident_mapping='unavailable',
        labels_for_estimation=None,publication_allowed=False,automatic_policy_update=False)


def estimate(receipt, frame, store_path, binding, rubric_value, trusted_heads=None, *, plan=None):
    """Estimate only from explicit adjudicated markers under the bound caller-declared rubric."""
    _queue(receipt,frame,trusted_heads,plan)
    rows=_assignment(receipt,binding);rubric_value=deepcopy(rubric_value);rubric_sha=rubric(rubric_value)
    if rubric_sha!=binding['rubric_sha256']:raise ValueError('audit corpus rubric differs from assignment')
    store=CorpusStore(store_path)
    with store.locked():
        observed=_observed(store,rows,binding);labels=[]
        for row,summary,_,final,eligible,kind in observed:
            matches=set() if final is None else {rubric_value['markers'][key] for key in final['symptoms']
                                               if key in rubric_value['markers']}
            complete=(eligible and summary['state']=='adjudicated' and final is not None and final['outcome']!='unknown'
                      and len(matches)==1)
            status='reviewed' if complete else 'nonresponse' if kind=='nonresponse' else 'pending'
            labels.append(dict(unit_sha256=row['unit_sha256'],status=status,
                               label=next(iter(matches)) if complete else None))
        batch=dict(receipt_sha256=receipt['receipt_sha256'],labels=labels)
        if receipt['profile']==audit.RECEIPT_PROFILE_V2:
            report=audit.estimate(receipt,batch,trusted_heads,frame=frame)
        else:
            from . import audit_stratified
            report=audit_stratified.estimate(receipt,frame,plan,batch)
    report['limitations'].extend(('audit_assignment_timing_unauthenticated','audit_rubric_operator_declared',
                                   'corpus_reviewers_independence_unverified','corpus_lineage_binding_unverified'))
    report['corpus_review_status']={status:sum(row['status']==status for row in labels)
                                    for status in ('reviewed','pending','nonresponse')}
    if any(not eligible for _,_,_,_,eligible,_ in observed):
        report['limitations'].append('corpus_ineligible_or_ambiguous_observations')
    report['assignment_sha256']=binding['assignment_sha256']
    return report
