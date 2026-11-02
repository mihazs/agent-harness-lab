"""Private controller comparisons and monotone oracle quarantine; never oracle approval."""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from types import SimpleNamespace

from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory, write_bytes
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .oracle import capture, identity
from .plan import ExperimentSpec, schedule
from .regression_controls import containment
from .regressions import fields, references, sha256, text
from .statistics import TrialRecord, analyze
from . import behavior,cohort_history

MAX_CASES = 256
MAX_BYTES = 64*1024*1024


def transition(state, kind, target, previous_kind=None):
    if state is None:
        return (kind,target) in (('oracle_registered','active'),('run_started','running'))
    if state == 'active': return kind == 'oracle_quarantined' and target == 'quarantined'
    return state == 'running' and kind == 'run_finished' and target in ('sealed','incomplete')


def oracle_case(bundle):
    sha256(bundle)
    return hashlib.sha256(('oracle:'+bundle).encode()).hexdigest()


def tree_hash(path):
    return hashlib.sha256(encoded(tree(path,max_files=4096,max_bytes=MAX_BYTES))).hexdigest()


def behavior_policies(folders, source, spec):
    bindings = {name:dict(bundle_sha256=value['digest']) for name,value in source['grader_bundles'].items()}
    policies = behavior.capture(folders,bindings)
    if policies and (spec.profile!='descriptive_smoke' or spec.partition!='development'):
        raise ValueError('public development behavior policies cannot qualify a protected experiment')
    tasks = {name:(root,family) for name,root,family in spec.tasks}
    splits = {task['id']:task.get('split') for task in source['tasks']}
    for name,binding in policies.items():
        policy = binding['policy']
        if name not in tasks or (policy['root_id'],policy['family'])!=tasks[name]:
            raise ValueError('behavior policy differs from frozen task lineage')
        if splits.get(name)!='development':
            raise ValueError('public development behavior policies cannot become held-out tasks')
    return policies


def bank_authority(source, *, export_root=None, expected=None):
    """Validate actual current bank authority and selected materialized inputs."""
    from agent_harness_lab.cli import evaluator
    from .bank_tools import materialize_sources
    from .bank_revisions import task_identity
    from .task_bank import validate
    authority = source.get('bank_authority')
    if authority is None:
        if 'bank_revision_sha256' in source or expected is not None:
            raise ValueError('controlled history requires its explicit bank authority')
        return None
    fields(authority,'schema_version bank_path bank_sha256 revision_sha256')
    if type(authority['schema_version']) is not int or authority['schema_version']!=1:
        raise ValueError('unsupported history bank authority')
    for key in ('bank_sha256','revision_sha256'): sha256(authority[key])
    location = authority['bank_path']
    if not isinstance(location,str) or not 0<len(location)<=4096 or '\0' in location:
        raise ValueError('bounded explicit bank authority path required')
    path = Path(location)
    if (not path.is_absolute() or '..' in path.parts or str(path)!=location
            or any(p.is_symlink() for p in (path,*path.parents))):
        raise ValueError('bank authority path must be canonical and symlink-free')
    checked = validate(path); admission = checked.get('admission')
    experiment = source.get('experiment',{})
    if not isinstance(experiment,dict): raise ValueError('bank authority experiment must be an object')
    if experiment.get('profile','descriptive_smoke')=='certification' and not checked['certification_eligible']:
        raise ValueError('bank authority is not eligible for certification')
    bank,bank_hash = record(path,1024*1024)
    if (bank_hash!=authority['bank_sha256'] or checked['bank_sha256']!=bank_hash or source.get('bank_digest')!=bank_hash
            or not isinstance(admission,dict) or admission.get('current_eligible') is not True
            or admission.get('revision_sha256')!=authority['revision_sha256']
            or source.get('bank_revision_sha256')!=authority['revision_sha256']):
        raise ValueError('bank authority is stale, ineligible or mismatched')
    tasks = source.get('tasks')
    if not isinstance(tasks,list) or not 0<len(tasks)<=1000:
        raise ValueError('bounded selected bank tasks required')
    indexed = {t['task_id']:t for t in bank['tasks']}; bindings = {}; files_total = bytes_total = 0
    runtime_digest = evaluator().digest
    for task in tasks:
        name = task['id']; original = indexed.get(name)
        if original is None or name in bindings: raise ValueError('unknown or duplicate selected bank task')
        split = {'calibration':'regression','certification':'heldout','transfer':'heldout'}.get(original['partition'],'development')
        if (task.get('root')!=original['root_id'] or task.get('family')!=original['family']
                or task.get('split')!=split or task.get('artifacts')!=original['artifacts']
                or source.get('grader_bundles',{}).get(name,{}).get('digest')!=original['grader_digest']):
            raise ValueError('selected task differs from authoritative bank policy')
        fixture = contained(path.parent,original['fixture']); entries = tree(fixture)
        files_total += len(entries)
        bytes_total += sum(len(read_regular(contained(fixture,p),MAX_BYTES)) for p in entries)
        if files_total>4096 or bytes_total>MAX_BYTES: raise ValueError('bank selection bound exceeded')
        with tempfile.TemporaryDirectory() as temporary:
            prepared = Path(temporary)/'fixture'; copy_frozen(fixture,prepared,original['fixture_digest'])
            materialize_sources(prepared)
            declaration,_ = record(prepared/'task.json',1024*1024)
            if task.get('prompt')!=declaration.get('prompt'): raise ValueError('bank task prompt changed')
            materialized = digest(prepared)
            bindings[name] = dict(task_revision_sha256=task_identity(original),
                materialized_sha256=materialized,execution_fixture_sha256=runtime_digest(prepared),
                grader_version=original['grader_version'])
        if export_root is not None and digest(contained(export_root,task['fixture']))!=materialized:
            raise ValueError('exported fixture differs from original materialized bank fixture')
    if expected is not None and bindings!=expected: raise ValueError('sealed bank task authority changed')
    current = validate(path)
    if current!=checked: raise ValueError('bank authority changed during validation')
    return bindings


