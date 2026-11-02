"""Default-off controller search with durable charges and untouched final callback."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time
from agent_harness_lab.private_journal import encoded, record
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.evaluation.regressions import fields, sha256
from .budget import Budget
from agent_harness_lab.monitoring.state import atomic_json
from .pareto import frontier

MAX_STATE_BYTES = 8*1024*1024


@dataclass(frozen=True)
class CandidateRecord:
    candidate_id: str
    parents: tuple
    mechanism: str
    changes: dict
    evidence_refs: tuple
    hypothesis: str
    falsifier: str

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.changes,sort_keys=True).encode()).hexdigest()

    def validate(self, allowed_files):
        if not self.candidate_id or not self.mechanism or not self.evidence_refs or not self.hypothesis or not self.falsifier:
            raise ValueError('candidate requires causal hypothesis/evidence/falsifier')
        if len(self.changes)>100 or not set(self.changes)<=set(allowed_files):
            raise ValueError('candidate exceeds authorized surface')
        size=0
        for path, content in self.changes.items():
            relative(path)
            if content is not None and not isinstance(content,str):
                raise ValueError('patch content must be text or deletion')
            size+=len((content or '').encode())
        if size>1024*1024: raise ValueError('candidate patch byte bound')


def compose(left,right,candidate_id):
    overlap=left.changes.keys() & right.changes.keys()
    if any(left.changes[k]!=right.changes[k] for k in overlap):
        raise ValueError('composition conflict')
    return CandidateRecord(candidate_id,(left.candidate_id,right.candidate_id),
        left.mechanism+'+'+right.mechanism,{**left.changes,**right.changes},
        left.evidence_refs+right.evidence_refs,'Combined patch needs interaction evaluation',
        'Any component or interaction regression rejects the composition')


def _callback_search(path, baseline, propose, evaluate, certify, budget, allowed_files, *, enabled=False, variants=3):
    if not enabled: return dict(status='disabled',automatic_promotion=False)
    if not 1<=variants<=3: raise ValueError('invalid bounded candidate pool')
    path=Path(path)
    if path.exists():
        state=json.loads(path.read_text())
        if state.get('schema_version')!=1: raise ValueError('unsupported search state')
        # Interrupted charged stages are never silently rerun.
        return dict(state,status=state.get('status','interrupted'),resumed_without_execution=True)
    state=dict(schema_version=1,status='searching',candidates=[],rejections=[],budget_events=[],automatic_promotion=False,
               evidence_source='unverified_callback',publication_allowed=False,adoption_authority=False)
    seen=set(); candidate_ids=set()
    run_id=hashlib.sha256(str(path.absolute()).encode()).hexdigest()[:16]
    def save():
        state['budget_events']=budget.events
        atomic_json(path,state)
    for index in range(variants+1):
        kind='baseline' if index==0 else 'proposal'
        event_id=f'{run_id}:{kind}:{index}'
        if not budget.consume(kind,1,event_id):
            state['status']='budget_exhausted'; save(); return state
        state.update(stage='in_progress',event_id=event_id); save()
        candidate=baseline if index==0 else propose(index)  # Development-only callback; no final data.
        try:
            candidate.validate(allowed_files)
        except (ValueError,TypeError,AttributeError):
            state['rejections'].append(dict(index=index,reason='invalid_or_unauthorized_proposal')); save(); continue
        if candidate.digest in seen or candidate.candidate_id in candidate_ids:
            state['rejections'].append(dict(candidate_id=candidate.candidate_id,reason='duplicate_patch')); save(); continue
        seen.add(candidate.digest); candidate_ids.add(candidate.candidate_id)
        if not budget.consume('development_trial',1,run_id+':trial:'+str(index)):
            state['status']='budget_exhausted'; save(); return state
        save()
        frozen_digest=candidate.digest
        result=evaluate(candidate)
        if candidate.digest!=frozen_digest:
            state['rejections'].append(dict(candidate_id=candidate.candidate_id,reason='candidate_mutated_during_evaluation'))
            state['stage']='development_rejected'
            if index==0:
                state['status']='blocked'; save(); return state
            save(); continue
        state['candidates'].append(dict(candidate_id=candidate.candidate_id,digest=candidate.digest,
            parents=list(candidate.parents),record=asdict(candidate),safe=result.get('safe') is True,
            metrics=result.get('metrics',{}),evidence_scope='development_only'))
        state['stage']='development_complete'; save()
    if not any(c['candidate_id']==baseline.candidate_id and c['safe'] for c in state['candidates']):
        state['status']='blocked'; save(); return state
    choices=frontier(state['candidates'])
    # Ambiguous frontiers/ties keep the no-op; point estimates are not certification.
    best=next((c for c in choices if c['candidate_id']==baseline.candidate_id),None)
    if best is None and len(choices)==1: best=choices[0]
    if best is None or best['candidate_id']==baseline.candidate_id:
        state['status']='no_change'; save(); return state
    if not budget.consume('final_certification',1,run_id+':final:'+best['candidate_id'],final=True):
        state['status']='budget_exhausted'; save(); return state
    state.update(status='certification_in_progress',finalist=best['candidate_id']); save()
    state['certification']=certify(CandidateRecord(**{**best['record'],'parents':tuple(best['record']['parents']),
                                                   'evidence_refs':tuple(best['record']['evidence_refs'])}))
    state['status']='recommended' if state['certification'].get('status')=='recommended' else 'rejected_or_inconclusive'
    state['stage']='complete'; save(); return state


@contextmanager
def _private_state(path, context):
    path = Path(path).absolute()
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('guarded search requires a canonical private state path')
    for name, location in context['paths'].items():
        if location is None:
            continue
        protected = Path(location)
        root = protected.parent if name == 'manifest' else protected
        if path.is_relative_to(root):
            raise ValueError('search state must remain outside controller input and custody views')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.parent.stat()
    if info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError('guarded search state directory must be owner-only')
    lock = path.with_name(path.name + '.lock')
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError('unsafe guarded search lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise ValueError('guarded search state must be private and regular')
        yield path
    finally:
        os.close(fd)


def _candidate(candidate, allowed_files):
    if not isinstance(candidate, CandidateRecord):
        raise ValueError('guarded search requires a candidate record')
    candidate.validate(allowed_files)
    raw = encoded(asdict(candidate))
    if len(raw) > 256*1024:
        raise ValueError('guarded candidate declaration byte limit exceeded')
    value = json.loads(raw)
    for key in ('candidate_id', 'mechanism', 'hypothesis', 'falsifier'):
        text = value[key]
        if not isinstance(text, str) or not text or len(text.encode()) > (128 if key == 'candidate_id' else 4096) or '\0' in text:
            raise ValueError('bounded candidate identity and rationale required')
    for key in ('parents', 'evidence_refs'):
        if not isinstance(value[key], list) or len(value[key]) > 32 or any(not isinstance(item, str) or not item or len(item.encode()) > 4096 for item in value[key]):
            raise ValueError('bounded candidate references required')
    return value


def _restored(value, allowed_files):
    fields(value, 'candidate_id parents mechanism changes evidence_refs hypothesis falsifier')
    candidate = CandidateRecord(**{**value, 'parents': tuple(value['parents']), 'evidence_refs': tuple(value['evidence_refs'])})
    if _candidate(candidate, allowed_files) != value:
        raise ValueError('candidate record changed')
    return candidate


def _verified(controller, attempt, context):
    binding = attempt['binding']
    fresh = controller.verify(binding)
    if (not isinstance(fresh, dict) or encoded(fresh.get('binding')) != encoded(binding)
            or binding['template_sha256'] != context['template_sha256']
            or binding['candidate_record'] != attempt['record'] or binding['patch_sha256'] != attempt['patch_sha256']
            or binding['run_id'] != attempt['run_id'] or binding['event_id'] != attempt['event_id']
            or binding['stage'] != attempt['kind'] or type(fresh.get('safe')) is not bool
            or type(fresh.get('retain_no_op')) is not bool or type(fresh.get('qualified_feasible')) is not bool):
        raise ValueError('search binding differs from actual controller custody')
    metrics = fresh.get('metrics')
    if not isinstance(metrics, dict) or set(metrics) not in (set(), {'primary_effect'}):
        raise ValueError('only observed primary effect can rank guarded search')
    if metrics and (type(metrics['primary_effect']) not in (int, float) or not math.isfinite(metrics['primary_effect'])):
        raise ValueError('invalid observed primary effect')
    if fresh['safe'] and (not fresh['qualified_feasible'] or not metrics):
        raise ValueError('favorable search evidence needs qualified numeric criteria')
    return dict(candidate_id=attempt['record']['candidate_id'], digest=attempt['patch_sha256'],
        record=attempt['record'], binding=binding, safe=fresh['safe'], metrics=dict(metrics),
        retain_no_op=fresh['retain_no_op'], qualified_feasible=fresh['qualified_feasible'],
        evidence_scope='controller_verified_development_only')


def _guarded_search(path, baseline, propose, budget, allowed_files, controller, variants, parent_deadline):
    started_at = time.monotonic()
    from .history_evidence import HistorySearchContext
    if type(budget) is not Budget:
        raise ValueError('guarded search requires the actual bounded stage budget')
    Budget(budget.ceiling, budget.reserve, budget.events)
    if type(controller) is not HistorySearchContext:
        raise ValueError('guarded search requires the actual history controller context')
    if type(variants) is not int or not 1 <= variants <= 3:
        raise ValueError('invalid bounded candidate pool')
    context = controller.template_binding
    baseline_record = _candidate(baseline, allowed_files)
    if baseline_record['changes'] or sorted(allowed_files) != context['allowed_files']:
        raise ValueError('guarded baseline must be the unchanged authorized context')
    if parent_deadline is None:
        parent_deadline = started_at + context['resource_profile']['budget_seconds']
    if type(parent_deadline) not in (int, float) or not math.isfinite(parent_deadline):
        raise ValueError('bounded parent deadline required')
    with _private_state(path, context) as path:
        prefix = hashlib.sha256(str(path).encode()).hexdigest()[:16]
        def save():
            state['budget']['events'] = json.loads(encoded(budget.events))
            if len(encoded(state)) > MAX_STATE_BYTES:
                raise ValueError('guarded search state byte limit exceeded')
            atomic_json(path, state)
        def finish(status, reason, candidates=None):
            state.update(status=status, reason=reason)
            save()
            return dict(state, candidates=candidates or [], resumed_without_execution=resumed)
        resumed = path.exists()
        if resumed:
            state = record(path, MAX_STATE_BYTES)[0]
            fields(state, 'schema_version context baseline_record variants allowed_files budget attempts confirmation status reason finalist automatic_promotion publication_allowed adoption_authority evidence_source')
            if (type(state['schema_version']) is not int or state['schema_version'] != 2
                    or encoded(state['context']) != encoded(context) or state['baseline_record'] != baseline_record
                    or type(state['variants']) is not int or state['variants'] != variants
                    or state['allowed_files'] != sorted(allowed_files) or state['automatic_promotion'] is not False
                    or state['publication_allowed'] is not False or state['adoption_authority'] is not False
                    or state['evidence_source'] != 'controller_verified_development_only'):
                raise ValueError('guarded search context or schema changed')
            fields(state['budget'], 'ceiling reserve events')
            if (any(type(state['budget'][key]) not in (int, float) for key in ('ceiling', 'reserve'))
                    or (state['budget']['ceiling'], state['budget']['reserve']) != (budget.ceiling, budget.reserve)):
                raise ValueError('guarded search budget changed')
            events = state['budget']['events']
            if not isinstance(events, list) or len(events) > 16:
                raise ValueError('bounded persisted search charges required')
            for event in events:
                fields(event, 'kind amount event_id final')
                if type(event['amount']) is not int or event['amount'] != 1 or type(event['final']) is not bool:
                    raise ValueError('invalid persisted search charge')
            restored = Budget(budget.ceiling, budget.reserve, events)
            if budget.events and encoded(budget.events) != encoded(restored.events):
                raise ValueError('caller budget differs from durable charges')
            budget.events = json.loads(encoded(restored.events))
        else:
            if budget.events:
                raise ValueError('new guarded search requires its own unspent stage budget')
            state = dict(schema_version=2, context=context, baseline_record=baseline_record, variants=variants,
                allowed_files=sorted(allowed_files), budget=dict(ceiling=budget.ceiling, reserve=budget.reserve, events=[]),
                attempts=[], confirmation=None, status='searching', reason='not_completed', finalist=None,
                automatic_promotion=False, publication_allowed=False, adoption_authority=False,
                evidence_source='controller_verified_development_only')
            save()
        attempts = state['attempts']
        if not isinstance(attempts, list) or len(attempts) > variants + 1:
            raise ValueError('bounded guarded attempts required')
        charge_ids = set()
        def validate_attempt(attempt, index, confirmation=False):
            fields(attempt, 'index kind event_id record patch_sha256 stage run_id binding')
            kind = 'development_confirmation' if confirmation else 'baseline' if index == 0 else 'development'
            event_id = prefix + (':confirmation' if confirmation else ':trial:' + str(index))
            if type(attempt['index']) is not int or attempt['index'] != index or attempt['kind'] != kind or attempt['event_id'] != event_id:
                raise ValueError('guarded attempt identity changed')
            proposal_id = prefix + ':proposal:' + str(index)
            if not confirmation:
                charge_ids.add(proposal_id)
                expected_charge = dict(kind='baseline' if index == 0 else 'proposal', amount=1, event_id=proposal_id, final=False)
                if expected_charge not in budget.events:
                    raise ValueError('missing durable proposal charge')
            if attempt['record'] is not None:
                candidate = _restored(attempt['record'], allowed_files)
                if candidate.digest != attempt['patch_sha256']:
                    raise ValueError('persisted patch identity changed')
            if attempt['stage'] not in ('proposal_charged', 'invalid', 'duplicate', 'trial_charged', 'run_started', 'complete'):
                raise ValueError('invalid guarded attempt stage')
            if attempt['stage'] in ('trial_charged', 'run_started', 'complete'):
                charge_ids.add(event_id)
                expected_charge = dict(kind='development_confirmation' if confirmation else 'development_trial', amount=1, event_id=event_id, final=confirmation)
                if expected_charge not in budget.events:
                    raise ValueError('missing durable execution charge')
            if attempt['run_id'] is not None:
                sha256(attempt['run_id'])
            if attempt['binding'] is not None and (attempt['stage'] != 'complete' or attempt['run_id'] is None):
                raise ValueError('receipt has no completed charged custody')
        verified = []
        try:
            for index, attempt in enumerate(attempts):
                validate_attempt(attempt, index)
                if attempt['binding'] is not None:
                    verified.append(_verified(controller, attempt, context))
            if state['confirmation'] is not None:
                validate_attempt(state['confirmation'], variants + 1, True)
            if set(e['event_id'] for e in budget.events) != charge_ids:
                raise ValueError('persisted charges differ from guarded stages')
        except (ValueError, OSError, KeyError, TypeError):
            return finish('blocked', 'history_evidence_invalid')
        if resumed and (any(a['stage'] not in ('complete', 'invalid', 'duplicate') for a in attempts)
                        or state['confirmation'] is not None and state['confirmation']['stage'] != 'complete'):
            return finish('interrupted', 'charged_stage_not_redispatched', verified)
        if not resumed:
            seen_patches, seen_ids = set(), set()
            for index in range(variants + 1):
                if time.monotonic() >= parent_deadline:
                    return finish('budget_exhausted', 'parent_deadline', verified)
                if not budget.consume('baseline' if index == 0 else 'proposal', 1, prefix + ':proposal:' + str(index)):
                    return finish('budget_exhausted', 'parent_stage_budget', verified)
                attempt = dict(index=index, kind='baseline' if index == 0 else 'development', event_id=prefix + ':trial:' + str(index),
                    record=None, patch_sha256=None, stage='proposal_charged', run_id=None, binding=None)
                attempts.append(attempt); save()
                try:
                    candidate = baseline if index == 0 else propose(index)
                    attempt['record'] = _candidate(candidate, allowed_files)
                    attempt['patch_sha256'] = candidate.digest
                except (ValueError, TypeError, AttributeError):
                    attempt['stage'] = 'invalid'; save(); continue
                if candidate.digest in seen_patches or candidate.candidate_id in seen_ids:
                    attempt['stage'] = 'duplicate'; save(); continue
                seen_patches.add(candidate.digest); seen_ids.add(candidate.candidate_id)
                if not budget.consume('development_trial', 1, attempt['event_id']):
                    return finish('budget_exhausted', 'parent_stage_budget', verified)
                attempt['stage'] = 'trial_charged'; save()
                def started(run_id):
                    sha256(run_id)
                    if attempt['run_id'] is not None:
                        raise ValueError('duplicate guarded controller start')
                    attempt.update(run_id=run_id, stage='run_started'); save()
                try:
                    result = controller.evaluate(candidate, stage=attempt['kind'], event_id=attempt['event_id'],
                        on_started=started, parent_deadline=parent_deadline)
                    attempt.update(binding=result['binding'], stage='complete')
                    if candidate.digest != attempt['patch_sha256'] or _candidate(candidate, allowed_files) != attempt['record']:
                        raise ValueError('candidate changed during controller execution')
                    verified.append(_verified(controller, attempt, context)); save()
                except BaseException as error:
                    finish('interrupted' if isinstance(error, KeyboardInterrupt) else 'blocked', 'controller_execution_not_replayed', verified)
                    if isinstance(error, KeyboardInterrupt): raise
                    return dict(state, candidates=verified, resumed_without_execution=False)
                if index == 0 and not verified[-1]['safe']:
                    return finish('no_change' if verified[-1]['retain_no_op'] else 'blocked', 'baseline_not_qualified_for_search', verified)
        if not verified or verified[0]['record'] != baseline_record:
            return finish('blocked', 'missing_verified_baseline', verified)
        if controller.template_binding != context:
            return finish('blocked', 'context_changed', verified)
        choices = frontier(verified)
        best = next((item for item in choices if item['candidate_id'] == baseline.candidate_id), None)
        if best is None and len(choices) == 1:
            best = choices[0]
        if best is None or best['candidate_id'] == baseline.candidate_id:
            return finish('no_change', 'no_unique_verified_improvement', verified)
        if len(attempts) != variants + 1 or any(a['stage'] not in ('complete', 'invalid', 'duplicate') for a in attempts):
            return finish('interrupted', 'charged_stage_not_redispatched', verified)
        state['finalist'] = best['candidate_id']
        confirmation = state['confirmation']
        if confirmation is None:
            if resumed:
                return finish('interrupted', 'confirmation_not_dispatched_on_resume', verified)
            event_id = prefix + ':confirmation'
            if not budget.consume('development_confirmation', 1, event_id, final=True):
                return finish('budget_exhausted', 'confirmation_reserve', verified)
            confirmation = dict(index=variants + 1, kind='development_confirmation', event_id=event_id,
                record=best['record'], patch_sha256=best['digest'], stage='trial_charged', run_id=None, binding=None)
            state['confirmation'] = confirmation; save()
            def started_confirmation(run_id):
                sha256(run_id)
                if confirmation['run_id'] is not None:
                    raise ValueError('duplicate confirmation start')
                confirmation.update(run_id=run_id, stage='run_started'); save()
            try:
                candidate = _restored(best['record'], allowed_files)
                result = controller.evaluate(candidate, stage='development_confirmation', event_id=event_id,
                    on_started=started_confirmation, parent_deadline=parent_deadline)
                confirmation.update(binding=result['binding'], stage='complete'); save()
            except BaseException as error:
                finish('interrupted' if isinstance(error, KeyboardInterrupt) else 'blocked', 'confirmation_not_replayed', verified)
                if isinstance(error, KeyboardInterrupt): raise
                return dict(state, candidates=verified, resumed_without_execution=resumed)
        try:
            if (confirmation['record'] != best['record'] or confirmation['patch_sha256'] != best['digest']
                    or any(confirmation['binding'][key] == item['binding'][key] for item in verified for key in ('run_id', 'attempt_id'))):
                raise ValueError('confirmation reused earlier custody')
            # Reopen every relevant receipt immediately before a favorable return.
            verified = [_verified(controller, attempt, context) for attempt in attempts if attempt['binding'] is not None]
            refreshed = frontier(verified)
            if len(refreshed) != 1 or refreshed[0]['record'] != best['record']:
                raise ValueError('selected candidate is no longer the unique verified improvement')
            checked = _verified(controller, confirmation, context)
            if controller.template_binding != context:
                raise ValueError('context changed before confirmation return')
        except (ValueError, OSError, KeyError, TypeError):
            return finish('blocked', 'history_evidence_invalid', verified)
        if checked['safe'] and time.monotonic() >= parent_deadline:
            return finish('budget_exhausted', 'parent_deadline_after_confirmation', verified)
        return finish('development_recommended_for_review' if checked['safe'] else 'rejected_or_inconclusive',
                      'fresh_development_confirmation_only', verified)


def search(path, baseline, propose, evaluate, certify, budget, allowed_files, *, enabled=False, variants=3,
           controller=None, parent_deadline=None):
    if not enabled:
        return dict(status='disabled', automatic_promotion=False, publication_allowed=False, adoption_authority=False)
    if controller is not None:
        return _guarded_search(path, baseline, propose, budget, allowed_files, controller, variants, parent_deadline)
    result = _callback_search(path, baseline, propose, evaluate, certify, budget, allowed_files, enabled=True, variants=variants)
    return dict(result, evidence_source='unverified_callback', automatic_promotion=False,
                publication_allowed=False, adoption_authority=False)
