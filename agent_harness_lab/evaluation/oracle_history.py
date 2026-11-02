"""Private controller comparisons and monotone oracle quarantine; never oracle approval."""
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import time
from types import SimpleNamespace

from agent_harness_lab.private_journal import PrivateJournal, encoded, record, sync_directory, write_bytes
from .integrity import contained, copy_frozen, digest, read_regular, tree
from .oracle import capture, identity
from .plan import ExperimentSpec, schedule
from .regression_controls import containment
from .regressions import fields, references, sha256, text
from .statistics import TrialRecord, analyze

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
        case = self.case(run_id); events = self.history(case)
        inputs, inputs_hash = record(case/'inputs.json')
        fields(inputs,'schema_version run_id source_manifest_sha256 plan_sha256 oracle_sha256 dependencies versions image_sha256')
        if (type(inputs['schema_version']) is not int or inputs['schema_version'] != 1 or inputs['run_id'] != run_id
                or events[0][0]['kind'] != 'run_started' or events[0][0]['payload'] != dict(inputs_sha256=inputs_hash)):
            raise ValueError('controller run input binding changed')
        source, manifest_hash = record(case/'manifest.json',1024*1024)
        spec = ExperimentSpec.parse(source['experiment']); spec.validate()
        if manifest_hash != inputs['source_manifest_sha256'] or spec.digest != inputs['plan_sha256'] or spec.grader_hash != inputs['oracle_sha256']:
            raise ValueError('stored experiment declaration changed')
        if not isinstance(inputs['dependencies'],dict) or not inputs['dependencies']:
            raise ValueError('controller run has no oracle dependencies')
        if set(inputs['dependencies']) != {v['digest'] for v in source['grader_bundles'].values()}:
            raise ValueError('controller oracle dependency index changed')
        for bundle, event_hash in inputs['dependencies'].items():
            sha256(event_hash)
            if self.oracle_events(bundle)[0][1] != event_hash: raise ValueError('oracle dependency registration changed')
            if digest(contained(case,'oracles/'+bundle)) != bundle: raise ValueError('frozen historical oracle changed')
        return case,events,inputs,spec

    def checked(self, run_id):
        case,events,inputs,spec = self.opened(run_id)
        validity = 'invalidated' if any(self.oracle_events(b)[-1][0]['state'] != 'active' for b in inputs['dependencies']) else 'current'
        if events[-1][0]['state'] != 'sealed': return case,events,inputs,spec,None,'incomplete'
        evidence, evidence_hash = record(case/'evidence.json')
        fields(evidence,'results_sha256 execution_tree_sha256')
        if events[-1][0]['payload'] != dict(evidence_sha256=evidence_hash): raise ValueError('controller evidence seal changed')
        result,result_hash = record(case/'execution/results.json',8*1024*1024)
        if result_hash != evidence['results_sha256'] or tree_hash(case/'execution') != evidence['execution_tree_sha256']:
            raise ValueError('sealed controller execution changed')
        self.trials(inputs,spec,result)
        return case,events,inputs,spec,result,validity

    @staticmethod
    def trials(inputs,spec,result):
        from agent_harness_lab.cli import evaluator
        grade_status = evaluator().grading_status
        if not isinstance(result.get('runtime'),dict): raise ValueError('missing observed runtime')
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
            records.append(TrialRecord(trial['trial_id'],outcome,'graded','executable_grader',
                spec.baseline_hash,spec.candidate_hash,spec.grader_hash,spec.runtime_hash))
        return records

    def summary(self, run_id, checked):
        _,events,inputs,_,_,validity = checked
        return dict(schema_version=1,run_id=run_id,state=events[-1][0]['state'],event_sha256=events[-1][1],
            oracle_validity=validity,dependencies=sorted(inputs['dependencies']),
            controller_observed=events[-1][0]['state']=='sealed',oracle_review='unverified',
            independence='unverified',automatic_promotion=False,publication_allowed=False)

    def show(self, run_id):
        with self.locked(): return self.summary(run_id,self.checked(run_id))

    def analyze(self, run_id):
        with self.locked():
            checked = self.checked(run_id); _,_,inputs,spec,result,validity = checked
            if validity != 'current':
                value = dict(status='blocked' if validity=='invalidated' else 'incomplete',reason='oracle_quarantined' if validity=='invalidated' else 'controller_run_not_sealed')
            else: value = analyze(spec,self.trials(inputs,spec,result))
            return dict(value,oracle_validity=validity,controller_run_id=run_id,oracle_review='unverified',
                independence='unverified',automatic_promotion=False,publication_allowed=False)

    def run(self, manifest, baseline, candidate, *, cgroup_root, image_root, image_sha256, disk_bytes, budget_seconds=60):
        from agent_harness_lab.cli import evaluator
        from .bank_tools import frozen_grader
        from .bank_revisions import sync_tree
        started = time.monotonic(); sha256(image_sha256)
        if type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds) or not 0<budget_seconds<=300:
            raise ValueError('history budget must be positive and at most 300 seconds')
        deadline = started+budget_seconds
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
        run_id = hashlib.sha256(('run:'+secrets.token_hex(32)).encode()).hexdigest()
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
            write_bytes(case/'manifest.json',raw); write_bytes(case/'inputs.json',encoded(inputs)); sync_tree(case)
            events = []; self.append(case,events,'run_started','running',dict(inputs_sha256=record(case/'inputs.json')[1]))
            sync_directory(case.parent); sync_directory(self.root)
        args = SimpleNamespace(manifest=manifest,baseline=baseline,candidate=candidate,output=case/'execution',
            agent='offline',codex='must-not-launch',claude='must-not-launch',cgroup_root=Path(cgroup_root),
            image_root=image_root,image_sha256=image_sha256,disk_bytes=disk_bytes,parent_deadline=deadline)
        try:
            runner.evaluate(args)
            result,result_hash = record(case/'execution/results.json',8*1024*1024)
            self.trials(inputs,spec,result)
            if (record(manifest,1024*1024)[1] != manifest_hash
                    or identity(manifest.parent,selected,source['grader_bundles']) != spec.grader_hash
                    or time.monotonic() >= deadline):
                raise ValueError('history inputs changed or sealing budget exhausted')
            evidence = dict(results_sha256=result_hash,execution_tree_sha256=tree_hash(case/'execution'))
            sync_tree(case/'execution'); write_bytes(case/'evidence.json',encoded(evidence)); sync_directory(case)
            if time.monotonic() >= deadline: raise TimeoutError('history sealing budget exhausted')
            with self.locked():
                current = self.history(case)
                self.append(case,current,'run_finished','sealed',dict(evidence_sha256=record(case/'evidence.json')[1]))
        except (OSError,ValueError,TypeError,KeyError,subprocess.SubprocessError,KeyboardInterrupt) as error:
            with self.locked():
                current = self.history(case)
                self.append(case,current,'run_finished','incomplete',dict(reason='execution_or_evidence_incomplete'))
            if isinstance(error,KeyboardInterrupt): raise
        return self.show(run_id)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl experiment-history',description=__doc__)
    parser.add_argument('--store',type=Path,required=True,help='Explicit private controller history')
    actions = parser.add_subparsers(dest='action',required=True)
    run = actions.add_parser('run',help='Execute a fresh full pinned offline comparison; never import results')
    for name in ('manifest','baseline','candidate','cgroup-root','image-root'):
        run.add_argument('--'+name,type=Path,required=True)
    run.add_argument('--image-sha256',required=True)
    run.add_argument('--disk-bytes',type=int,required=True)
    run.add_argument('--budget-seconds',type=float,default=60)
    for name in ('show','analyze'):
        command = actions.add_parser(name)
        command.add_argument('--run',dest='run_id',required=True)
    oracle = actions.add_parser('oracle',help='Read an observed bundle registration and current CAS identity')
    oracle.add_argument('--bundle',required=True)
    quarantine = actions.add_parser('quarantine',help='Revoke a known bundle across all dependent comparisons')
    quarantine.add_argument('--record',type=Path,required=True)
    args = parser.parse_args(argv)
    try:
        store = HistoryStore(args.store)
        if args.action == 'run':
            result = store.run(args.manifest,args.baseline,args.candidate,cgroup_root=args.cgroup_root,
                image_root=args.image_root,image_sha256=args.image_sha256,disk_bytes=args.disk_bytes,budget_seconds=args.budget_seconds)
            code = 0 if result['state']=='sealed' and result['oracle_validity']=='current' else 3 if result['oracle_validity']=='invalidated' else 1
        elif args.action == 'quarantine': result,code = store.quarantine(args.record),0
        elif args.action == 'oracle': result,code = store.oracle(args.bundle),0
        elif args.action == 'analyze': result,code = store.analyze(args.run_id),3
        else: result,code = store.show(args.run_id),0
        print(json.dumps(result,indent=2,allow_nan=False))
        return code
    except BlockingIOError:
        print(json.dumps(dict(status='busy',automatic_promotion=False,publication_allowed=False)))
        return 3
    except (ValueError,OSError,TypeError,KeyError):
        parser.exit(2,'experiment history error: invalid or unavailable private controller evidence\n')