class HistoryStore(PrivateJournal):
    def __init__(self, root):
        super().__init__(root,'experiment-oracle-history-v1',transition)

    def new_case(self, case_id):
        cases = self.root/'cases'; cases.mkdir(mode=0o700,exist_ok=True)
        count = 0
        with os.scandir(cases) as entries:
            for index, entry in enumerate(entries):
                if index >= MAX_CASES: raise ValueError('history case capacity exhausted')
                sha256(entry.name)
                if not entry.is_dir(follow_symlinks=False): raise ValueError('invalid history case directory')
                count += 1
        if count >= MAX_CASES: raise ValueError('history case capacity exhausted')
        case = self.case(case_id); case.mkdir(mode=0o700)
        (case/'events').mkdir(mode=0o700)
        return case

    def oracle_events(self, bundle):
        events = self.history(self.case(oracle_case(bundle)))
        if events[0][0]['payload'] != dict(bundle_sha256=bundle) or events[0][0]['kind'] != 'oracle_registered':
            raise ValueError('oracle registration binding changed')
        for event,_ in events[1:]:
            value = event['payload'].get('record')
            self.quarantine_value(value)
            if value['bundle_sha256'] != bundle or value['expected_event_sha256'] != event['previous_sha256']:
                raise ValueError('oracle quarantine binding changed')
        return events

    def oracle(self, bundle):
        with self.locked():
            events = self.oracle_events(bundle)
            return dict(bundle_sha256=bundle,state=events[-1][0]['state'],event_sha256=events[-1][1])

    @staticmethod
    def quarantine_value(value):
        fields(value,'schema_version bundle_sha256 expected_event_sha256 operator evidence_refs reason')
        if type(value['schema_version']) is not int or value['schema_version'] != 1: raise ValueError('unsupported quarantine schema')
        sha256(value['bundle_sha256']); sha256(value['expected_event_sha256']); text(value['reason'])
        fields(value['operator'],'identity source provenance basis')
        for key in ('identity','provenance','basis'): text(value['operator'][key])
        if len(value['operator']['identity']) > 128 or value['operator']['source'] != 'operator_attestation':
            raise ValueError('explicit operator quarantine provenance required')
        references(value['evidence_refs'])
        if not value['evidence_refs']: raise ValueError('quarantine needs explicit evidence references')

    def quarantine(self, path):
        value,_ = record(path,64*1024); self.quarantine_value(value)
        bundle = value['bundle_sha256']
        with self.locked():
            events = self.oracle_events(bundle)
            if events[-1][1] != value['expected_event_sha256']: raise ValueError('stale oracle quarantine decision')
            self.append(self.case(oracle_case(bundle)),events,'oracle_quarantined','quarantined',dict(record=value))
            return dict(bundle_sha256=bundle,state='quarantined',event_sha256=events[-1][1],
                historical_comparisons_invalidated=True,corrected_oracle_approved=False,publication_allowed=False)

    def opened(self, run_id):
        return self._opened(run_id,current_bank=True)

    def _opened(self, run_id, *, current_bank):
        case = self.case(run_id); events = self.history(case)
        inputs, inputs_hash = record(case/'inputs.json')
        version = inputs.get('schema_version')
        fields(inputs,'schema_version run_id source_manifest_sha256 plan_sha256 oracle_sha256 dependencies versions image_sha256'
            + (' bank_authority bank_tasks' if type(version) is int and version in (2,3,4) else '')
            + (' behavior_policies' if type(version) is int and version in (3,4) else '')
            + (' cohort_binding_sha256' if type(version) is int and version==4 else '')
            + (' protected_access_sha256' if 'protected_access_sha256' in inputs else ''))
        if (type(version) is not int or version not in (1,2,3,4) or inputs['run_id'] != run_id
                or events[0][0]['kind'] != 'run_started' or events[0][0]['payload'] != dict(inputs_sha256=inputs_hash)):
            raise ValueError('controller run input binding changed')
        source, manifest_hash = record(case/'manifest.json',1024*1024)
        spec = ExperimentSpec.parse(source['experiment']); spec.validate()
        if manifest_hash != inputs['source_manifest_sha256'] or spec.digest != inputs['plan_sha256'] or spec.grader_hash != inputs['oracle_sha256']:
            raise ValueError('stored experiment declaration changed')
        if 'protected_access_sha256' in inputs:
            receipt, receipt_hash = record(case/'protected-access.json',1024*1024)
            if receipt_hash != inputs['protected_access_sha256'] or receipt.get('run_id') != run_id:
                raise ValueError('managed access frozen binding changed')
            self.access_ledger().validate_managed_history(receipt)
            binding = receipt['binding']
            if (binding['plan_sha256'] != spec.digest or binding['manifest_sha256'] != manifest_hash
                    or binding['oracle_sha256'] != spec.grader_hash or binding['runtime_sha256'] != spec.runtime_hash):
                raise ValueError('managed access differs from historical comparison')
        if version==2 or inputs.get('bank_authority') is not None:
            if not isinstance(inputs['bank_authority'],dict) or inputs['bank_authority']!=source.get('bank_authority'):
                raise ValueError('stored bank authority binding changed')
            bound = inputs['bank_tasks']
            if not isinstance(bound,dict) or not 0<len(bound)<=1000:
                raise ValueError('bounded nonempty bank task authority required')
            for name,value in bound.items():
                if not isinstance(name,str) or not 0<len(name)<=128:
                    raise ValueError('bounded bank task identity required')
                fields(value,'task_revision_sha256 materialized_sha256 execution_fixture_sha256 grader_version')
                for key in ('task_revision_sha256','materialized_sha256','execution_fixture_sha256'): sha256(value[key])
                text(value['grader_version'])
            if current_bank: bank_authority(source,expected=inputs['bank_tasks'])
        elif source.get('bank_authority') is not None:
            raise ValueError('bound bank history cannot use the unbound input schema')
        elif version in (3,4) and inputs['bank_tasks'] is not None:
            raise ValueError('unbound behavior history cannot invent bank task authority')
        if not isinstance(inputs['dependencies'],dict) or not inputs['dependencies']:
            raise ValueError('controller run has no oracle dependencies')
        if set(inputs['dependencies']) != {v['digest'] for v in source['grader_bundles'].values()}:
            raise ValueError('controller oracle dependency index changed')
        for bundle, event_hash in inputs['dependencies'].items():
            sha256(event_hash)
            if self.oracle_events(bundle)[0][1] != event_hash: raise ValueError('oracle dependency registration changed')
            if digest(contained(case,'oracles/'+bundle)) != bundle: raise ValueError('frozen historical oracle changed')
        if version in (3,4):
            policies = behavior_policies({bundle:contained(case,'oracles/'+bundle) for bundle in inputs['dependencies']},source,spec)
            if (not policies and version==3) or policies!=inputs['behavior_policies']:
                raise ValueError('frozen behavior policy binding changed')
        if version==4:
            binding,binding_hash=record(case/'cohort-binding.json',1024*1024)
            if (binding_hash!=inputs['cohort_binding_sha256']
                    or binding.get('manifest_path')!=str(case/'cohort-manifest.json')):
                raise ValueError('frozen preservation qualification binding changed')
            cohort_history.validate(binding,source,spec,inputs['bank_tasks'],current=current_bank)
        elif spec.cohort_manifest_sha256 is not None:
            raise ValueError('preservation plans require explicit controller qualification binding')
        return case,events,inputs,spec

    def checked(self, run_id):
        case,events,inputs,spec = self.opened(run_id)
        validity = 'invalidated' if any(self.oracle_events(b)[-1][0]['state'] != 'active' for b in inputs['dependencies']) else 'current'
        if events[-1][0]['state'] != 'sealed': return case,events,inputs,spec,None,'incomplete'
        result = self._sealed(case,events,inputs,spec)
        return case,events,inputs,spec,result,validity

    def _sealed(self,case,events,inputs,spec):
        evidence, evidence_hash = record(case/'evidence.json')
        fields(evidence,'results_sha256 execution_tree_sha256')
        if events[-1][0]['payload'] != dict(evidence_sha256=evidence_hash): raise ValueError('controller evidence seal changed')
        result,result_hash = record(case/'execution/results.json',8*1024*1024)
        if result_hash != evidence['results_sha256'] or tree_hash(case/'execution') != evidence['execution_tree_sha256']:
            raise ValueError('sealed controller execution changed')
        self.trials(inputs,spec,result)
        return result

    def historical_plan(self,run_id):
        """Read frozen prior declarations/evidence, without approving old bank applicability."""
        with self.locked(): return self._historical_plan(run_id)

    def _historical_plan(self,run_id,*,event_sha256=None):
        case,events,inputs,spec = self._opened(run_id,current_bank=False)
        if event_sha256 is not None:
            sha256(event_sha256)
            index = next((i for i,(_,value) in enumerate(events) if value==event_sha256),None)
            if index is None: raise ValueError('historical controller event is unavailable')
            events = events[:index+1]
        source,_ = record(case/'manifest.json',1024*1024)
        evidence_hash = None
        if events[-1][0]['state']=='sealed':
            result = self._sealed(case,events,inputs,spec)
            fixtures = result.get('fixtures')
            evidence_hash = record(case/'evidence.json')[1]
        elif inputs.get('bank_tasks') is not None:
            fixtures = {k:v['execution_fixture_sha256'] for k,v in inputs['bank_tasks'].items()}
        else: raise ValueError('unsealed legacy history lacks authoritative earlier fixture pins')
        if not isinstance(fixtures,dict) or set(fixtures)!={t[0] for t in spec.tasks}:
            raise ValueError('earlier fixture identities unavailable')
        for value in fixtures.values(): sha256(value)
        return dict(run_id=run_id,event_sha256=events[-1][1],inputs_sha256=record(case/'inputs.json')[1],
            evidence_sha256=evidence_hash,plan=spec.to_dict(),plan_sha256=spec.digest,manifest=source,
            manifest_sha256=inputs['source_manifest_sha256'],fixtures=fixtures,bank_tasks=inputs.get('bank_tasks'),
            state=events[-1][0]['state'],historical_outcome='observed' if evidence_hash else 'unknown',
            fixture_mode_evidence='pinned' if inputs.get('bank_tasks') is not None else 'unknown',
            current_applicability='not_evaluated',independence='unverified',publication_allowed=False)

    @staticmethod
    def trials(inputs,spec,result):
        from agent_harness_lab.cli import evaluator
        grade_status = evaluator().grading_status
        if not isinstance(result.get('runtime'),dict): raise ValueError('missing observed runtime')
        if spec.cohort_manifest_sha256 is not None and (inputs['schema_version']!=4
                or result.get('cohort_manifest_sha256')!=spec.cohort_manifest_sha256
                or result.get('cohort_qualification')!='current_membership_only'
                or result.get('publication_allowed') is not False):
            raise ValueError('executed preservation preflight differs from frozen controller inputs')
        if inputs.get('bank_tasks') is not None and result.get('fixtures')!={
                name:value['execution_fixture_sha256'] for name,value in inputs['bank_tasks'].items()}:
            raise ValueError('executed fixtures differ from authoritative materialized bank inputs')
        if (result.get('inputs_unchanged') is not True or result.get('manifest_sha256') != inputs['source_manifest_sha256']
                or result.get('plan_digest') != spec.digest or result.get('grader_hash') != spec.grader_hash
                or result.get('versions') != inputs['versions'] or result.get('runtime',{}).get('agent') != 'offline'
                or result.get('runtime',{}).get('model_inference') is not False):
            raise ValueError('controller comparison provenance is incomplete')
        expected = {t['trial_id']:t for t in schedule(spec)}
        trials = result.get('trials')
        if (not isinstance(trials,list) or len(trials) != len(expected)
                or any(not isinstance(t,dict) for t in trials)
                or {t.get('trial_id') for t in trials} != set(expected)):
            raise ValueError('both complete scheduled arms must be executed afresh')
        records = []
        for trial in trials:
            if any(trial.get(k) != v for k,v in expected[trial['trial_id']].items()):
                raise ValueError('observed trial differs from frozen schedule')
            grade = trial.get('grader',{}); execution = trial.get('execution',{})
            if not isinstance(grade,dict) or not isinstance(execution,dict):
                raise ValueError('comparison needs executable grade records')
            outcome = grade.get('outcome')
            graded = grade.get('execution')
            if not isinstance(graded,dict): raise ValueError('missing actual grader execution')
            typed = dict(graded,timeout=graded.get('status')=='timeout')
            if (type(outcome) is not bool or trial.get('status') != ('passed' if outcome else 'failed')
                    or grade.get('status') != trial['status'] or execution.get('status') != 'completed'
                    or type(execution.get('exit_code')) is not int or execution['exit_code'] != 0
                    or execution.get('inputs_unchanged') is not True
                    or not containment(execution,inputs['image_sha256'],'executor')
                    or graded.get('status') != ('completed' if outcome else 'failed')
                    or graded.get('inputs_unchanged') is not True
                    or grade_status(typed,typed=True) != trial['status']
                    or grade.get('grader_identity') != {k:typed.get(k) for k in ('grader_id','grader_version')}
                    or not containment(graded,inputs['image_sha256'])):
                raise ValueError('comparison needs actual isolated executions and typed grades')
            if (inputs.get('bank_tasks') is not None
                    and grade['grader_identity']['grader_version']!=inputs['bank_tasks'][trial['task']]['grader_version']):
                raise ValueError('observed grader version differs from authoritative bank task')
            policy = inputs.get('behavior_policies',{}).get(trial['task'])
            observed = behavior.observation(policy,graded) if policy is not None else None
            if spec.cohort_manifest_sha256 is not None and observed is not None and not observed['complete']:
                outcome=None
            records.append(TrialRecord(trial['trial_id'],outcome,'graded','executable_grader',
                spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash,
                critical_regression=bool(trial['version']=='candidate' and observed and observed['critical'])))
        return records

    def summary(self, run_id, checked):
        _,events,inputs,_,_,validity = checked
        result = dict(schema_version=1,run_id=run_id,state=events[-1][0]['state'],event_sha256=events[-1][1],
            oracle_validity=validity,dependencies=sorted(inputs['dependencies']),
            controller_observed=events[-1][0]['state']=='sealed',oracle_review='unverified',
            independence='unverified',automatic_promotion=False,publication_allowed=False)
        if 'protected_access_sha256' in inputs:
            from .certification_access import view
            result['protected_access']=view(record(checked[0]/'protected-access.json',1024*1024)[0])
        if inputs.get('bank_tasks') is not None: result['bank_authority']='current_eligible'
        if inputs['schema_version']==4:
            result['cohort_manifest_sha256']=checked[3].cohort_manifest_sha256
            result['cohort_qualification']='current_membership_only'
            result['cohort_frozen_at']=events[0][0]['recorded_at']
        return result

    def show(self, run_id):
        with self.locked(): return self.summary(run_id,self.checked(run_id))

    def analyze(self, run_id):
        with self.locked(): return self._analyze_checked(self.checked(run_id))

    def _analyze_checked(self, checked):
        case,_,inputs,spec,result,validity = checked
        run_id=inputs['run_id']
        membership=None
        if spec.cohort_manifest_sha256 is not None:
            membership,membership_hash=record(case/'cohort-binding.json',1024*1024)
            if membership_hash!=inputs['cohort_binding_sha256']:
                raise ValueError('preservation analysis binding changed after validation')
            membership['manifest_json']=read_regular(case/'cohort-manifest.json',1024*1024)
        effects = behavior.report(spec,result['trials'],inputs['behavior_policies']) if result is not None and inputs.get('behavior_policies') else None
        if validity != 'current':
            value = dict(status='blocked' if validity=='invalidated' else 'incomplete',reason='oracle_quarantined' if validity=='invalidated' else 'controller_run_not_sealed')
        else: value = analyze(spec,self.trials(inputs,spec,result),cohort_binding=membership)
        retained=dict(preservation=value['preservation']) if 'preservation' in value else {}
        if validity=='current' and effects is not None:
            if effects['critical_candidate_effect']:
                value = dict(status='rejected',reason='critical_regression',**retained)
            elif effects['required_observation_unknown']:
                value = dict(status='blocked',reason='behavior_observation_incomplete',**retained)
        result = dict(value,oracle_validity=validity,controller_run_id=run_id,oracle_review='unverified',
            independence='unverified',automatic_promotion=False,publication_allowed=False)
        if 'protected_access_sha256' in inputs:
            from .certification_access import view
            result['protected_access']=view(record(case/'protected-access.json',1024*1024)[0])
        if inputs.get('bank_tasks') is not None: result['bank_authority']='current_eligible'
        if effects is not None: result['behavior']=effects
        if inputs['schema_version']==4:
            result['cohort_manifest_sha256']=spec.cohort_manifest_sha256
            result['cohort_qualification']='current_membership_only'
        return result

    def decision(self, run_id):
        """Persist private recomputed analysis evidence, never adoption authority."""
        from .decision_receipts import create
        with self.locked(): return create(self,run_id)

    def verify_decision(self, run_id, receipt_sha256):
        """Reopen live authority and recompute; a supplied JSON label is not proof."""
        from .decision_receipts import verify
        with self.locked(): return verify(self,run_id,receipt_sha256)

    def access_ledger(self):
        from .task_bank import CertificationLedger
        return CertificationLedger(self.root/'protected-access.json')

    def reserve_access(self, manifest, baseline, candidate, *, budget_seconds=60,parent_deadline=None):
        from .certification_access import capture
        if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
            raise ValueError('access reservation budget must be positive and at most 300 seconds')
        deadline=time.monotonic()+budget_seconds
        if parent_deadline is not None:
            if type(parent_deadline) not in (int,float) or not math.isfinite(parent_deadline): raise ValueError('finite access deadline required')
            deadline=min(deadline,parent_deadline)
        def check():
            if time.monotonic()>=deadline: raise TimeoutError('access reservation budget exhausted')
        paths = [Path(p).absolute() for p in (manifest,baseline,candidate)]
        if any(self.root.is_relative_to(p if p.is_dir() else p.parent)
                or (p if p.is_dir() else p.parent).is_relative_to(self.root) for p in paths):
            raise ValueError('access ledger must remain outside controller input views')
        binding = capture(manifest,baseline,candidate,check)
        declared,_ = record(manifest,1024*1024)
        if ExperimentSpec.parse(declared['experiment']).cohort_manifest_sha256 is not None:
            raise ValueError('managed diagnostics do not qualify preservation cohorts')
        with self.locked(create=True): pass
        check()
        return self.access_ledger().reserve_managed(binding)

    def run(self, manifest, baseline, candidate, *, cgroup_root, image_root, image_sha256, disk_bytes, budget_seconds=60,parent_deadline=None,on_started=None,access_reservation=None):
        from agent_harness_lab.cli import evaluator
        from .bank_tools import frozen_grader
        from .bank_revisions import sync_tree
        started = time.monotonic(); sha256(image_sha256)
        if on_started is not None and not callable(on_started): raise ValueError('history start observer must be callable')
        if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
            raise ValueError('history budget must be positive and at most 300 seconds')
        deadline = started+budget_seconds
        if parent_deadline is not None:
            if type(parent_deadline) not in (int,float) or not math.isfinite(parent_deadline):
                raise ValueError('history parent deadline must be finite')
            deadline = min(deadline,parent_deadline)
        manifest,baseline,candidate,image_root = map(lambda p:Path(p).absolute(),(manifest,baseline,candidate,image_root))
        if any('..' in p.parts or any(q.is_symlink() for q in (p,*p.parents))
                for p in (manifest,baseline,candidate,image_root)):
            raise ValueError('history inputs require canonical symlink-free paths')
        if cgroup_root is None or type(disk_bytes) is not int or disk_bytes <= 0:
            raise ValueError('history requires the full pinned offline resource profile')
        sources = [manifest.parent,baseline,candidate,image_root]
        if any(self.root.is_relative_to(p) or p.is_relative_to(self.root) for p in sources):
            raise ValueError('history store must remain outside worker input views')
        raw = read_regular(manifest,1024*1024); source,manifest_hash = record(manifest,1024*1024)
        if hashlib.sha256(raw).hexdigest() != manifest_hash: raise ValueError('manifest changed during history preflight')
        spec = ExperimentSpec.parse(source['experiment']); spec.validate()
        if spec.profile == 'certification':
            raise ValueError('genuine certification qualification issuer is unavailable; public development remains unqualified')
        if 'protected_access' in source and access_reservation is None:
            raise ValueError('managed access declaration requires its controller reservation')
        bound_tasks = bank_authority(source,export_root=manifest.parent)
        if bound_tasks is not None:
            owner = Path(source['bank_authority']['bank_path']).parents[2]
            if self.root.is_relative_to(owner) or owner.is_relative_to(self.root):
                raise ValueError('history store and bank authority trees must not overlap')
        selected = source['tasks']; captured = capture(manifest.parent,selected,source.get('grader_bundles',{}))
        if identity(manifest.parent,selected,source.get('grader_bundles',{})) != spec.grader_hash or spec.runtime_hash != image_sha256:
            raise ValueError('history plan must bind the current oracle and pinned image')
        if {t[0]:t[1:] for t in spec.tasks} != {t['id']:(t.get('root'),t.get('family')) for t in selected}:
            raise ValueError('history plan and task lineage declarations differ')
        runner = evaluator(); versions = {'baseline':runner.digest(baseline),'candidate':runner.digest(candidate)}
        if (versions['baseline'],versions['candidate']) != (spec.baseline_hash,spec.candidate_hash):
            raise ValueError('history plan harness identities differ')
        folders = {}
        for task in selected:
            declaration = source['grader_bundles'][task['id']]
            folder,_ = frozen_grader(manifest.parent,task,declaration)
            folders[captured['tasks'][task['id']]['bundle_sha256']] = folder
        policies = behavior_policies(folders,source,spec)
        membership=cohort_history.capture(manifest.parent,source,spec,bound_tasks)
        if time.monotonic()>=deadline: raise TimeoutError('history qualification preflight budget exhausted')
        run_id = hashlib.sha256(('run:'+secrets.token_hex(32)).encode()).hexdigest()
        from .certification_access import capture as capture_access, exposure
        binding = None
        def check_access_budget():
            if time.monotonic() >= deadline: raise TimeoutError('access observation budget exhausted')
        if access_reservation is not None:
            binding = capture_access(manifest,baseline,candidate,check_access_budget)
            if spec.cohort_manifest_sha256 is not None:
                raise ValueError('managed diagnostics do not qualify preservation cohorts')
            assets = binding['assets']
        else:
            assets,_ = exposure(manifest.parent,selected,source['grader_bundles'],check_access_budget)
        if time.monotonic() >= deadline: raise TimeoutError('managed access preflight budget exhausted')
        with self.locked(create=True): pass
        access_handle,access_receipt = self.access_ledger().admit_managed(assets,run_id,
            reservation_id=access_reservation,binding=binding)
        with self.locked(create=True):
            dependencies = {}
            for bundle in sorted(folders):
                if not self.case(oracle_case(bundle)).exists():
                    registered = self.new_case(oracle_case(bundle)); events = []
                    self.append(registered,events,'oracle_registered','active',dict(bundle_sha256=bundle))
                events = self.oracle_events(bundle)
                if events[-1][0]['state'] != 'active': raise ValueError('oracle dependency is quarantined; fresh reviewed correction and both-arm plan required')
                dependencies[bundle] = events[0][1]
            case = self.new_case(run_id); (case/'oracles').mkdir(mode=0o700)
            for bundle,folder in folders.items(): copy_frozen(folder,case/'oracles'/bundle,bundle)
            inputs = dict(schema_version=1,run_id=run_id,source_manifest_sha256=manifest_hash,plan_sha256=spec.digest,
                oracle_sha256=spec.grader_hash,dependencies=dependencies,versions=versions,image_sha256=image_sha256)
            if bound_tasks is not None:
                inputs.update(schema_version=2,bank_authority=source['bank_authority'],bank_tasks=bound_tasks)
            if policies:
                inputs.update(schema_version=3,bank_authority=source.get('bank_authority'),bank_tasks=bound_tasks,behavior_policies=policies)
            if membership is not None:
                binding_hash=cohort_history.freeze(case,membership,source,spec,bound_tasks)
                inputs.update(schema_version=4,bank_authority=source['bank_authority'],bank_tasks=bound_tasks,
                    behavior_policies=policies,cohort_binding_sha256=binding_hash)
            if access_receipt is not None:
                write_bytes(case/'protected-access.json',encoded(access_receipt))
                inputs['protected_access_sha256'] = record(case/'protected-access.json',1024*1024)[1]
            if len(encoded(inputs))>256*1024: raise ValueError('history input metadata limit exceeded')
            write_bytes(case/'manifest.json',raw); write_bytes(case/'inputs.json',encoded(inputs)); sync_tree(case)
            events = []; self.append(case,events,'run_started','running',dict(inputs_sha256=record(case/'inputs.json')[1]))
            sync_directory(case.parent); sync_directory(self.root)
        args = SimpleNamespace(manifest=manifest,baseline=baseline,candidate=candidate,output=case/'execution',
            agent='offline',codex='must-not-launch',claude='must-not-launch',cgroup_root=Path(cgroup_root),
            image_root=image_root,image_sha256=image_sha256,disk_bytes=disk_bytes,parent_deadline=deadline)
        if access_handle is not None: args._protected_access = access_handle
        try:
            if on_started is not None:
                try: on_started(run_id)
                except Exception: raise ValueError('history start observation failed') from None
            if time.monotonic()>=deadline: raise TimeoutError('history deadline exhausted before dispatch')
            if bound_tasks is not None: bank_authority(source,export_root=manifest.parent,expected=bound_tasks)
            if membership is not None: cohort_history.validate(membership,source,spec,bound_tasks)
            runner.evaluate(args)
            result,result_hash = record(case/'execution/results.json',8*1024*1024)
            self.trials(inputs,spec,result)
            if bound_tasks is not None: bank_authority(source,export_root=manifest.parent,expected=bound_tasks)
            if membership is not None: cohort_history.validate(membership,source,spec,bound_tasks)
            if (record(manifest,1024*1024)[1] != manifest_hash
                    or identity(manifest.parent,selected,source['grader_bundles']) != spec.grader_hash
                    or time.monotonic() >= deadline):
                raise ValueError('history inputs changed or sealing budget exhausted')
            evidence = dict(results_sha256=result_hash,execution_tree_sha256=tree_hash(case/'execution'))
            sync_tree(case/'execution'); write_bytes(case/'evidence.json',encoded(evidence)); sync_directory(case)
            if time.monotonic() >= deadline: raise TimeoutError('history sealing budget exhausted')
            with self.locked():
                if bound_tasks is not None: bank_authority(source,export_root=manifest.parent,expected=bound_tasks)
                if membership is not None: cohort_history.validate(membership,source,spec,bound_tasks)
                current = self.history(case)
                if time.monotonic()>=deadline: raise TimeoutError('history authority validation exhausted sealing budget')
                self.append(case,current,'run_finished','sealed',dict(evidence_sha256=record(case/'evidence.json')[1]))
        except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError,KeyboardInterrupt) as error:
            with self.locked():
                current = self.history(case)
                self.append(case,current,'run_finished','incomplete',dict(reason='execution_or_evidence_incomplete'))
            if isinstance(error,KeyboardInterrupt): raise
        return self.show(run_id)


