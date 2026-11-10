"""Private local audit-to-corpus observations; declarations are not authenticated authority."""
from copy import deepcopy

from . import audit_sampling as audit
from .corpus import CorpusStore

PROFILE = 'audit-corpus-assignment-v1'
MAX_ASSIGNMENTS = 128


def _assignment(receipt, value):
    audit._fields(value,'schema_version profile receipt_sha256 assignments publication_allowed assignment_sha256',
                  'invalid audit corpus assignment')
    body={key:item for key,item in value.items() if key!='assignment_sha256'}
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=PROFILE
            or value['receipt_sha256']!=receipt['receipt_sha256'] or value['publication_allowed'] is not False
            or audit._digest(body)!=value['assignment_sha256']):
        raise ValueError('audit corpus assignment differs')
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


def _current(store):
    rows,_,_=store._snapshot()
    return {key:opened for key,opened in rows}


def prepare(receipt, frame, store_path, mapping, trusted_heads=None):
    """Bind caller-selected pending cases to a replayed v2 sample, without proving assignment time."""
    if frame is None or receipt.get('profile')!=audit.RECEIPT_PROFILE_V2:
        raise ValueError('explicit frame and v2 audit receipt required')
    audit.review_queue(receipt,trusted_heads,frame=frame)
    if (not isinstance(mapping,list) or not 0<len(mapping)<=MAX_ASSIGNMENTS):
        raise ValueError('bounded nonempty audit assignment mapping required')
    store=CorpusStore(store_path);assignments=[]
    with store.locked():
        current=_current(store)
        for row in mapping:
            audit._fields(row,'unit_sha256 case_id','invalid audit assignment mapping')
            audit._sha(row['unit_sha256']);audit._sha(row['case_id'])
            if row['case_id'] not in current:raise ValueError('corpus case is not current')
            _,inputs,events,_,_=current[row['case_id']]
            if (len(events)!=1 or events[0][0]['state']!='pending'
                    or store._retirement(row['case_id'],inputs,events) is not None
                    or audit.unit_sha256(inputs['root_id'])!=row['unit_sha256']):
                raise ValueError('pending corpus case and matching root required')
            assignments.append(dict(row,pending_event_sha256=events[0][1]))
        body=dict(schema_version=1,profile=PROFILE,receipt_sha256=receipt['receipt_sha256'],
                  assignments=sorted(assignments,key=lambda row:row['unit_sha256']),publication_allowed=False)
        result=dict(body,assignment_sha256=audit._digest(body))
        _assignment(receipt,result)
        return result


def inspect(receipt, frame, store_path, binding, trusted_heads=None):
    """Read current local review lifecycle, not incident labels or population estimates."""
    if frame is None or receipt.get('profile')!=audit.RECEIPT_PROFILE_V2:
        raise ValueError('explicit frame and v2 audit receipt required')
    audit.review_queue(receipt,trusted_heads,frame=frame)
    rows=_assignment(receipt,binding);store=CorpusStore(store_path); observations=[]
    with store.locked():
        current=_current(store)
        for row in rows:
            if row['case_id'] not in current:raise ValueError('assigned corpus case is not current')
            _,inputs,events,submissions,final=current[row['case_id']]
            if (events[0][1]!=row['pending_event_sha256'] or events[0][0]['state']!='pending'
                    or audit.unit_sha256(inputs['root_id'])!=row['unit_sha256']):
                raise ValueError('assigned corpus case or pending event differs')
            summary=store._summary(row['case_id'],inputs,events,submissions,final)
            if store._retirement(row['case_id'],inputs,events) is not None:
                raise ValueError('assigned corpus case was exposed')
            observations.append(dict(unit_sha256=row['unit_sha256'],case_id=row['case_id'],
                event_sha256=events[-1][1],state=summary['state'],disposition=summary['disposition']))
    return dict(profile='audit-corpus-observation-v1',assignment_sha256=binding['assignment_sha256'],
        observations=deepcopy(observations),assignment_timing='unauthenticated',review_authentication='operator_attested',
        reviewer_independence='unverified',frame_origin='unauthenticated',incident_mapping='unavailable',
        labels_for_estimation=None,publication_allowed=False,automatic_policy_update=False)
