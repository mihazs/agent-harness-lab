"""Private diagnostic annotation journals; operator attestations are not gold truth."""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile

from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory, write_bytes
from agent_harness_lab.evaluation.task_bank import identifier

FORMAT = 'diagnostic-corpus-v1'
PARTITIONS = ('fitting','calibration','threshold','development','validation','certification')
OUTCOMES = ('task_failure','successful','external_failure','requirements_changed','unknown')
MAX_CASES = 128
PROVENANCE = 'operator_attested_adjudication'
LIMITATIONS = ('Local reviewer identities are unauthenticated; annotations are not executable task oracles.',
    'Near-duplicate adjudication, verified lineage binding and held-out classifier evaluation are not implemented.')


def _fields(value, names):
    if not isinstance(value,dict) or set(value) != set(names.split()):
        raise ValueError('diagnostic corpus fields differ from frozen schema')


def _text(value, maximum=4096):
    if not isinstance(value,str) or not value.strip() or len(value.encode('utf-8'))>maximum or '\0' in value:
        raise ValueError('bounded diagnostic text required')


def _sha(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('exact corpus SHA-256 required')


def _identity(value):
    identifier(value); _text(value,128)
    if value!=value.strip(): raise ValueError('canonical declared identity cannot contain surrounding whitespace')


def _digest(value): return hashlib.sha256(encoded(value)).hexdigest()


def incident(value):
    _fields(value, 'schema_version incident_id root_id family project_id time_group partition source_kind source_ref source_revision_sha256 permission license summary predicted_summary evidence capture_complete ambiguity selection_reason rubric_sha256 author supersedes')
    if type(value['schema_version']) is not int or value['schema_version']!=1: raise ValueError('unsupported incident schema')
    for key in ('incident_id','root_id','family','project_id','time_group','author'):
        _identity(value[key])
    for key in ('source_revision_sha256','rubric_sha256'): _sha(value[key])
    for key in ('source_ref','summary','license'): _text(value[key])
    if value['predicted_summary'] is not None: _text(value['predicted_summary'])
    if value['supersedes'] is not None: _sha(value['supersedes'])
    if (value['partition'] not in PARTITIONS or value['source_kind'] not in ('synthetic','public','private')
            or value['permission'] not in ('approved','unverified')
            or value['capture_complete'] is not None and type(value['capture_complete']) is not bool
            or type(value['ambiguity']) is not bool
            or value['selection_reason'] not in ('representative_sample','uncertainty','rare_class','known_error','manual')):
        raise ValueError('invalid corpus provenance/capture/selection')
    evidence=value['evidence']
    if not isinstance(evidence,list) or len(evidence)>16: raise ValueError('bounded evidence list required')
    seen=set()
    for item in evidence:
        _fields(item,'id content sha256'); identifier(item['id']); _text(item['content'])
        _sha(item['sha256'])
        if item['id'] in seen or hashlib.sha256(item['content'].encode()).hexdigest()!=item['sha256']:
            raise ValueError('duplicate or changed evidence fragment')
        seen.add(item['id'])
    if len(encoded(value))>128*1024: raise ValueError('incident metadata byte bound exceeded')
    return deepcopy(value)


def annotation(value, inputs):
    _fields(value,'outcome recovery symptoms mechanisms verification evidence_refs reason')
    if value['outcome'] not in OUTCOMES or value['recovery'] not in ('recovered','unrecovered','not_applicable','unknown'):
        raise ValueError('unsupported outcome/recovery distinction')
    if value['verification'] not in ('executable_artifact','reviewed_observation','unknown'): raise ValueError('invalid verification provenance')
    _text(value['reason'])
    for name in ('symptoms','mechanisms','evidence_refs'):
        rows=value[name]
        if not isinstance(rows,list) or len(rows)>16 or len(set(map(str,rows)))!=len(rows):
            raise ValueError('bounded unique annotation references required')
        for row in rows: identifier(row); _text(row,128)
    if not set(value['evidence_refs']) <= {e['id'] for e in inputs['evidence']}:
        raise ValueError('annotation evidence is outside the frozen incident')
    if value['outcome']!='unknown' and (not value['evidence_refs'] or value['verification']=='unknown'
            or inputs['capture_complete'] is not True or inputs['permission']!='approved'):
        raise ValueError('missing outcome/capture evidence requires an unknown annotation')
    if value['mechanisms'] and not value['evidence_refs']: raise ValueError('mechanism labels require cited evidence')
    return deepcopy(value)


def _eligible(inputs):
    return inputs['capture_complete'] is True and inputs['permission']=='approved' and not inputs['ambiguity']


def _separate(groups, texts, inputs, partition):
    for group in (('root',inputs['root_id']),('incident',inputs['incident_id']),('source',inputs['source_revision_sha256'])):
        if groups.setdefault(group,partition)!=partition: raise ValueError('protected corpus split leakage')
    normalized=' '.join(inputs['summary'].split()).casefold()
    if texts.setdefault(normalized,partition)!=partition: raise ValueError('copied text aliases leak across protected splits')


def transition(state, kind, target, previous_kind=None):
    if state is None: return kind==target=='pending'
    if kind=='claimed': return state in ('pending','submitted') and target=='claimed'
    if kind=='submitted': return state=='claimed' and target in ('submitted','reviewed')
    if kind=='adjudicated': return state=='reviewed' and target=='adjudicated'
    if kind=='exposed': return state!='exposed' and target=='exposed'
    return False


class CorpusStore(PrivateJournal):
    """Single local store for queue history and immutable corpus input revisions."""
    def __init__(self, root): super().__init__(root,FORMAT,transition)

    def _cases(self):
        return self.case_ids(MAX_CASES)

    def _opened(self, case_id):
        case=self.case(case_id); events=self.history(case)
        inputs,identity=record(case/'incident.json',128*1024); inputs=incident(inputs)
        if _digest(inputs)!=case_id or events[0][0]['payload']!={'input_sha256':identity}:
            raise ValueError('immutable corpus incident changed')
        submissions=[]; claimant=None; final=None
        for event,_ in events[1:]:
            payload=event['payload']; kind=event['kind']
            if kind=='claimed':
                _fields(payload,'reviewer'); _identity(payload['reviewer'])
                if payload['reviewer'].casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}:
                    raise ValueError('separate declared reviewer identity required')
                claimant=payload['reviewer']
            elif kind=='submitted':
                _fields(payload,'reviewer label')
                if payload['reviewer']!=claimant or len(submissions)>=2: raise ValueError('review claim/submission differs')
                annotation(payload['label'],inputs); submissions.append(payload); claimant=None
                if event['state']!=('submitted' if len(submissions)==1 else 'reviewed'):
                    raise ValueError('review count differs from lifecycle')
            elif kind=='adjudicated':
                _fields(payload,'reviewer label reason'); _identity(payload['reviewer']); _text(payload['reason'])
                if len(submissions)!=2 or payload['reviewer'].casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}:
                    raise ValueError('adjudication requires a separate declared reviewer')
                final=annotation(payload['label'],inputs)
            elif kind=='exposed':
                _fields(payload,'reason'); _text(payload['reason'])
        return case,inputs,events,submissions,final

    def _summary(self, case_id, inputs, events, submissions, final):
        labels=[s['label'] for s in submissions]
        disposition=('unresolved' if final is not None and final['outcome']=='unknown' else 'adjudicated' if final is not None else
            'agreement' if len(labels)==2 and labels[0]==labels[1] else 'disagreement' if len(labels)==2 else 'unresolved')
        exposed=events[-1][0]['state']=='exposed'
        prior=inputs['supersedes']; seen=set()
        while prior is not None:
            if prior in seen or len(seen)>=MAX_CASES: raise ValueError('invalid incident revision ancestry')
            seen.add(prior); _,previous,history,_,_=self._opened(prior)
            exposed |= any(e['kind']=='exposed' for e,_ in history); prior=previous['supersedes']
        return dict(schema_version=1,case_id=case_id,event_sha256=events[-1][1],state=events[-1][0]['state'],
            partition='development' if exposed else inputs['partition'],
            submissions=len(submissions),disposition=disposition,review_authentication='operator_attested',
            reviewer_independence='unverified',root_independence='unverified',gold_label=False,
            classifier_selection_allowed=False,task_outcome_oracle=False,publication_allowed=False)

    def enqueue(self, value):
        inputs=incident(value); case_id=_digest(inputs)
        with self.locked(create=True):
            existing=self._cases()
            if len(existing)>=MAX_CASES: raise ValueError('corpus capacity exhausted')
            if self.case(case_id).exists(): raise ValueError('incident revision already queued')
            if inputs['supersedes'] is not None:
                _,old,_,_,_=self._opened(inputs['supersedes'])
                if old['incident_id']!=inputs['incident_id'] or all(old[k]==inputs[k] for k in ('source_revision_sha256','rubric_sha256')):
                    raise ValueError('requeue requires changed source/rubric for the same incident')
                if any(old[k]!=inputs[k] for k in ('root_id','family','project_id','time_group','partition','source_kind')):
                    raise ValueError('requeue cannot relabel lineage/privacy/partition identity')
            for key in existing:
                _,prior,_,_,_=self._opened(key)
                if inputs['supersedes'] is None and prior['incident_id']==inputs['incident_id']:
                    raise ValueError('existing incident requires an explicit source/rubric requeue')
                if inputs['supersedes'] is not None and prior['supersedes']==inputs['supersedes']:
                    raise ValueError('stale competing incident revision')
            with tempfile.TemporaryDirectory(prefix='.corpus-stage-',dir=self.root) as folder:
                stage=Path(folder)/'case'; stage.mkdir(mode=0o700); (stage/'events').mkdir(mode=0o700)
                raw=encoded(inputs); write_bytes(stage/'incident.json',raw)
                events=[]; self.append(stage,events,'pending','pending',dict(input_sha256=hashlib.sha256(raw).hexdigest()))
                sync_directory(stage)
                cases=self.root/'cases'; cases.mkdir(mode=0o700,exist_ok=True)
                stage.rename(self.case(case_id)); sync_directory(cases); sync_directory(self.root)
            return self._summary(case_id,inputs,events,[],None)

    def packet(self, case_id, reviewer):
        """Convenience blinding; the local store owner can still inspect journal files."""
        _identity(reviewer)
        with self.locked():
            _,inputs,events,submissions,final=self._opened(case_id)
            visible={k:deepcopy(inputs[k]) for k in ('incident_id','source_revision_sha256','rubric_sha256','summary','evidence','capture_complete','ambiguity')}
            return dict(**self._summary(case_id,inputs,events,submissions,final),review_packet=visible,
                prior_labels_visible=False,predicted_summary_visible=False)

    def update(self, case_id, expected_event_sha256, kind, *, reviewer=None, label=None, reason=None):
        _sha(expected_event_sha256)
        with self.locked():
            case,inputs,events,submissions,final=self._opened(case_id)
            if expected_event_sha256!=events[-1][1]: raise ValueError('stale corpus event revision')
            if kind=='exposed': _text(reason); payload=dict(reason=reason); target='exposed'
            elif kind in ('claimed','submitted','adjudicated'):
                _identity(reviewer); payload=dict(reviewer=reviewer); target=kind
                if kind!='claimed': payload['label']=annotation(label,inputs)
                if kind=='submitted': target='submitted' if not submissions else 'reviewed'
                if kind=='adjudicated': _text(reason); payload['reason']=reason
            else: raise ValueError('unsupported corpus queue operation')
            if not transition(events[-1][0]['state'],kind,target,events[-1][0]['kind']): raise ValueError('invalid corpus lifecycle transition')
            if kind=='claimed' and reviewer.casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}:
                raise ValueError('independent review cannot reuse a declared identity')
            if kind=='submitted' and reviewer!=events[-1][0]['payload'].get('reviewer'):
                raise ValueError('only the current claimant can submit')
            if kind=='adjudicated' and (len(submissions)!=2 or reviewer.casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}):
                raise ValueError('adjudication requires a separate declared reviewer')
            self.append(case,events,kind,target,payload)
            _,inputs,events,submissions,final=self._opened(case_id)
            return self._summary(case_id,inputs,events,submissions,final)

    def _snapshot(self):
        from agent_harness_lab.evaluation.decision_receipts import engine
        rows=[(case_id,self._opened(case_id)) for case_id in self._cases()]
        superseded={row[1][1]['supersedes'] for row in rows if row[1][1]['supersedes'] is not None}
        if any(sum(row[1][1]['supersedes']==prior for row in rows)>1 for prior in superseded):
            raise ValueError('ambiguous competing incident revisions')
        current=[row for row in rows if row[0] not in superseded]
        summaries=[self._summary(key,*opened[1:]) for key,opened in current]
        revision=_digest(dict(engine=engine(),preprocessing='whitespace-casefold-v1',cases=[(key,opened[2][-1][1]) for key,opened in rows]))
        return current,summaries,revision

    def snapshot(self):
        with self.locked():
            _,items,revision=self._snapshot()
            return dict(schema_version=1,revision_sha256=revision,items=items,publication_allowed=False,
                classifier_selection_allowed=False,root_independence='unverified')

    def export_development(self, expected_revision_sha256):
        """Export reviewed declarations only; protected labels have no training export."""
        _sha(expected_revision_sha256)
        with self.locked():
            rows,summaries,revision=self._snapshot()
            if revision!=expected_revision_sha256: raise ValueError('stale corpus export revision')
            groups={}; texts={}; exported=[]
            for (key,(_,inputs,events,submissions,final)),summary in zip(rows,summaries):
                partition=summary['partition']; _separate(groups,texts,inputs,partition)
                if partition not in ('fitting','development') or final is None or final['outcome']=='unknown': continue
                if not _eligible(inputs): continue
                exported.append(dict(case_id=key,partition=partition,record=deepcopy(inputs),annotation=deepcopy(final),
                    review_event_sha256=events[-1][1],label_provenance=PROVENANCE,gold_label=False))
            body=dict(schema_version=1,profile=FORMAT,revision_sha256=revision,records=exported,
                publication_allowed=False,classifier_selection_allowed=False,root_independence='unverified',
                limitations=list(LIMITATIONS))
            return dict(body,manifest_sha256=_digest(body))