def main(argv=None):
    import argparse
    from .cli import pinned_arguments
    parser = argparse.ArgumentParser(prog='ahl experiment-history',description=__doc__)
    parser.add_argument('--store',type=Path,required=True,help='Explicit private controller history')
    actions = parser.add_subparsers(dest='action',required=True)
    run = actions.add_parser('run',help='Execute a fresh full pinned offline comparison; never import results')
    reserve = actions.add_parser('reserve-access',help='Reserve an unqualified one-shot protected diagnostic comparison')
    for command in (run,reserve):
        for name in ('manifest','baseline','candidate'):
            command.add_argument('--'+name,type=Path,required=True)
    reserve.add_argument('--budget-seconds',type=float,default=60)
    status = actions.add_parser('access-status',help='Inspect private reservation retirement without inferring worker access')
    status.add_argument('--reservation-id',required=True)
    run.add_argument('--access-reservation',help='Actual controller-owned protected diagnostic reservation ID')
    pinned_arguments(run)
    for name in ('show','analyze','historical-plan','decision','verify-decision'):
        command = actions.add_parser(name)
        command.add_argument('--run',dest='run_id',required=True)
        if name=='verify-decision': command.add_argument('--receipt-sha256',required=True)
    oracle = actions.add_parser('oracle',help='Read an observed bundle registration and current CAS identity')
    oracle.add_argument('--bundle',required=True)
    quarantine = actions.add_parser('quarantine',help='Revoke a known bundle across all dependent comparisons')
    quarantine.add_argument('--record',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        store = HistoryStore(args.store)
        if args.action == 'run':
            result = store.run(args.manifest,args.baseline,args.candidate,cgroup_root=args.cgroup_root,
                image_root=args.image_root,image_sha256=args.image_sha256,disk_bytes=args.disk_bytes,budget_seconds=args.budget_seconds,
                access_reservation=args.access_reservation)
            code = 0 if result['state']=='sealed' and result['oracle_validity']=='current' else 3 if result['oracle_validity']=='invalidated' else 1
        elif args.action == 'access-status':
            with store.locked(): result=store.access_ledger().managed_status(args.reservation_id)
            code=0
        elif args.action == 'reserve-access': result,code = store.reserve_access(args.manifest,args.baseline,args.candidate,budget_seconds=args.budget_seconds),0
        elif args.action == 'quarantine': result,code = store.quarantine(args.record),0
        elif args.action == 'oracle': result,code = store.oracle(args.bundle),0
        elif args.action == 'analyze': result,code = store.analyze(args.run_id),3
        elif args.action=='decision': result,code = store.decision(args.run_id),3
        elif args.action=='verify-decision': result,code = store.verify_decision(args.run_id,args.receipt_sha256),0
        elif args.action=='historical-plan': result,code = store.historical_plan(args.run_id),0
        else: result,code = store.show(args.run_id),0
        print(json.dumps(result,indent=2,allow_nan=False))
        return code
    except BlockingIOError:
        print(json.dumps(dict(status='busy',automatic_promotion=False,publication_allowed=False)))
        return 3
    except (ValueError,OSError,TypeError,KeyError):
        parser.exit(2,'experiment history error: invalid or unavailable private controller evidence\n')
