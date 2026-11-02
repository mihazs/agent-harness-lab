"""Fresh entire paired execution from pinned prior declarations, never receipt import."""
from dataclasses import replace
import hashlib
import math
from pathlib import Path,PurePosixPath
import re
import secrets
import sys
import time

from agent_harness_lab.private_journal import MAX_EVENTS,encoded,record,sync_directory,write_bytes
from .bank_corrections import canonical,committed_application
from .bank_revisions import sync_tree
from .bank_tools import export_manifest
from .integrity import contained,digest,read_regular
from .oracle import identity
from .oracle_history import HistoryStore
from .plan import ExperimentSpec
from .regressions import fields,sha256,text


def request_value(value,case_id):
    fields(value,'schema_version case_id expected_event_sha256 evidence_sha256 bank_sha256 revision_sha256 history_store prior operator reason')
    if type(value['schema_version']) is not int or value['schema_version']!=1 or value['case_id']!=case_id:
        raise ValueError('rerun case/schema mismatch')
    for key in ('case_id','expected_event_sha256','evidence_sha256','bank_sha256','revision_sha256'): sha256(value[key])
    if not isinstance(value['history_store'],str) or str(canonical(value['history_store']))!=value['history_store']:
        raise ValueError('rerun history locator must be explicit and canonical')
    fields(value['prior'],'run_id event_sha256 inputs_sha256 plan_sha256 evidence_sha256')
    for key,val in value['prior'].items():
        if key=='evidence_sha256' and val is None: continue
        sha256(val)
    fields(value['operator'],'identity source provenance basis')
    for key in ('identity','provenance','basis'): text(value['operator'][key])
    if len(value['operator']['identity'])>128 or value['operator']['source']!='operator_attestation':
        raise ValueError('explicit operator rerun provenance required')
    text(value['reason'])


def prior_binding(history,request,*,historical=False):
    prior = history._historical_plan(request['prior']['run_id'],**({'event_sha256':request['prior']['event_sha256']} if historical else {}))
    if any(prior[key]!=value for key,value in request['prior'].items()):
        raise ValueError('stale or forged prior controller evidence')
    return prior


def declared_inputs(prior,source,corrected):
    previous = {t['id']:t for t in prior['manifest']['tasks']}
    for current in source['tasks']:
        name = current['id']; original = previous[name]
        if (any(current.get(k)!=original.get(k) for k in ('id','root','family','split','prompt','artifacts','offline_executor'))
                or current['split']!='development'
                or not (current['grader'][0]==original['grader'][0]
                    or current['grader'][0]=='/usr/bin/python3' and normalizable_selector(original['grader'][0]))
                or current['grader'][2]!=original['grader'][2]):
            raise ValueError('rerun task execution/policy differs from earlier declaration')
        if name!=corrected and source['grader_bundles'][name]['digest']!=prior['manifest']['grader_bundles'][name]['digest']:
            raise ValueError('rerun changed an unrelated oracle')
    for key in ('max_trials','budget_seconds','timeout_seconds'):
        if (key in source)!=(key in prior['manifest']) or source.get(key)!=prior['manifest'].get(key):
            raise ValueError('rerun changed earlier declared execution budgets')


def grading_selector(original):
    """Normalize a relocated host Python label to the same pinned grading command."""
    if original in ('python','python3','/usr/bin/python3',sys.executable): return original
    if not normalizable_selector(original): raise ValueError('earlier grading interpreter cannot be safely normalized')
    return '/usr/bin/python3'


def normalizable_selector(original):
    if original in ('python','python3','/usr/bin/python3'): return True
    if not isinstance(original,str) or not 0<len(original)<=4096 or '\0' in original: return False
    path = PurePosixPath(original)
    return (path.is_absolute() and str(path)==original and '..' not in path.parts
        and re.fullmatch(r'python(?:3(?:\.[0-9]{1,2})?)?',path.name) is not None)


def started_identity(attempt,history):
    value,_ = record(attempt/'started-run.json'); fields(value,'run_id'); sha256(value['run_id'])
    _,_,inputs,_ = history._opened(value['run_id'],current_bank=False)
    if inputs['source_manifest_sha256']!=record(attempt/'export/manifest.json',1024*1024)[1]:
        raise ValueError('generated run identity belongs to another attempt')
    return value['run_id'],inputs