def development_export(value):
    """Check an export_development manifest for read-only consumers such as the benchmark.

    The digest detects changed or truncated content; it is not an authenticity signature,
    so the protected-partition, eligibility and provenance checks run independently."""
    _fields(value,'schema_version profile revision_sha256 records publication_allowed classifier_selection_allowed root_independence limitations manifest_sha256')
    _sha(value['revision_sha256']); _sha(value['manifest_sha256'])
    if value['manifest_sha256']!=_digest({k:v for k,v in value.items() if k!='manifest_sha256'}):
        raise ValueError('diagnostic corpus manifest digest differs')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=FORMAT
            or value['publication_allowed'] is not False or value['classifier_selection_allowed'] is not False
            or value['root_independence']!='unverified' or value['limitations']!=list(LIMITATIONS)):
        raise ValueError('diagnostic corpus manifest authority differs')
    rows=value['records']
    if not isinstance(rows,list) or len(rows)>MAX_CASES: raise ValueError('bounded diagnostic corpus records required')
    groups={}; texts={}; seen=set()
    for row in rows:
        _fields(row,'case_id partition record annotation review_event_sha256 label_provenance gold_label')
        inputs=incident(row['record']); _sha(row['review_event_sha256']); partition=row['partition']
        if row['case_id']!=_digest(inputs) or row['case_id'] in seen: raise ValueError('diagnostic corpus record identity differs')
        seen.add(row['case_id'])
        # Exposure retires any declared partition to development; nothing else may change it.
        if (partition not in ('fitting','development') or partition!=inputs['partition'] and partition!='development'
                or row['label_provenance']!=PROVENANCE or row['gold_label'] is not False or not _eligible(inputs)):
            raise ValueError('diagnostic corpus export has protected, ineligible or relabelled items')
        if annotation(row['annotation'],inputs)['outcome']=='unknown': raise ValueError('unknown annotations are not exported labels')
        _separate(groups,texts,inputs,partition)
    return deepcopy(value)
