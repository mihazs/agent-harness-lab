"""Private diagnostic annotation journals; operator attestations are not gold truth."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import os
from pathlib import Path
import re
import tempfile

from agent_harness_lab.diagnostics import triage_policy
from agent_harness_lab.evaluation.integrity import contained
from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory, write_bytes
from agent_harness_lab.evaluation.task_bank import identifier

FORMAT = 'diagnostic-corpus-v1'
PARTITIONS = ('fitting','calibration','threshold','development','validation','certification')
OUTCOMES = ('task_failure','successful','external_failure','requirements_changed','unknown')
MAX_CASES = 128
PROVENANCE = 'operator_attested_adjudication'
NEAR_RULE = 'word-3gram-jaccard-v1'
NEAR_PERMILLE = 800
NEAR_DECISIONS = ('distinct_incident','same_lineage')
MAX_DECISIONS = 1024
ACCESS_PROFILE = 'diagnostic-corpus-protected-access-v1'
ACCESS_PARTITION = 'validation'  # certification stays reserved: no genuine qualification issuer exists
ACCESS_LIMITATIONS = ('Validation labels are operator-attested declarations, not gold labels; reviewer and root independence are unverified.',
    'Retirement records the exposure in this store only; it cannot stop a model owner from refitting on the reported result.')
LIMITATIONS = ('Local reviewer identities are unauthenticated; annotations are not executable task oracles.',
    'Near-duplicate detection is lexical only; verified lineage binding and held-out classifier evaluation are not implemented.')


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


def _outcome(opened):
    """The eligible known adjudicated terminal outcome of one opened case, else None."""
    _,inputs,_,_,final=opened
    return final['outcome'] if final is not None and final['outcome']!='unknown' and _eligible(inputs) else None


def _separate(groups, texts, inputs, partition):
    for group in (('root',inputs['root_id']),('incident',inputs['incident_id']),('source',inputs['source_revision_sha256'])):
        if groups.setdefault(group,partition)!=partition: raise ValueError('protected corpus split leakage')
    normalized=' '.join(inputs['summary'].split()).casefold()
    if (texts.setdefault(('summary',normalized),partition)!=partition
            or any(other!=partition for other in texts.get(('fragment',normalized),set()))):
        raise ValueError('copied text aliases leak across protected splits')
    for item in inputs['evidence']:
        fragment=' '.join(item['content'].split()).casefold()
        summary_partition=texts.get(('summary',fragment))
        if summary_partition is not None and summary_partition!=partition:
            raise ValueError('copied text aliases leak across protected splits')
        texts.setdefault(('fragment',fragment),set()).add(partition)


@lru_cache(maxsize=4096)
def _shingles(text):
    words=re.findall(r'\w+',text.casefold())
    return frozenset(tuple(words[i:i+3]) for i in range(max(1,len(words)-2)))


def _jaccard(a, b): return len(a&b)*1000//len(a|b)


def similarity(first, second):
    """Largest lexical overlap (permille) between one incident's summary and the other's summary/fragments."""
    mine,theirs=_shingles(first['summary']),_shingles(second['summary'])
    return max(_jaccard(mine,theirs),*(_jaccard(mine,_shingles(e['content'])) for e in second['evidence']),
        *(_jaccard(theirs,_shingles(e['content'])) for e in first['evidence']))


def _decision(value):
    _fields(value,'schema_version cases rule threshold_permille similarity_permille decision reviewer reason review_authentication recorded_at')
    _text(value['reason']); _identity(value['reviewer']); _text(value['recorded_at'],64)
    cases=value['cases']
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or not isinstance(cases,list) or len(cases)!=2
            or cases!=sorted(set(cases)) or value['rule']!=NEAR_RULE or value['threshold_permille']!=NEAR_PERMILLE
            or type(value['similarity_permille']) is not int or not NEAR_PERMILLE<=value['similarity_permille']<=1000
            or value['decision'] not in NEAR_DECISIONS or value['review_authentication']!='operator_attested'):
        raise ValueError('invalid near-duplicate decision')
    for case in cases: _sha(case)
    return deepcopy(value)


def _near(entries, decisions):
    """Fail when a lexically near pair crosses partitions without a recorded distinct-incident decision.

    entries are (case_id, effective_partition, incident); decisions map sorted case-ID pairs to records."""
    for index,(first,part,mine) in enumerate(entries):
        for second,other,theirs in entries[index+1:]:
            if part==other: continue
            score=similarity(mine,theirs)
            if score<NEAR_PERMILLE: continue
            found=decisions.get(tuple(sorted((first,second))))
            if found is None: raise ValueError('near-duplicate text across protected splits requires a recorded decision')
            if found['similarity_permille']!=score: raise ValueError('near-duplicate decision differs from current text')
            if found['decision']!='distinct_incident': raise ValueError('same-lineage near duplicates leak across protected splits')


def transition(state, kind, target, previous_kind=None):
    if state is None: return kind==target=='pending'
    if kind=='audit_assigned': return state=='pending' and previous_kind=='pending' and target=='pending'
    if kind=='claimed': return state in ('pending','submitted') and target=='claimed'
    if kind=='nonresponse': return state=='claimed' and previous_kind=='claimed' and target=='claimed'
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
            if kind=='audit_assigned':
                _fields(payload,'assignment_sha256 receipt_sha256 rubric_sha256 unit_sha256')
                for pin in payload.values(): _sha(pin)
                if payload['rubric_sha256']!=inputs['rubric_sha256']:
                    raise ValueError('audit assignment rubric differs from corpus case')
            elif kind=='claimed':
                _fields(payload,'reviewer'); _identity(payload['reviewer'])
                if payload['reviewer'].casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}:
                    raise ValueError('separate declared reviewer identity required')
                claimant=payload['reviewer']
            elif kind=='nonresponse':
                _fields(payload,'reviewer'); _identity(payload['reviewer'])
                if payload['reviewer']!=claimant:raise ValueError('nonresponse differs from review claim')
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

    def _retirement(self, case_id, inputs, events):
        """Return the newest exposure event that retires this incident lineage, if any."""
        found=None; key=case_id; history=events; prior=inputs['supersedes']; seen=set()
        while True:
            if found is None:
                found=next((dict(case_id=key,event_sha256=sha,reason=event['payload']['reason'])
                    for event,sha in history if event['kind']=='exposed'),None)
            if prior is None: return found
            if prior in seen or len(seen)>=MAX_CASES: raise ValueError('invalid incident revision ancestry')
            seen.add(prior); _,previous,history,_,_=self._opened(prior); key=prior; prior=previous['supersedes']

    def _summary(self, case_id, inputs, events, submissions, final):
        labels=[s['label'] for s in submissions]
        disposition=('unresolved' if final is not None and final['outcome']=='unknown' else 'adjudicated' if final is not None else
            'agreement' if len(labels)==2 and labels[0]==labels[1] else 'disagreement' if len(labels)==2 else 'unresolved')
        exposed=self._retirement(case_id,inputs,events) is not None
        return dict(schema_version=1,case_id=case_id,event_sha256=events[-1][1],state=events[-1][0]['state'],
            partition='development' if exposed else inputs['partition'],
            submissions=len(submissions),disposition=disposition,review_authentication='operator_attested',
            reviewer_independence='unverified',root_independence='unverified',gold_label=False,
            classifier_selection_allowed=False,task_outcome_oracle=False,publication_allowed=False)

    def _decisions(self):
        folder=contained(self.root,'near_duplicates'); found={}
        if not folder.exists(): return found
        with os.scandir(folder) as entries:
            for entry in entries:
                if len(found)>=MAX_DECISIONS: raise ValueError('near-duplicate decision bound exceeded')
                value,_=record(Path(entry.path),32*1024)  # worst case: 4096 control bytes escape to 6 chars each; value=_decision(value)
                if entry.name!=_digest(value['cases'])+'.json': raise ValueError('near-duplicate decision file differs from its pair')
                found[tuple(value['cases'])]=value
        return found

    def decide_near_duplicate(self, first, second, decision, reviewer, reason):
        """Record one immutable operator-attested decision for a lexically near pair of incident revisions."""
        _identity(reviewer); _text(reason)
        if decision not in NEAR_DECISIONS or first==second: raise ValueError('distinct near-duplicate pair and decision required')
        pair=sorted((first,second))
        with self.locked():
            mine,theirs=(self._opened(key)[1] for key in pair); score=similarity(mine,theirs)
            if score<NEAR_PERMILLE: raise ValueError('pair is not a near duplicate under the frozen rule')
            if reviewer.casefold() in {mine['author'].casefold(),theirs['author'].casefold()}:
                raise ValueError('near-duplicate decision requires a separate declared reviewer')
            value=dict(schema_version=1,cases=pair,rule=NEAR_RULE,threshold_permille=NEAR_PERMILLE,similarity_permille=score,
                decision=decision,reviewer=reviewer,reason=reason,review_authentication='operator_attested',
                recorded_at=datetime.now(timezone.utc).isoformat())
            path=contained(self.root,f'near_duplicates/{_digest(pair)}.json')
            if path.exists(): raise ValueError('near-duplicate decision already recorded')
            self._publish(self._locked_store,path,value)
            return _decision(value)

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
            elif kind in ('claimed','nonresponse','submitted','adjudicated'):
                if kind=='nonresponse' and (label is not None or reason is not None):
                    raise ValueError('nonresponse cannot carry a label or reason')
                _identity(reviewer); payload=dict(reviewer=reviewer); target='claimed' if kind=='nonresponse' else kind
                if kind in ('submitted','adjudicated'): payload['label']=annotation(label,inputs)
                if kind=='submitted': target='submitted' if not submissions else 'reviewed'
                if kind=='adjudicated': _text(reason); payload['reason']=reason
            else: raise ValueError('unsupported corpus queue operation')
            if not transition(events[-1][0]['state'],kind,target,events[-1][0]['kind']): raise ValueError('invalid corpus lifecycle transition')
            if kind=='claimed' and reviewer.casefold() in {inputs['author'].casefold(),*(s['reviewer'].casefold() for s in submissions)}:
                raise ValueError('independent review cannot reuse a declared identity')
            if kind in ('submitted','nonresponse') and reviewer!=events[-1][0]['payload'].get('reviewer'):
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
        revision=_digest(dict(engine=engine(),preprocessing='whitespace-casefold-v1',near_duplicate=NEAR_RULE,
            decisions=sorted(_digest(v) for v in self._decisions().values()),cases=[(key,opened[2][-1][1]) for key,opened in rows]))
        return current,summaries,revision

    def snapshot(self):
        with self.locked():
            _,items,revision=self._snapshot()
            return dict(schema_version=1,revision_sha256=revision,items=items,publication_allowed=False,
                classifier_selection_allowed=False,root_independence='unverified')

    def _isolated(self, rows, summaries):
        """Raise on an alias or undecided near duplicate across effective partitions; return the recorded decisions."""
        groups={}; texts={}; decisions=self._decisions()
        for (_,opened),summary in zip(rows,summaries): _separate(groups,texts,opened[1],summary['partition'])
        _near([(key,summary['partition'],opened[1]) for (key,opened),summary in zip(rows,summaries)],decisions)
        return decisions

    def _untouched(self):
        """Snapshot, the untouched validation cases (every review state, so inputs reveal no label progress) and their labels.

        A root is labeled only when each of its cases has an eligible, known adjudication and they agree, so no
        rejection depends on a label; otherwise it is unlabeled."""
        rows,summaries,revision=self._snapshot(); self._isolated(rows,summaries)
        cases=sorted((key,opened) for (key,opened),summary in zip(rows,summaries) if summary['partition']==ACCESS_PARTITION)
        if not cases: raise ValueError('no untouched validation cases')
        roots={}
        for _,opened in cases: roots.setdefault(opened[1]['root_id'],set()).add(_outcome(opened))
        labels=[_outcome(opened) if len(roots[opened[1]['root_id']])==1 else None for _,opened in cases]
        return revision,cases,labels,_digest([revision,[key for key,_ in cases]])

    def protected_inputs(self):
        """Label-free classifier inputs for the untouched validation partition, bound to this exact corpus revision."""
        with self.locked():
            revision,cases,_,plan=self._untouched()
            return dict(schema_version=1,profile=ACCESS_PROFILE,partition=ACCESS_PARTITION,revision_sha256=revision,plan_sha256=plan,
                cases=[dict(case_id=key,text=opened[1]['summary']) for key,opened in cases],publication_allowed=False)

    def evaluate_protected(self, plan_sha256, policy, model_sha256, scores):
        """Evaluate frozen per-case class scores against validation labels once; no label is released.

        ``scores`` maps every case ID of the ``protected_inputs`` plan to one score per policy class. The policy
        classes must be exactly the corpus terminal outcomes, a label-independent check. The frozen triage policy
        evaluation runs first, so an invalid input retires nothing. A returned result means every labeled case is
        retired to development, so a validation label supports one evaluation."""
        _sha(plan_sha256); _sha(model_sha256)
        if set(triage_policy.check_policy(policy,require_disjoint_roles=True)[0])!=set(OUTCOMES)-{'unknown'}:
            raise ValueError('policy classes must be exactly the corpus terminal outcomes')
        with self.locked():
            revision,cases,labels,plan=self._untouched()
            if plan_sha256!=plan: raise ValueError('stale protected access plan')
            if not isinstance(scores,dict) or set(scores)!={key for key,_ in cases}:
                raise ValueError('scores must cover exactly the frozen validation cases')
            report=triage_policy.evaluate(policy,[dict(root_case_id=opened[1]['root_id'],split='heldout',label=label,
                scores=scores[key],model_sha256=model_sha256) for (key,opened),label in zip(cases,labels)],report_version=5)
            used=[item for item,label in zip(cases,labels) if label is not None]
            if any(len(opened[2])>=self.max_events for _,opened in used): raise ValueError('case journal operation bound exceeded')
            access_id=_digest(dict(plan=plan,policy=report['policy_sha256'],model=model_sha256,scores=scores))
            for _,(case,_,events,_,_) in used:
                self.append(case,events,'exposed','exposed',dict(reason='protected validation access '+access_id))
            return dict(access=dict(schema_version=1,profile=ACCESS_PROFILE,access_id=access_id,partition=ACCESS_PARTITION,
                corpus_revision_sha256=revision,plan_sha256=plan,policy_sha256=report['policy_sha256'],model_sha256=model_sha256,
                labeled_cases=len(used),unlabeled_cases=len(cases)-len(used),retired_case_ids=[key for key,_ in used],
                label_provenance=PROVENANCE,gold_label=False,reviewer_authentication='operator_attested',
                root_independence='unverified',classifier_selection_allowed=False,publication_allowed=False,
                limitations=list(ACCESS_LIMITATIONS)),evaluation=report)

    def export_development(self, expected_revision_sha256):
        """Export reviewed declarations only; protected labels have no training export."""
        _sha(expected_revision_sha256)
        with self.locked():
            rows,summaries,revision=self._snapshot()
            if revision!=expected_revision_sha256: raise ValueError('stale corpus export revision')
            exported=[]; decisions=self._isolated(rows,summaries)
            for (key,(_,inputs,events,submissions,final)),summary in zip(rows,summaries):
                partition=summary['partition']
                if partition not in ('fitting','development') or final is None or final['outcome']=='unknown': continue
                if not _eligible(inputs): continue
                exported.append(dict(case_id=key,partition=partition,record=deepcopy(inputs),annotation=deepcopy(final),
                    review_event_sha256=events[-1][1],label_provenance=PROVENANCE,gold_label=False,
                    retirement=self._retirement(key,inputs,events)))
            ids={row['case_id'] for row in exported}
            body=dict(schema_version=1,profile=FORMAT,revision_sha256=revision,records=exported,
                near_duplicate_decisions=[v for k,v in sorted(decisions.items()) if set(k)<=ids],
                publication_allowed=False,classifier_selection_allowed=False,root_independence='unverified',
                limitations=list(LIMITATIONS))
            return dict(body,manifest_sha256=_digest(body))


def _retired(row, inputs):
    """Require the exported exposure reference wherever the effective partition differs.

    Exposure is the last event of its case, so a reference to the row's own case must
    name its final event; an earlier revision's reference requires a supersedes link."""
    retirement=row['retirement']
    if retirement is None:
        if row['partition']!=inputs['partition']: raise ValueError('changed partition lacks exposure retirement evidence')
        return
    _fields(retirement,'case_id event_sha256 reason'); _sha(retirement['case_id']); _sha(retirement['event_sha256']); _text(retirement['reason'])
    if (row['partition']!='development' or retirement['case_id']==row['case_id'] and retirement['event_sha256']!=row['review_event_sha256']
            or retirement['case_id']!=row['case_id'] and inputs['supersedes'] is None):
        raise ValueError('exposure retirement evidence differs from the exported row')