def validate_events(store,case_id,case,events):
    """Read-side completion is tied to actual controller history, not copied JSON."""
    latest = None
    for index,(event,_) in enumerate(events):
        if event['kind']=='rerun_started':
            fields(event['payload'],'attempt request_sha256')
            attempt = contained(case,event['payload']['attempt'])
            request,request_hash = record(attempt/'request.json',64*1024); request_value(request,case_id)
            if request_hash!=event['payload']['request_sha256'] or request['expected_event_sha256']!=event['previous_sha256']:
                raise ValueError('persisted rerun request/CAS changed')
            applied = next(e for e,_ in events[:index] if e['kind']=='applied')
            if (request['bank_sha256']!=applied['payload']['bank_sha256']
                    or request['revision_sha256']!=applied['payload']['revision_sha256']):
                raise ValueError('rerun refers to another applied bank')
            audited = next(e for e,_ in reversed(events[:index]) if e['kind']=='audit_finished' and e['state']=='audited')
            if request['evidence_sha256']!=audited['payload']['evidence_sha256']:
                raise ValueError('rerun review evidence differs from applied correction')
            latest = dict(rerun_status='interrupted_or_running',rerun_attempt=event['payload']['attempt'])
            if (attempt/'started-run.json').exists():
                latest['new_run_id'] = started_identity(attempt,HistoryStore(request['history_store']))[0]
        elif event['kind']=='rerun_finished':
            fields(event['payload'],'attempt receipt_sha256')
            started = events[index-1][0]
            if started['kind']!='rerun_started' or started['payload']['attempt']!=event['payload']['attempt']:
                raise ValueError('rerun completion has no exact controller start')
            attempt = contained(case,event['payload']['attempt']); receipt,receipt_hash = record(attempt/'receipt.json')
            fields(receipt,'schema_version status prior_run_id prior_state new_run_id new_event_sha256 new_evidence_sha256 new_plan_sha256 fixture_mode_evidence harness_mode_evidence historical_state_reproduction independence automatic_promotion publication_allowed')
            if (type(receipt['schema_version']) is not int or receipt['schema_version']!=1
                    or receipt_hash!=event['payload']['receipt_sha256'] or receipt['status'] not in ('completed','incomplete','budget_exhausted','interrupted')
                    or receipt['automatic_promotion'] is not False or receipt['publication_allowed'] is not False
                    or receipt['independence']!='unverified' or receipt['historical_state_reproduction']!='unverified'
                    or receipt['fixture_mode_evidence'] not in ('pinned','unknown') or receipt['harness_mode_evidence']!='unknown'):
                raise ValueError('rerun receipt binding changed')
            request,_ = record(attempt/'request.json',64*1024)
            if receipt['prior_run_id']!=request['prior']['run_id']: raise ValueError('rerun prior subject changed')
            prior = prior_binding(HistoryStore(request['history_store']),request,historical=True)
            if receipt['prior_state']!=prior['state'] or receipt['fixture_mode_evidence']!=prior['fixture_mode_evidence']:
                raise ValueError('rerun failure/success claims differ from frozen prior evidence')
            for key in ('new_run_id','new_event_sha256','new_evidence_sha256','new_plan_sha256'):
                if receipt[key] is not None: sha256(receipt[key])
            if receipt['new_run_id'] is None and any(receipt[k] is not None for k in ('new_event_sha256','new_evidence_sha256')):
                raise ValueError('rerun event evidence has no generated run subject')
            if receipt['new_evidence_sha256'] is not None and receipt['new_event_sha256'] is None:
                raise ValueError('rerun evidence has no bound controller event')
            if receipt['new_run_id'] is not None:
                generated,captured = started_identity(attempt,HistoryStore(request['history_store']))
                if (generated!=receipt['new_run_id']
                        or captured['plan_sha256']!=receipt['new_plan_sha256']):
                    raise ValueError('rerun receipt names another controller execution')
                if receipt['new_event_sha256'] is not None:
                    observed = HistoryStore(request['history_store'])._historical_plan(generated,event_sha256=receipt['new_event_sha256'])
                    if observed['evidence_sha256']!=receipt['new_evidence_sha256']:
                        raise ValueError('rerun receipt event/evidence changed')
            if receipt['status']=='completed':
                actual = HistoryStore(request['history_store'])._historical_plan(receipt['new_run_id'])
                old = ExperimentSpec.parse(prior['plan']); derived = replace(old,grader_hash=actual['plan']['grader_hash'])
                source = actual['manifest']; authority = source.get('bank_authority',{})
                correction = record(case/'spec.json')[0]
                declared_inputs(prior,source,correction['parent']['task_id'])
                if (actual['state']!='sealed' or actual['run_id']==receipt['prior_run_id']
                        or actual['event_sha256']!=receipt['new_event_sha256'] or actual['evidence_sha256']!=receipt['new_evidence_sha256']
                        or actual['plan_sha256']!=receipt['new_plan_sha256']
                        or actual['manifest_sha256']!=record(attempt/'export/manifest.json',1024*1024)[1]
                        or derived.digest!=actual['plan_sha256'] or derived.grader_hash==old.grader_hash
                        or actual['fixtures']!=prior['fixtures']
                        or authority.get('bank_sha256')!=request['bank_sha256'] or authority.get('revision_sha256')!=request['revision_sha256']
                        or authority.get('bank_path')!=str(contained(store.root,'revisions/'+request['revision_sha256']+'/bank.json'))
                        or source['grader_bundles'][correction['parent']['task_id']]['digest']!=correction['replacement']['sha256']
                        or receipt['prior_state']!=prior['state'] or receipt['fixture_mode_evidence']!=prior['fixture_mode_evidence']
                        or receipt['harness_mode_evidence']!='unknown'
                        or record(attempt/'started-run.json')[0]!=dict(run_id=actual['run_id'])):
                    raise ValueError('rerun lacks fresh complete sealed execution')
                if prior['bank_tasks'] is not None and any(actual['bank_tasks'][name]['materialized_sha256']!=value['materialized_sha256'] for name,value in prior['bank_tasks'].items()):
                    raise ValueError('rerun mode evidence differs from pinned earlier fixtures')
            latest = dict(rerun_status=receipt['status'],rerun_attempt=event['payload']['attempt'],
                new_run_id=receipt['new_run_id'],new_plan_sha256=receipt['new_plan_sha256'],
                historical_state_reproduction='unverified',harness_mode_evidence='unknown',
                fixture_mode_evidence=receipt['fixture_mode_evidence'])
    return latest or {}