def development_export(value):
    """Check an export_development manifest for read-only consumers such as the benchmark.

    The digest detects changed or truncated content; it is not an authenticity signature,
    so the protected-partition, eligibility and provenance checks run independently."""
    _fields(value,'schema_version profile revision_sha256 records near_duplicate_decisions publication_allowed classifier_selection_allowed root_independence limitations manifest_sha256')
    _sha(value['revision_sha256']); _sha(value['manifest_sha256'])
    if value['manifest_sha256']!=_digest({k:v for k,v in value.items() if k!='manifest_sha256'}):
        raise ValueError('diagnostic corpus manifest digest differs')
    if (type(value['schema_version']) is not int or value['schema_version']!=1 or value['profile']!=FORMAT
            or value['publication_allowed'] is not False or value['classifier_selection_allowed'] is not False
            or value['root_independence']!='unverified' or value['limitations']!=list(LIMITATIONS)):
        raise ValueError('diagnostic corpus manifest authority differs')
    rows=value['records']
    if not isinstance(rows,list) or len(rows)>MAX_CASES: raise ValueError('bounded diagnostic corpus records required')
    groups={}; texts={}; seen=set(); entries=[]
    for row in rows:
        _fields(row,'case_id partition record annotation review_event_sha256 label_provenance gold_label retirement')
        inputs=incident(row['record']); _sha(row['review_event_sha256']); partition=row['partition']
        if row['case_id']!=_digest(inputs) or row['case_id'] in seen: raise ValueError('diagnostic corpus record identity differs')
        seen.add(row['case_id']); _retired(row,inputs)
        if (partition not in ('fitting','development') or row['label_provenance']!=PROVENANCE or row['gold_label'] is not False or not _eligible(inputs)):
            raise ValueError('diagnostic corpus export has protected, ineligible or relabelled items')
        if annotation(row['annotation'],inputs)['outcome']=='unknown': raise ValueError('unknown annotations are not exported labels')
        _separate(groups,texts,inputs,partition); entries.append((row['case_id'],partition,inputs))
    decisions={}
    for item in value['near_duplicate_decisions']:
        item=_decision(item)
        if not set(item['cases'])<=seen or tuple(item['cases']) in decisions: raise ValueError('near-duplicate decision names no exported pair')
        decisions[tuple(item['cases'])]=item
    _near(entries,decisions)
    return deepcopy(value)