def rerun(store,case_id,request_path,baseline,candidate,*,cgroup_root,image_root,image_sha256,disk_bytes,budget_seconds=60):
    from agent_harness_lab.cli import evaluator
    started = time.monotonic()
    if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
        raise ValueError('rerun budget must be positive and bounded')
    deadline = started+budget_seconds
    request,request_hash = record(request_path,64*1024); request_value(request,case_id)
    raw = read_regular(request_path,64*1024)
    if hashlib.sha256(raw).hexdigest()!=request_hash: raise ValueError('rerun request changed')
    baseline,candidate,image_root = map(canonical,(baseline,candidate,image_root)); sha256(image_sha256)
    history = HistoryStore(request['history_store'])
    if (cgroup_root is None or type(disk_bytes) is not int or disk_bytes<=0
            or store.root==history.root or store.root.is_relative_to(history.root) or history.root.is_relative_to(store.root)
            or any(root.is_relative_to(p) or p.is_relative_to(root) for root in (store.root,history.root) for p in (baseline,candidate,image_root))):
        raise ValueError('rerun needs complete pinned profiles and disjoint private views')
    with store.locked():
        case,_,events,spec,task = store.opened(case_id)
        if events[-1][0]['state']!='applied' or events[-1][1]!=request['expected_event_sha256']:
            raise ValueError('rerun requires current applied correction CAS')
        applied = next(e for e,_ in events if e['kind']=='applied')
        metadata = committed_application(store,case_id,applied['payload'])
        if (not metadata['current_eligible'] or metadata['revision_sha256']!=request['revision_sha256']
                or metadata['bank_manifest_sha256']!=request['bank_sha256']
                or store.checked_evidence(case,spec,task,events)[0]!=request['evidence_sha256']
                or metadata['descriptor']['negative']['history_store']!=str(history.root)):
            raise ValueError('rerun authority differs from applied correction')
        with history.locked(): prior = prior_binding(history,request)
        old = ExperimentSpec.parse(prior['plan'])
        if old.cohort_manifest_sha256 is not None:
            raise ValueError('corrected preservation plans require fresh qualification and a newly frozen cohort manifest')
        if old.profile!='descriptive_smoke' or old.partition!='development': raise ValueError('correction reruns cannot upgrade protected/certification exposure')
        if image_sha256!=old.runtime_hash or image_sha256!=spec['image_sha256']:
            raise ValueError('rerun runtime differs from observed prior/audited image')
        runner = evaluator()
        if runner.digest(baseline)!=old.baseline_hash or runner.digest(candidate)!=old.candidate_hash:
            raise ValueError('earlier harness bytes missing or changed')
        selected = [t[0] for t in old.tasks]
        if task.task_id not in selected or prior['manifest']['grader_bundles'][task.task_id]['digest']!=task.grader_digest:
            raise ValueError('prior run did not select this exact old oracle')
        if len(events)+3>MAX_EVENTS: raise ValueError('rerun journal capacity exhausted')
        relative = 'reruns/'+secrets.token_hex(12); attempt = contained(case,relative); attempt.mkdir(parents=True,mode=0o700)
        write_bytes(attempt/'request.json',raw); sync_directory(attempt); sync_directory(attempt.parent); sync_directory(case)
        store.append(case,events,'rerun_started','applied',dict(attempt=relative,request_sha256=request_hash))
        receipt = dict(schema_version=1,status='incomplete',prior_run_id=prior['run_id'],prior_state=prior['state'],
            new_run_id=None,new_event_sha256=None,new_evidence_sha256=None,new_plan_sha256=None,
            fixture_mode_evidence=prior['fixture_mode_evidence'],harness_mode_evidence='unknown',
            historical_state_reproduction='unverified',independence='unverified',automatic_promotion=False,publication_allowed=False)
        interrupted = False
        try:
            bank = contained(store.root,'revisions/'+metadata['revision_sha256']+'/bank.json')
            manifest = export_manifest(bank,attempt/'export',max_trials=old.max_trials,task_ids=selected)
            previous = {t['id']:t for t in prior['manifest']['tasks']}
            for current in manifest['tasks']:
                name = current['id']; original = previous[name]
                if any(current[k]!=original.get(k) for k in ('id','root','family','split','prompt','artifacts')) or current['split']!='development':
                    raise ValueError('rerun task/policy differs from earlier declaration')
                fixture = contained(attempt/'export',current['fixture'])
                if runner.digest(fixture)!=prior['fixtures'][name]: raise ValueError('earlier materialized fixture bytes unavailable')
                if prior['bank_tasks'] is not None and digest(fixture)!=prior['bank_tasks'][name]['materialized_sha256']:
                    raise ValueError('earlier materialized fixture modes changed')
                if name!=task.task_id and manifest['grader_bundles'][name]['digest']!=prior['manifest']['grader_bundles'][name]['digest']:
                    raise ValueError('rerun changed an unrelated oracle')
                current['grader'][0] = grading_selector(original['grader'][0])
                current['offline_executor'] = original['offline_executor']
            for key in ('max_trials','budget_seconds','timeout_seconds'):
                if key in prior['manifest']: manifest[key] = prior['manifest'][key]
                else: manifest.pop(key,None)
            declared_inputs(prior,manifest,task.task_id)
            new = replace(old,grader_hash=identity(attempt/'export',manifest['tasks'],manifest['grader_bundles']))
            if new.grader_hash==old.grader_hash: raise ValueError('rerun requires a newly pinned corrected oracle')
            manifest['experiment'] = new.to_dict(); write_path = attempt/'export/manifest.json'
            write_path.write_bytes(encoded(manifest)); sync_tree(attempt)
            remaining = deadline-time.monotonic()
            if remaining<=0: raise TimeoutError('rerun preparation exhausted parent deadline')
            receipt['new_plan_sha256'] = new.digest
            def remember(run_id):
                sha256(run_id)
                write_bytes(attempt/'started-run.json',encoded(dict(run_id=run_id))); sync_directory(attempt)
                receipt['new_run_id'] = run_id
            actual = history.run(write_path,baseline,candidate,cgroup_root=cgroup_root,image_root=image_root,
                image_sha256=image_sha256,disk_bytes=disk_bytes,budget_seconds=min(300,remaining),parent_deadline=deadline,on_started=remember)
            if actual['run_id']!=receipt['new_run_id']: raise ValueError('history result differs from actual generated run identity')
            with history.locked(): observed = history._historical_plan(actual['run_id'])
            receipt.update(new_event_sha256=observed['event_sha256'],new_evidence_sha256=observed['evidence_sha256'])
            if (observed['state']!='sealed' or observed['plan_sha256']!=new.digest
                    or observed['manifest_sha256']!=record(write_path,1024*1024)[1]):
                raise ValueError('fresh complete controller comparison unavailable')
            committed_application(store,case_id,applied['payload'])
            if time.monotonic()>=deadline: raise TimeoutError('rerun sealing exceeded parent deadline')
            receipt.update(status='completed',new_event_sha256=observed['event_sha256'],new_evidence_sha256=observed['evidence_sha256'])
        except KeyboardInterrupt:
            interrupted = True; receipt['status']='interrupted'
        except TimeoutError: receipt['status']='budget_exhausted'
        except (ValueError,OSError,TypeError,KeyError,StopIteration): receipt['status']='incomplete'
        if receipt['new_run_id'] is None and (attempt/'started-run.json').exists():
            receipt['new_run_id'] = started_identity(attempt,history)[0]
        write_bytes(attempt/'receipt.json',encoded(receipt)); sync_directory(attempt)
        if receipt['status']=='completed' and time.monotonic()>=deadline:
            receipt['status']='budget_exhausted'; (attempt/'receipt.json').unlink()
            write_bytes(attempt/'receipt.json',encoded(receipt)); sync_directory(attempt)
        store.append(case,events,'rerun_finished','applied',dict(attempt=relative,receipt_sha256=record(attempt/'receipt.json')[1]))
        if interrupted: raise KeyboardInterrupt
        return store.summary(case_id,*store.opened(case_id))
