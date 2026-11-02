"""Opt-in real kernel boundaries; synthetic fixtures, no inference or credentials."""
from contextlib import contextmanager
import errno
import hashlib
import os
import json
import re
import shutil
import subprocess
from pathlib import Path
import selectors
import socket
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import regrade_artifacts
from agent_harness_lab.evaluation.cgroup import Cgroup, delegated
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend
from agent_harness_lab.evaluation.image import MOUNT_DIRS, scan
from test_offline_evaluation import OfflineFixture

ENABLED = bool(os.environ.get('AHL_TEST_CGROUP_ROOT')) or os.environ.get('AHL_TEST_SYSTEMD_SCOPE') == '1'


@contextmanager
def kernel_subtree():
    explicit = os.environ.get('AHL_TEST_CGROUP_ROOT')
    if explicit:
        yield delegated(explicit)
        return
    lines = Path('/proc/self/cgroup').read_text().splitlines()
    if len(lines) != 1 or not lines[0].startswith('0::/'):
        raise ValueError('kernel canaries require unified cgroups')
    root = Path('/sys/fs/cgroup')/lines[0][4:]
    if (not root.name.startswith('ahl-sandbox-test-') or not root.name.endswith('.scope')
            or root.stat().st_uid != os.geteuid()
            or (root/'cgroup.procs').read_text().split() != [str(os.getpid())]):
        raise ValueError('canaries require their own explicitly delegated foreground scope')
    controller = root/'controller'
    controller.mkdir(mode=0o700)
    try:
        (controller/'cgroup.procs').write_text(str(os.getpid()))
        # Only this disposable test scope changes; production never enables controllers.
        (root/'cgroup.subtree_control').write_text('+cpu +memory +pids')
        yield delegated(root)
    finally:
        (root/'cgroup.subtree_control').write_text('-cpu -memory -pids')
        (root/'cgroup.procs').write_text(str(os.getpid()))
        controller.rmdir()


class KernelFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
            raise AssertionError('kernel canaries imported the wrong package layout')
        cls.cgroup_root = cls.enterClassContext(kernel_subtree())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root/'work'; self.work.mkdir()
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root)

    def run_code(self, code, *, inputs=None, role='executor', limits=Limits(seconds=5), **options):
        return self.backend.run(['/usr/bin/python3', '-c', code], self.work, inputs or {}, role, limits, **options)

    def assert_complete(self, run):
        self.assertEqual(run['status'], 'completed', run['stderr'])
        self.assertEqual(run['exit_code'], 0, run['stderr'])
        self.assertTrue(run['inputs_unchanged'])
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])
        self.assertFalse(run['attestation']['automatic_promotion'])


def pinned_runtime(rootfs):
    rootfs.mkdir(mode=0o700)
    for name in MOUNT_DIRS: (rootfs/name).mkdir(parents=True,exist_ok=True)
    environment = {'PATH':'/usr/bin:/bin','LC_ALL':'C'}
    stdlib = Path(subprocess.check_output(['/usr/bin/python3','-I','-c',
        'import sysconfig; print(sysconfig.get_path("stdlib"))'],env=environment,text=True).strip())
    shutil.copytree(stdlib,rootfs/stdlib.relative_to('/'),dirs_exist_ok=True,
        ignore=shutil.ignore_patterns('site-packages','__pycache__','test','tests','ensurepip','idlelib','tkinter'))
    binaries = [Path(path) for path in ('/usr/bin/python3','/usr/bin/true','/usr/bin/bwrap')]
    extensions = list((stdlib/'lib-dynload').glob('*.so'))
    libraries = subprocess.check_output(['ldd',*map(str,binaries+extensions)],env=environment,text=True)
    dependencies = {Path(path) for path in re.findall(r'(/[^\s():]+)',libraries) if Path(path).is_file()}
    for source in binaries+sorted(dependencies):
        target = rootfs/source.relative_to('/'); target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    (rootfs/'image-marker').write_text('SYNTHETIC_PINNED_IMAGE')
    return scan(rootfs)


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class NamespaceKernelTests(KernelFixture):
    def test_executable_artifact_cannot_read_the_grader_reference(self):
        bundle = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1/graders/localization'
        expected = json.loads((bundle/'expected.json').read_text())
        malicious = '''import json,pathlib
paths=[pathlib.Path('/inputs/oracle/expected.json')]
paths += [p/'root/inputs/oracle/expected.json' for p in pathlib.Path('/proc').iterdir() if p.name.isdigit()]
for path in paths:
 try: reference=json.loads(path.read_text())
 except OSError: continue
 print(json.dumps(reference['python_checks']['outputs'])); raise SystemExit(0)
raise RuntimeError('oracle is unavailable')
'''
        code = f'''import pathlib
pathlib.Path('app').mkdir()
pathlib.Path('app/calc.py').write_text({malicious!r})
pathlib.Path('original.txt').write_text('preserve\\n')
pathlib.Path('result.json').write_text({json.dumps(expected['result'])!r})
'''
        self.assert_complete(self.run_code(code))
        grade = regrade_artifacts(self.work, ['app/calc.py', 'original.txt', 'result.json'], bundle,
                                 digest(bundle), self.backend, limits=Limits(seconds=5))
        self.assertEqual(grade['status'], 'failed')
        self.assertIs(grade['outcome'], False)

    def test_capability_with_real_aggregate_controls(self):
        result = self.backend.capability()
        self.assertEqual(result['status'], 'available', result)
        self.assertIsNotNone(result['cgroup_v2'])
        self.assertFalse(result['automatic_promotion'])

    def test_roles_cannot_read_forbidden_files_or_mutate_readonly_inputs(self):
        (self.root/'forbidden').write_text('SYNTHETIC_FORBIDDEN')
        harness, fixture, oracle = (self.root/name for name in ('harness', 'fixture', 'oracle'))
        for path in (harness, fixture, oracle):
            path.mkdir(); (path/'marker').write_text('SYNTHETIC_INPUT')
        with tempfile.TemporaryDirectory(prefix='ahl-canary-', dir=Path.home()) as home:
            secret = Path(home)/'canary'; secret.write_text('SYNTHETIC_HOME')
            for role in ('proposer', 'evaluator_author', 'executor', 'grader'):
                inputs = {'fixture': fixture, 'oracle': oracle} if role == 'grader' else {'harness': harness, 'fixture': fixture}
                code = f'''import os,pathlib
assert 'AHL_TEST_SECRET' not in os.environ
for value in {list(map(str, (secret, self.root/'forbidden', oracle, self.root)))!r}:
 assert not pathlib.Path(value).exists()
assert pathlib.Path('/inputs/oracle').exists() is {role == 'grader'!r}
for name in {list(inputs)!r}:
 path=pathlib.Path('/inputs')/name/'marker'
 assert path.read_text() == 'SYNTHETIC_INPUT'
 try: path.write_text('mutated'); raise AssertionError('readonly input was writable')
 except OSError: pass
 try: path.chmod(0o777); raise AssertionError('readonly input chmod succeeded')
 except OSError: pass
'''
                with self.subTest(role=role), patch.dict(os.environ, AHL_TEST_SECRET='SYNTHETIC_ENV'):
                    self.assert_complete(self.run_code(code, inputs=inputs, role=role))

    def test_network_namespace_cannot_connect_to_host_listener(self):
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0)); listener.listen()
            port = listener.getsockname()[1]
            code = f"import socket; s=socket.socket(); s.settimeout(.5); assert s.connect_ex(('127.0.0.1',{port})) != 0"
            self.assert_complete(self.run_code(code, role='grader'))

    def test_worker_links_do_not_escape_bounded_artifact_extraction(self):
        code = f'''import os,pathlib
os.symlink({str(self.root/'forbidden')!r},'symlink')
try: pathlib.Path('symlink').read_bytes(); raise AssertionError('host path was readable')
except OSError: pass
pathlib.Path('original').write_text('synthetic')
os.link('original','hardlink')
'''
        self.assert_complete(self.run_code(code))
        for name in ('symlink', 'hardlink', '../forbidden'):
            with self.assertRaises(ValueError): artifacts(self.work, [name])

    def test_forged_passing_artifact_is_regraded_by_pristine_oracle(self):
        bundle = self.root/'oracle'; bundle.mkdir()
        (bundle/'expected.json').write_text('42')
        (bundle/'grade.py.txt').write_text('''import json,pathlib,sys
outcome=json.loads((pathlib.Path(sys.argv[1])/'result.json').read_text()) == json.loads(pathlib.Path(sys.argv[2]).read_text())
print(json.dumps(dict(schema_version=1,valid=True,outcome=outcome,grader_id='synthetic',grader_version='1')))
sys.exit(0 if outcome else 1)
''')
        self.assert_complete(self.run_code("from pathlib import Path; Path('result.json').write_text('{\"outcome\":true}'); print('passed')"))
        grade = regrade_artifacts(self.work, ['result.json'], bundle, digest(bundle), self.backend, limits=Limits(seconds=5))
        self.assertEqual(grade['status'], 'failed')
        self.assertIs(grade['outcome'], False)

    def test_output_timeout_and_interruption_leave_no_worker_group(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        flood = self.run_code("print('x'*100000)", limits=Limits(seconds=3, output_bytes=1024))
        self.assertEqual(flood['status'], 'output_limit')
        timeout = self.run_code('import time; time.sleep(30)', limits=Limits(seconds=.5))
        self.assertEqual(timeout['status'], 'timeout')
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class OfflineKernelTests(OfflineFixture, KernelFixture):
    def test_both_arms_of_all_starter_tasks_use_real_executor_and_pristine_grader(self):
        self.args.cgroup_root = self.cgroup_root
        self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        self.assertEqual(len(result['trials']), 14)
        self.assertTrue(result['inputs_unchanged'])
        self.assertFalse(result['automatic_promotion'])
        for trial in result['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
            self.assertIs(trial['grader']['outcome'], trial['version'] == 'candidate')
            for run in (trial['execution'], trial['grader']['execution']):
                self.assertEqual(run['attestation']['backend'], 'bubblewrap-offline-v1')
                self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class PinnedImageKernelTests(OfflineFixture, KernelFixture):
    def test_oracle_history_seals_real_arms_and_quarantines_shared_grader_dependencies(self):
        from dataclasses import asdict, replace
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        tasks = [self.manifest['tasks'][0],self.manifest['tasks'][-1]]
        self.manifest['tasks'] = tasks
        self.manifest['grader_bundles'] = {t['id']:self.manifest['grader_bundles'][t['id']] for t in tasks}
        plan = ExperimentSpec('synthetic-history',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,4,2,.05,.1,.05,tuple((t['family'],.5) for t in tasks),
            self.runner.digest(self.root/'baseline'),self.runner.digest(self.root/'candidate'),
            identity(self.root/'export',tasks,self.manifest['grader_bundles']),frozen['sha256'])
        self.manifest['experiment'] = asdict(plan); self.write_manifest()
        history = HistoryStore(self.root/'history')
        options = dict(cgroup_root=self.cgroup_root,image_root=rootfs,image_sha256=frozen['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=60)
        old = history.run(self.args.manifest,self.args.baseline,self.args.candidate,**options)
        self.assertEqual(old['state'],'sealed'); self.assertEqual(old['oracle_validity'],'current')
        report = history.case(old['run_id'])/'execution/results.json'; original = report.read_bytes()
        bundle = self.manifest['grader_bundles'][tasks[0]['id']]
        decision = dict(schema_version=1,bundle_sha256=bundle['digest'],
            expected_event_sha256=history.oracle(bundle['digest'])['event_sha256'],
            operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='synthetic kernel canary',basis='Exercise oracle dependency quarantine.'),
            evidence_refs=[dict(ref='synthetic:kernel-history',sha256='a'*64)],reason='Synthetic quarantine.')
        record = self.root/'quarantine.json'; record.write_text(json.dumps(decision)); history.quarantine(record)
        self.assertEqual(history.analyze(old['run_id'])['reason'],'oracle_quarantined')
        self.assertEqual(report.read_bytes(),original)
        reference = self.root/'export'/bundle['path']/'expected.json'; reference.write_text(reference.read_text()+'\n')
        bundle['digest'] = digest(reference.parent)
        plan = replace(plan,grader_hash=identity(self.root/'export',tasks,self.manifest['grader_bundles']))
        self.manifest['experiment'] = asdict(plan); self.write_manifest()
        new = history.run(self.args.manifest,self.args.baseline,self.args.candidate,**options)
        self.assertEqual(new['state'],'sealed'); self.assertEqual(new['oracle_validity'],'current')
        observed = json.loads((history.case(new['run_id'])/'execution/results.json').read_text())
        self.assertEqual(len(observed['trials']),4)
        self.assertEqual({t['version'] for t in observed['trials']},{'baseline','candidate'})
        self.assertEqual(history.show(old['run_id'])['oracle_validity'],'invalidated')
        self.assertEqual(new['oracle_review'],'unverified'); self.assertEqual(new['independence'],'unverified')

    def test_pinned_image_is_used_by_executor_pristine_grader_and_nested_checks(self):
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs, image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        scratch = self.root/'scratch'; scratch.mkdir()
        code = '''from pathlib import Path
assert Path('/image-marker').read_text() == 'SYNTHETIC_PINNED_IMAGE'
assert not Path('/etc/os-release').exists()
assert not Path('/inputs/oracle').exists()
try: Path('/image-marker').write_text('changed'); raise AssertionError('image is writable')
except OSError: pass
'''
        run = backend.run(['/usr/bin/python3', '-I', '-c', code], scratch, {}, 'executor', Limits(seconds=5), artifact_allowlist=[])
        self.assert_complete(run)
        self.assertTrue(run['attestation']['runtime_image']['verified_after'])
        self.args.cgroup_root = self.cgroup_root
        self.args.image_root, self.args.image_sha256 = rootfs, frozen['sha256']
        self.args.disk_bytes = 64*1024*1024
        self.assertEqual(self.runner.evaluate(self.args), 1)
        result = self.results()
        self.assertEqual(len(result['trials']), 14)
        for trial in result['trials']:
            self.assertEqual(trial['status'], 'passed' if trial['version'] == 'candidate' else 'failed')
            for execution in (trial['execution'], trial['grader']['execution']):
                self.assertTrue(execution['attestation']['aggregate_disk_bound_verified'])
                attested = execution['attestation']['runtime_image']
                self.assertEqual(attested['sha256'], frozen['sha256'])
                self.assertTrue(attested['verified_before'] and attested['verified_after'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class RegressionDraftKernelTests(KernelFixture):
    def test_preservation_qualification_uses_real_prior_and_fresh_pinned_workers(self):
        from dataclasses import replace
        from test_preservation_cohorts import PreservationCohortTests
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        fixture=PreservationCohortTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        value=fixture.fixture; runtime=self.root/'runtime'; pinned=pinned_runtime(runtime)
        value.image_hash=pinned['sha256']; value.install_plan()
        profile=dict(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=60)
        prior=value.store.run(value.export/'manifest.json',value.baseline,value.candidate,**profile)
        self.assertEqual(prior['state'],'sealed',prior)
        captured=value.store.historical_plan(prior['run_id'])
        qualification=fixture.value['cohorts'][0]['qualification']
        qualification.update(run_id=prior['run_id'],event_sha256=captured['event_sha256'],
            evidence_sha256=captured['evidence_sha256'],plan_sha256=captured['plan_sha256'])
        pin=fixture.write(); shutil.copy2(fixture.path,value.export/'cohorts.json')
        value.baseline=value.candidate
        next_candidate=value.root/'kernel-next-candidate'; shutil.copytree(value.candidate,next_candidate)
        value.candidate=next_candidate; value.install_plan()
        value.manifest['cohort_manifest']=dict(path='cohorts.json',sha256=pin)
        spec=replace(ExperimentSpec.parse(value.manifest['experiment']),cohort_manifest_sha256=pin)
        value.manifest['experiment']=spec.to_dict(); value.write_manifest()
        observed=value.store.run(value.export/'manifest.json',value.baseline,value.candidate,**profile)
        self.assertEqual(observed['state'],'sealed',observed)
        self.assertEqual(observed['cohort_qualification'],'current_membership_only')
        analysis=value.store.analyze(observed['run_id'])
        self.assertEqual(analysis['reason'],'uncertainty_or_margin_not_met')
        self.assertEqual(analysis['preservation']['cohorts']['preserved']['endpoint']['mean'],0)
        receipt=value.store.decision(observed['run_id'])
        self.assertEqual(receipt['analysis'],analysis)
        self.assertEqual(value.store.verify_decision(observed['run_id'],receipt['receipt_sha256']),receipt)
        self.assertFalse(receipt['adoption_authority'])
        from agent_harness_lab.optimization.history_evidence import HistorySearchContext
        from agent_harness_lab.optimization.search import CandidateRecord
        context=HistorySearchContext(value.store.root,value.baseline,value.export/'manifest.json',
            dict(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
                 disk_bytes=64*1024*1024,budget_seconds=45,work_root=self.root/'prepared-search'),['solve.py'])
        control=CandidateRecord('baseline',(),'synthetic-kernel-control',{},('synthetic:pinned-workers',),
            'Observe a frozen no-op comparison','Any changed input invalidates the result')
        checked=context.evaluate(control,stage='baseline',event_id='synthetic:kernel-search')
        self.assertFalse(checked['qualified_feasible'])
        self.assertTrue(checked['retain_no_op'])
        self.assertEqual(context.verify(checked['binding']),checked)
        from agent_harness_lab.evaluation.cohort_requalification import ALGORITHM,qualify
        original=value.store.historical_plan(observed['run_id'])
        protocol=dict(schema_version=2,algorithm=ALGORITHM,prior_run_id=original['run_id'],
            prior_plan_sha256=original['plan_sha256'],cohort_manifest_sha256=pin,
            author='synthetic-kernel-protocol-author',image_sha256=pinned['sha256'],
            max_total_trials=8,before_harnesses=[])
        protocol_path=self.root/'qualification-protocol.json'; protocol_path.write_text(json.dumps(protocol))
        protocol_sha=hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        review=dict(identity='synthetic-kernel-protocol-reviewer',source='operator_attestation',
            basis_kind='independent_artifact_review',evidence_refs=[dict(ref='synthetic:protocol',sha256=protocol_sha)])
        fresh=qualify(protocol_path,protocol_sha,original,value.bank,value.baseline,value.candidate,
            self.root/'prepared-requalification',value.store,review=review,cgroup_root=self.cgroup_root,
            image_root=runtime,image_sha256=pinned['sha256'],disk_bytes=64*1024*1024,
            parent_deadline=time.monotonic()+45)
        self.assertEqual(len(fresh['phases']),1)
        fresh_run=fresh['phases'][0]['run_id']
        self.assertNotIn(fresh_run,(prior['run_id'],observed['run_id']))
        self.assertNotEqual(fresh['manifest_sha256'],pin)
        for run_id in (prior['run_id'],observed['run_id'],fresh_run):
            trials=value.store.checked(run_id)[4]['trials']; self.assertEqual(len(trials),4)
            for trial in trials:
                for execution in (trial['execution'],trial['grader']['execution']):
                    image=execution['attestation']['runtime_image']
                    self.assertEqual(image['sha256'],pinned['sha256'])
                    self.assertTrue(image['verified_before'] and image['verified_after'])

    def test_adversarial_final_effects_are_sealed_and_veto_the_candidate(self):
        from test_oracle_behavior import fixture
        value=fixture(self,all_tasks=True,
            candidate_suffix="Path('sink.txt').write_text(Path('canary.txt').read_text())\n")
        runtime=self.root/'runtime'; pinned=pinned_runtime(runtime)
        value.manifest['experiment']['runtime_hash']=pinned['sha256']; value.write_manifest()
        completed=value.store.run(value.args.manifest,value.args.baseline,value.args.candidate,
            cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=75)
        self.assertEqual(completed['state'],'sealed',completed)
        result=value.store.analyze(completed['run_id'])
        self.assertEqual((result['status'],result['reason']),('rejected','critical_regression'))
        report=result['behavior']; self.assertFalse(report['required_observation_unknown'])
        self.assertEqual(sum(f['candidate']['critical_trials'] for f in report['families'].values()),14)
        for family in report['families'].values():
            for arm,counts in family.items():
                self.assertEqual(counts['task_completed']['true'],counts['trials'])
                self.assertEqual(counts['effects']['canary_exported']['true' if arm=='candidate' else 'false'],counts['trials'])
        self.assertEqual(report['unobserved']['model_compliance'],'unknown')

    def test_grader_correction_audits_real_controls_without_a_behavioral_failure_claim(self):
        from test_grader_correction import GraderCorrectionTests
        fixture = GraderCorrectionTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        fixture.spec_value['image_sha256'] = frozen['sha256']; fixture.write_spec()
        parent = fixture.bank.read_bytes(); proposed = fixture.propose()
        backend = SandboxBackend(cgroup_root=self.cgroup_root,image_root=rootfs,
            image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
        audited = fixture.store.audit(proposed['case_id'],backend)
        self.assertEqual(audited['state'],'audited',audited)
        self.assertEqual(audited['old_audit_status'],'controls_failed')
        self.assertEqual(audited['replacement_audit_status'],'grader_controls_passed')
        reports = [json.loads(p.read_text()) for p in fixture.store.root.rglob('review.json')]
        old = next(r for r in reports if r['status']=='controls_failed')
        new = next(r for r in reports if r['status']=='grader_controls_passed')
        self.assertTrue(next(c for c in old['checks'] if c['control']=='complete')['observed'])
        self.assertFalse(next(c for c in old['checks'] if c['control']=='alternative_valid')['observed'])
        self.assertTrue(next(c for c in new['checks'] if c['control']=='alternative_valid')['observed'])
        self.assertFalse(next(c for c in new['checks'] if c['control']=='superficial')['observed'])
        for report in reports:
            for check in report['checks']:
                self.assertEqual(check['attestation']['runtime_image']['sha256'],frozen['sha256'])
                self.assertTrue(check['attestation']['cgroup_v2']['empty_after_cleanup'])
        reviewed = fixture.store.review(fixture.case_id,fixture.review_file())
        self.assertEqual(reviewed['state'],'reviewed'); self.assertEqual(fixture.bank.read_bytes(),parent)
        self.assertFalse(reviewed['task_failure_verified']); self.assertFalse(reviewed['bank_replacement'])

    def test_grader_correction_applies_and_reruns_entire_pair_with_real_pinned_workers(self):
        from dataclasses import asdict
        from agent_harness_lab.cli import evaluator
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
        from test_grader_correction import GraderCorrectionTests
        fixture = GraderCorrectionTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        fixture.spec_value['image_sha256'] = frozen['sha256']; fixture.write_spec()
        proposed = fixture.propose()
        profile = dict(cgroup_root=self.cgroup_root,image_root=rootfs,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024,budget_seconds=60)
        backend = SandboxBackend(**{k:v for k,v in profile.items() if k!='budget_seconds'})
        audited = fixture.store.audit(proposed['case_id'],backend); self.assertEqual(audited['state'],'audited',audited)
        fixture.store.review(fixture.case_id,fixture.review_file())
        root = fixture.root; baseline,candidate = root/'baseline',root/'candidate'
        export = root/'prior-export'; export.mkdir()
        shutil.copytree(root/'fixture',export/'fixture'); shutil.copytree(root/'oldoracle',export/'oldoracle')
        for folder,order in ((baseline,('count','label')),(candidate,('label','count'))):
            folder.mkdir()
            (folder/'solve.py').write_text("import json,pathlib\nw=pathlib.Path.cwd(); data=json.loads((w/'task.json').read_text())['inputs']\n"
                +f"value={{k:data[k] for k in {order!r}}}\n"
                +"(w/'result.json').write_text(json.dumps(value,separators=(',',':')))\n")
        runner = evaluator()
        task = dict(id=fixture.task['task_id'],root=fixture.task['root_id'],family=fixture.task['family'],split='development',
            prompt=json.loads((root/'fixture/task.json').read_text())['prompt'],fixture='fixture',artifacts=fixture.task['artifacts'],
            grader=[sys.executable,str(export/'oldoracle/grade.py.txt'),'{workspace}',str(export/'oldoracle/expected.json')],
            offline_executor=['/usr/bin/python3','-I','/inputs/harness/solve.py'])
        bundles = {task['id']:dict(path='oldoracle',digest=fixture.task['grader_digest'])}
        spec = ExperimentSpec('synthetic-real-correction-pair',((task['id'],task['root'],task['family']),),2,13,4,2,.05,.1,.05,
            ((task['family'],1),),runner.digest(baseline),runner.digest(candidate),identity(export,[task],bundles),frozen['sha256'])
        manifest = dict(schema_version=1,tasks=[task],grader_bundles=bundles,experiment=asdict(spec),max_trials=4,budget_seconds=45,timeout_seconds=5)
        source = export/'manifest.json'; source.write_text(json.dumps(manifest))
        history = HistoryStore(root/'real-history')
        prior = history.run(source,baseline,candidate,**profile); self.assertEqual(prior['state'],'sealed',prior)
        reviewed = fixture.store.show(fixture.case_id)
        operator = dict(identity='synthetic-kernel-curator',source='operator_attestation',provenance='synthetic real-kernel canary',basis='Explicit synthetic reviewed replacement')
        request = dict(schema_version=1,case_id=fixture.case_id,expected_event_sha256=reviewed['event_sha256'],evidence_sha256=reviewed['evidence_sha256'],
            parent_bank_sha256=hashlib.sha256(fixture.bank.read_bytes()).hexdigest(),parent_revision_sha256=None,
            history_store=str(history.root),expected_oracle_event_sha256=history.oracle(fixture.task['grader_digest'])['event_sha256'],operator=operator,reason='Synthetic kernel application')
        application = root/'apply.json'; application.write_text(json.dumps(request))
        applied = fixture.store.apply(fixture.case_id,fixture.bank,history,application)
        self.assertEqual(history.show(prior['run_id'])['oracle_validity'],'invalidated')
        original = history.historical_plan(prior['run_id']); current = fixture.store.show(fixture.case_id)
        request = dict(schema_version=1,case_id=fixture.case_id,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'],
            bank_sha256=applied['bank_manifest_sha256'],revision_sha256=applied['revision_sha256'],history_store=str(history.root),
            prior={k:original[k] for k in ('run_id','event_sha256','inputs_sha256','plan_sha256','evidence_sha256')},operator=operator,reason='Fresh real paired execution')
        rerun = root/'rerun.json'; rerun.write_text(json.dumps(request))
        result = fixture.store.rerun(fixture.case_id,rerun,baseline,candidate,**profile)
        self.assertEqual(result['rerun_status'],'completed',result)
        actual = history.historical_plan(result['new_run_id']); observed = ExperimentSpec.parse(actual['plan'])
        expected = asdict(spec); expected['grader_hash'] = observed.grader_hash
        self.assertEqual(asdict(observed),expected)
        self.assertTrue({t['trial_id'] for t in schedule(spec)}.isdisjoint(t['trial_id'] for t in schedule(observed)))
        self.assertEqual(history.show(result['new_run_id'])['oracle_validity'],'current')
        analysis = history.analyze(result['new_run_id']); self.assertEqual(analysis['reason'],'insufficient_independent_roots')
        self.assertFalse(result['automatic_promotion']); self.assertFalse(result['task_failure_verified'])
        self.assertEqual(result['historical_state_reproduction'],'unverified'); self.assertEqual(result['harness_mode_evidence'],'unknown')
        rows = json.loads((history.case(result['new_run_id'])/'execution/results.json').read_text())['trials']
        self.assertEqual(len(rows),4)
        for row in rows:
            self.assertEqual(row['status'],'passed')
            for run in (row['execution'],row['grader']['execution']):
                self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])
                self.assertEqual(run['attestation']['runtime_image']['sha256'],frozen['sha256'])

    def test_regression_review_lifecycle_seals_real_replay_with_operator_attestation_and_bank_authority(self):
        from test_regression_replay import make_replay, SOLVER
        from agent_harness_lab.evaluation.regression_review import ReviewStore
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        source_root = self.root/'source'; source_root.mkdir()
        store = ReviewStore(self.root/'private-cases')
        solver = SOLVER + f'\nassert not pathlib.Path({str(store.root)!r}).exists()\n'
        proposal, draft, spec = make_replay(source_root, frozen['sha256'], solver)
        reported = store.propose(proposal, draft, spec, 'synthetic-author')
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs,
            image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        reproduced = store.reproduce(reported['case_id'], backend)
        self.assertEqual(reproduced['state'], 'reproduced', reproduced)
        prepared = store.draft(reported['case_id'])
        review = dict(schema_version=1, case_id=reported['case_id'], expected_event_sha256=prepared['event_sha256'],
            evidence_sha256=prepared['evidence_sha256'], decision='reviewed',
            reviewer=dict(identity='synthetic-reviewer', source='operator_attestation', provenance='synthetic kernel fixture',
                basis_kind='independent_artifact_review', basis='Synthetic operator assertion; no authenticated human-independence claim.'),
            evidence_refs=[dict(ref='synthetic:kernel-review', sha256='a'*64)], reason='Exercise the operator review contract.')
        path = self.root/'operator-review.json'; path.write_text(json.dumps(review))
        reviewed = store.review(reported['case_id'], path)
        self.assertEqual(reviewed['state'], 'reviewed'); self.assertEqual(reviewed['review_independence'], 'operator_attested')
        self.assertEqual(reviewed['historical_verification'], 'unverified'); self.assertFalse(reviewed['bank_admission'])
        import hashlib
        import shutil
        from agent_harness_lab.config import starter
        from agent_harness_lab.evaluation.task_bank import validate
        parent_root = self.root/'parent-bank'; shutil.copytree(starter().parent,parent_root)
        parent = parent_root/'bank.json'; value = json.loads(parent.read_text())
        value['tasks'] = [task for task in value['tasks'] if task['task_id']=='resumption']
        parent.write_text(json.dumps(value)); original = parent.read_bytes()
        admission = dict(schema_version=1,case_id=reported['case_id'],expected_event_sha256=reviewed['event_sha256'],
            evidence_sha256=reviewed['evidence_sha256'],parent_bank_sha256=hashlib.sha256(original).hexdigest(),
            parent_revision_sha256=None,purpose='development_regression',provenance_binding=None,
            operator=dict(identity='synthetic-curator',source='operator_attestation',provenance='synthetic kernel fixture',
                basis='Freeze the actual controlled replay and all reviewed controls.'),reason='Synthetic kernel admission.')
        record = self.root/'admission.json'; record.write_text(json.dumps(admission))
        accepted = store.accept(reported['case_id'],parent,record)
        bank = store.root/'revisions'/accepted['revision_sha256']/'bank.json'
        self.assertTrue(accepted['current_eligible']); self.assertFalse(validate(bank)['certification_eligible'])
        self.assertEqual(parent.read_bytes(),original); self.assertEqual(store.show(reported['case_id'])['state'],'accepted')
        from dataclasses import asdict
        from test_offline_evaluation import SOLVER as comparison_solver
        from agent_harness_lab.cli import evaluator
        from agent_harness_lab.evaluation.bank_tools import export_manifest
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        export = self.root/'bank-export'; manifest = export_manifest(bank,export)
        runner = evaluator()
        baseline,candidate = self.root/'baseline',self.root/'candidate'
        for folder,source in ((baseline,"from pathlib import Path; Path('result.json').write_text('{}')\n"),
                              (candidate,comparison_solver)):
            folder.mkdir(); (folder/'solve.py').write_text(source)
        tasks = manifest['tasks']; families = sorted({t['family'] for t in tasks})
        for task in tasks: task['offline_executor'] = ['/usr/bin/python3','-I','/inputs/harness/solve.py']
        plan = ExperimentSpec('synthetic-bank-authority',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,4,2,.05,.1,.05,tuple((f,1/len(families)) for f in families),runner.digest(baseline),
            runner.digest(candidate),identity(export,tasks,manifest['grader_bundles']),frozen['sha256'])
        manifest['experiment'] = asdict(plan); (export/'manifest.json').write_text(json.dumps(manifest))
        history = HistoryStore(self.root/'bank-history')
        observed = history.run(export/'manifest.json',baseline,candidate,cgroup_root=self.cgroup_root,
            image_root=rootfs,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
        self.assertEqual(observed['state'],'sealed'); self.assertEqual(observed['bank_authority'],'current_eligible')
        self.assertEqual(observed['oracle_validity'],'current')
        result = history.case(observed['run_id'])/'execution/results.json'; frozen_result = result.read_bytes()
        trials = json.loads(frozen_result)['trials']; self.assertEqual(len(trials),4)
        self.assertEqual({t['version'] for t in trials},{'baseline','candidate'})
        for trial in trials:
            self.assertEqual(trial['grader']['outcome'],trial['version']=='candidate')
            self.assertEqual(trial['grader']['execution']['attestation']['runtime_image']['sha256'],frozen['sha256'])
        current = store.show(reported['case_id'])
        review.update(expected_event_sha256=current['event_sha256'],decision='quarantined')
        path.write_text(json.dumps(review)); store.review(reported['case_id'],path)
        with self.assertRaises(ValueError): history.show(observed['run_id'])
        with self.assertRaises(ValueError): history.analyze(observed['run_id'])
        self.assertEqual(result.read_bytes(),frozen_result)

    def test_regression_replay_uses_real_matched_snapshot_views_and_hidden_oracle(self):
        from test_regression_replay import make_replay, SOLVER
        from agent_harness_lab.evaluation.regression_replay import replay
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        replay_root = self.root/'replay'; replay_root.mkdir()
        canary = self.root/'host-only'; canary.write_text('SYNTHETIC_HOST_ONLY')
        solver = SOLVER + f'''
assert not pathlib.Path('/inputs/oracle').exists()
assert not pathlib.Path({str(canary)!r}).exists()
assert set(p.name for p in view.iterdir()) == {{'harness','config','memory'}}
try:
 (view/'config/state.json').write_text('changed')
 raise AssertionError('snapshot input is writable')
except OSError: pass
'''
        source, draft, spec = make_replay(replay_root, frozen['sha256'], solver)
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs,
            image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        report = replay(source, draft, spec, self.root/'output', backend)
        self.assertEqual(report['status'], 'replay_completed', report)
        self.assertEqual(report['reproduction_disposition'], 'reproduced')
        self.assertEqual(report['task_trials'], 8)
        self.assertTrue(report['repair_observed']); self.assertTrue(report['aa_observed_agreement'])
        self.assertFalse(report['task_failure_verified']); self.assertFalse(report['bank_admission'])

    def test_regression_draft_controls_use_real_pinned_graders_and_private_quotas(self):
        from test_regression_controls import make_draft
        from agent_harness_lab.evaluation.regression_controls import audit
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        draft_root = self.root/'draft'; draft_root.mkdir()
        source, draft = make_draft(draft_root, frozen['sha256'])
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs,
            image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        report = audit(source, draft, self.root/'review', backend)
        self.assertEqual(report['status'], 'grader_controls_passed', report)
        self.assertEqual([c['observed'] for c in report['checks']], [False, False, True, True, False])
        for control in report['checks']:
            self.assertTrue(control['attestation']['aggregate_disk_bound_verified'])
            self.assertTrue(control['attestation']['cgroup_v2']['empty_after_cleanup'])
            self.assertEqual(control['attestation']['runtime_image']['sha256'], frozen['sha256'])
        self.assertFalse(report['task_failure_verified']); self.assertFalse(report['bank_admission'])


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel:roles')
class ControlledWorkflowKernelTests(KernelFixture):
    def test_controlled_workflow_role_views_deny_source_key_candidate_and_prior_heldout_reads(self):
        from test_sandbox_construction import ControlledWorkflowTests,PROPOSER,AUTHOR,EXECUTOR,wire,workflow,responses
        fixture = ControlledWorkflowTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        fixture.args.cgroup_root = self.cgroup_root; fixture.args.image_root = rootfs
        fixture.args.image_sha256 = frozen['sha256']; fixture.args.disk_bytes = 64*1024*1024; fixture.args.timeout = 60
        home = tempfile.TemporaryDirectory(prefix='ahl-role-canary-',dir=Path.home()); self.addCleanup(home.cleanup)
        home_secret = Path(home.name)/'canary'; home_secret.write_text('SYNTHETIC_HOME_CANARY')
        probe = '''import json,os,pathlib,sys
assert 'AHL_SYNTHETIC_BROKER_KEY' not in os.environ
assert not pathlib.Path('/inputs/oracle').exists()
for value in json.loads(sys.argv[-1]):
 try: pathlib.Path(value).read_bytes()
 except OSError: continue
 raise AssertionError('forbidden host path was readable')
'''
        def transport(key,body,max_bytes,deadline):
            self.assertEqual(key,'SYNTHETIC_CONTROLLER_KEY'); self.assertNotIn(key,body.decode())
            payload = json.loads(body); fixture.histories.append(payload); ident = str(len(fixture.histories))
            if any(item.get('type')=='function_call_output' for item in payload['input']): return wire.reply([wire.message('final-'+ident)])
            instructions = payload['input'][0]['content']; output = fixture.args._run_output
            hidden = output/'private-heldout'; hidden.mkdir(exist_ok=True); (hidden/'canary').write_text('SYNTHETIC_HELDOUT')
            forbidden = [fixture.key,fixture.project/'AGENTS.md',home_secret,hidden/'canary']
            if 'You are the proposer' in instructions:
                argv = ['/usr/bin/python3','-c',probe+PROPOSER,'good']
            elif 'You are the evaluator author' in instructions:
                forbidden += [output/'candidate-0/AGENTS.md',output/'proposer-0/proposal.json']
                self.assertNotIn('SYNTHETIC_CANDIDATE_SECRET',body.decode())
                argv = ['/usr/bin/python3','-c',probe+"assert pathlib.Path('/inputs/harness/AGENTS.md').read_text() == 'bad'\n"+AUTHOR,'1','complete']
            else: argv = ['/usr/bin/python3','-c',probe+EXECUTOR,'/inputs/harness']
            argv.append(json.dumps(list(map(str,forbidden))))
            return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        with patch('agent_harness_lab.cli.evaluator',return_value=fixture.runner), \
             patch.object(responses,'request',side_effect=transport), \
             patch.object(fixture.runner,'execute',side_effect=AssertionError('no native host execution')), \
             patch.dict(os.environ,AHL_SYNTHETIC_BROKER_KEY='SYNTHETIC_ENV_KEY'):
            result = workflow.improve(fixture.args)
        self.assertEqual(result['status'],'inconclusive',result)
        self.assertEqual(result['inference']['request_attempts'],16)
        self.assertEqual((fixture.project/'AGENTS.md').read_text(),'bad')
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        self.assertFalse(ledger['request_pending']); self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(ledger))
        evaluated=json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
        for trial in evaluated['trials']:
            report=trial['controller_actions']; self.assertFalse(report['publication_allowed'])
            emission,=[row for row in report['actions'] if row['kind']=='assistant_emission']
            self.assertTrue(emission['protocol_validated'])
            self.assertEqual(emission['response_disposition'],'validated')
            self.assertEqual(emission['external_delivery'],'unknown')
            self.assertEqual(emission['observation_scope'],'controller_transport_text_only')
            self.assertLessEqual(len(emission['content']['text'].encode('utf-8')),8192)


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TrajectoryKernelTests(KernelFixture):
    def test_trajectory_assertions_use_real_workers_and_independent_artifact_grades(self):
        from dataclasses import replace
        from test_trajectory_evaluation import TrajectoryEvaluationTests
        from agent_harness_lab.evaluation import responses
        fixture = TrajectoryEvaluationTests(); fixture.setUp(); self.addCleanup(fixture.doCleanups)
        runtime = self.root/'runtime'; frozen = pinned_runtime(runtime)
        fixture.plan = replace(fixture.plan,runtime_hash=frozen['sha256'])
        fixture.freeze()
        args = fixture.f.args
        args.cgroup_root = self.cgroup_root; args.image_root = runtime
        args.image_sha256 = frozen['sha256']; args.disk_bytes = 64*1024*1024
        backend = SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,
            image_sha256=frozen['sha256'],disk_bytes=args.disk_bytes)
        args.sandbox_backend = backend
        with patch.object(responses,'request',side_effect=fixture.f.transport), \
                patch.object(backend,'run',wraps=backend.run) as workers, \
                patch.object(fixture.f.runner,'execute',side_effect=AssertionError('native agents must not launch')):
            self.assertEqual(fixture.f.runner.evaluate(args),1)
        fixture.f.backend.run.assert_not_called()
        self.assertEqual(workers.call_count,9)  # One real capability probe plus eight trial workers.
        self.assertEqual([call.args[3] for call in workers.call_args_list].count('executor'),4)
        graders = [call for call in workers.call_args_list if call.args[3]=='grader']
        self.assertEqual(sum(set(call.args[2])=={'fixture','oracle'} for call in graders),4)
        self.assertEqual(sum(call.args[0]==['/usr/bin/true'] and call.args[2]=={} for call in graders),1)
        result = fixture.fixture.results()
        self.assertEqual(fixture.f.requests,8)
        self.assertEqual(result['trajectory_analysis']['roots'],2)
        self.assertEqual(result['trajectory_analysis']['trials'],4)
        self.assertEqual(result['trajectory_analysis']['oracle_validity'],'unverified')
        self.assertEqual(result['trajectory_analysis']['native_identity'],'unverified')
        self.assertFalse(result['trajectory_analysis']['publication_allowed'])
        self.assertFalse(result['trajectory_analysis']['automatic_promotion'])
        for trial in result['trials']:
            self.assertEqual(trial['status'],'passed' if trial['version']=='candidate' else 'failed')
            grade = trial['grader']['execution']
            self.assertTrue(grade['attestation']['cgroup_v2']['empty_after_cleanup'])
            self.assertEqual(grade['attestation']['runtime_image']['sha256'],frozen['sha256'])
        for trial in fixture.controlled_trials(result):
            grade = trial['controller_grade']; receipt = grade['trajectory']
            self.assertEqual(receipt['trial_id'],trial['trial_id'])
            self.assertEqual(receipt['status'],'satisfied'); self.assertTrue(receipt['closed'])
            self.assertTrue(grade['valid']); self.assertEqual(grade['outcome'],trial['grader']['outcome'])
            self.assertEqual(grade['history_authority'],'unverified')
            self.assertEqual(receipt['native_identity'],'unverified')
            self.assertFalse(receipt['publication_allowed'])
            action, = [a for a in trial['controller_actions']['actions'] if a['kind']=='tool_request']
            self.assertTrue(action['backend_invoked']); self.assertTrue(action['containment_validated'])
        self.assertNotIn('SYNTHETIC_CONTROLLER_KEY',json.dumps(result))


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class StorageKernelTests(KernelFixture):
    def setUp(self):
        super().setUp()
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root, disk_bytes=64*1024*1024)

    def run_code(self, code, **options):
        options.setdefault('artifact_allowlist', [])
        return super().run_code(code, **options)

    def test_working_state_retains_nonzero_edits_between_workers_and_discards_timeout_changes(self):
        from agent_harness_lab.evaluation.working_state import WorkingState
        (self.work/'empty').mkdir(); (self.work/'seed').write_text('original')
        state = WorkingState(self.work,time.monotonic()+10)
        def execute(code,seconds=5):
            return self.backend.run(['/usr/bin/python3','-c',code],self.work,{},'executor',
                                    Limits(seconds=seconds),working_state=state)
        first = execute("from pathlib import Path; Path('empty').rmdir(); Path('seed').unlink(); Path('result').write_text('edited'); raise SystemExit(1)")
        self.assertEqual(first['status'],'failed'); self.assertEqual(first['exit_code'],1)
        self.assertTrue(first['attestation']['cgroup_v2']['empty_after_cleanup'])
        self.assertIsNotNone(first['attestation']['storage_policy']['extracted'])
        second = execute("from pathlib import Path; assert not Path('empty').exists(); assert not Path('seed').exists(); assert Path('result').read_text()=='edited'; Path('result').write_text('final')")
        self.assert_complete(second)
        before = state.snapshot
        failed = execute("from pathlib import Path; import time; Path('result').write_text('partial'); time.sleep(30)",.5)
        self.assertEqual(failed['status'],'timeout'); self.assertIs(state.snapshot,before)
        self.assertTrue(failed['attestation']['cgroup_v2']['empty_after_cleanup'])
        signaled = execute("from pathlib import Path; import os,signal; Path('result').write_text('killed'); os.kill(os.getpid(),signal.SIGKILL)")
        self.assertTrue(signaled['exit_code']<0 or signaled['exit_code']>=128)
        self.assertIs(state.snapshot,before)
        self.assertIsNone(signaled['attestation']['storage_policy']['extracted'])
        exported = state.export(['result'],self.root/'artifacts',time.monotonic()+5)
        self.assertEqual((exported/'result').read_text(),'final')
        self.assertEqual((self.work/'seed').read_text(),'original'); self.assertTrue((self.work/'empty').is_dir())

    def test_every_writable_volume_enforces_aggregate_capacity_without_leaking_setup_fds(self):
        code = "import errno,os,stat\nfor value in os.listdir('/proc/self/fd'):\n if int(value)>2:\n  try: info=os.fstat(int(value))\n  except OSError: continue\n  assert not stat.S_ISDIR(info.st_mode), 'setup handle leaked'\nfor path,cap in (('/work',32*1024*1024),('/tmp',16*1024*1024),('/dev/shm',16*1024*1024)):\n info=os.statvfs(path)\n assert info.f_blocks*info.f_frsize == cap\n denied=False\n for number in range(128):\n  try:\n   with open(path+'/'+str(number),'wb') as stream: stream.write(b'x'*(512*1024))\n  except OSError as error:\n   assert error.errno == errno.ENOSPC\n   denied=True; break\n assert denied, path+' exceeded aggregate quota'\n assert os.statvfs(path).f_bavail == 0\n for name in os.listdir(path): os.unlink(path+'/'+name)\nprint('all-volumes-bounded')\n"
        run = self.run_code(code)
        self.assert_complete(run)
        self.assertIn('all-volumes-bounded', run['stdout'])
        self.assertTrue(run['attestation']['aggregate_disk_bound_verified'])
        self.assertEqual(list(self.work.iterdir()), [])

    def test_seed_and_only_allowlisted_artifacts_survive_worker_cleanup(self):
        source = self.work/'seed'; source.write_text('original'); source.chmod(0o755)
        (self.work/'empty').mkdir()
        code = "from pathlib import Path\nassert Path('seed').read_text() == 'original'\nassert Path('seed').stat().st_mode & 0o777 == 0o755\nassert Path('empty').is_dir()\nPath('seed').write_text('changed')\nPath('result').write_text('allowed')\nPath('unlisted').write_text('discarded')\n"
        run = self.run_code(code, artifact_allowlist=['seed', 'result'])
        self.assert_complete(run)
        self.assertEqual(source.read_text(), 'changed')
        self.assertEqual(source.stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.work/'result').read_text(), 'allowed')
        self.assertFalse((self.work/'unlisted').exists())
        self.assertEqual(run['attestation']['storage_policy']['extracted'], dict(artifacts=2, bytes=14))

    def test_unsafe_or_oversized_outputs_are_rejected_before_export(self):
        for code in ("os.symlink('/etc/passwd','result')",
                     "Path('original').write_text('x'); os.link('original','result')",
                     "os.mkfifo('result')", "Path('dir').mkdir(); os.symlink('dir','result')",
                     "Path('result').write_bytes(b'x'*(1024*1024+1))"):
            with self.subTest(code=code), self.assertRaises((OSError, ValueError)):
                self.run_code('import os; from pathlib import Path; '+code,
                    artifact_allowlist=['result'], limits=Limits(seconds=5, file_bytes=2*1024*1024))
            self.assertEqual(list(self.work.iterdir()), [])

    def test_failure_timeout_and_interruption_do_not_export_and_leave_no_cgroup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        for suffix, expected in (("raise SystemExit(1)", 'failed'), ("import time; time.sleep(30)", 'timeout')):
            run = self.run_code("from pathlib import Path; Path('result').write_text('untrusted'); "+suffix,
                                artifact_allowlist=['result'], limits=Limits(seconds=.5))
            self.assertEqual(run['status'], expected)
            self.assertFalse((self.work/'result').exists())
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)

    def test_memfd_and_new_tmpfs_cannot_bypass_the_aggregate_storage_ceiling(self):
        filler = "import os; f=open('data','wb'); [f.write(b'x'*65536) for _ in range(2048)]"
        new_mount = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--ro-bind', '/', '/',
                     '--proc', '/proc', '--dev', '/dev', '--size', str(128*1024*1024),
                     '--tmpfs', '/tmp', '--chdir', '/tmp', '--', '/usr/bin/python3', '-c', filler]
        attacks = ["import os; fd=os.memfd_create('bypass'); [os.write(fd,b'x'*65536) for _ in range(2048)]",
                   f"import os; os.execv('/usr/bin/bwrap',{new_mount!r})"]
        (self.work/'seed').write_bytes(b'x'*(1024*1024))
        for code in attacks:
            with self.subTest(code=code):
                run = self.run_code(code, limits=Limits(seconds=5, file_bytes=128*1024*1024))
                self.assertEqual(run['status'], 'resource_limit', run)
                controls = run['attestation']['cgroup_v2']
                self.assertEqual(run['attestation']['storage_policy']['controller_seeded_bytes'], 1024*1024)
                self.assertEqual(int(controls['controls']['memory.max']), 63*1024*1024)
                self.assertGreater(controls['events']['memory.events']['oom_kill'], 0)
                self.assertTrue(controls['empty_after_cleanup'])

    def test_capability_verifies_the_storage_policy(self):
        result = self.backend.capability()
        self.assertEqual(result['status'], 'available', result)
        self.assertTrue(result['storage_policy']['verified'])
        self.assertNotIn('no aggregate disk quota', result['limitations'])

    def test_hugetlb_memfd_mapping_and_shared_memory_are_denied(self):
        code = "import ctypes,errno,mmap,os\ntry: os.memfd_create('hugetlb-probe',os.MFD_HUGETLB)\nexcept OSError as error: assert error.errno == errno.EPERM\nelse: raise AssertionError('HugeTLB memfd allowed')\nlibc=ctypes.CDLL(None,use_errno=True)\nlibc.mmap.restype=ctypes.c_void_p\nmapped=libc.mmap(None,2*1024*1024,3,mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS|0x40000,-1,0)\nassert mapped == ctypes.c_void_p(-1).value and ctypes.get_errno() == errno.EPERM\nassert libc.shmget(0,2*1024*1024,0o600|0o1000|0o4000) == -1\nassert ctypes.get_errno() == errno.EPERM\nprint('hugetlb-denied')\n"
        run = self.run_code(code, limits=Limits(seconds=5, file_bytes=4*1024*1024))
        self.assert_complete(run)
        self.assertIn('hugetlb-denied', run['stdout'])
        self.assertEqual(run['attestation']['storage_policy']['hugetlb_policy']['name'], 'seccomp-deny-hugetlb-v1')

    def test_alternate_syscall_numbers_cannot_bypass_the_storage_filter(self):
        code = "import ctypes; ctypes.CDLL(None).syscall(0x4000013f,0,4); raise AssertionError('alternate syscall returned')"
        run = self.run_code(code)
        self.assertEqual(run['status'], 'failed', run)
        self.assertEqual(run['stderr'], '')
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_startup_verification_and_tiny_budget_fail_closed_and_cleanup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        with patch('agent_harness_lab.evaluation.workspace.VERIFY', "raise RuntimeError('synthetic startup failure')"):
            with self.assertRaises(ValueError): self.run_code("raise AssertionError('worker must not start')")
        (self.work/'seed').write_bytes(b'x'*(5*1024*1024))
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root, disk_bytes=16384)
        with self.assertRaises((OSError, ValueError)): self.run_code("raise AssertionError('worker must not start')")
        self.assertEqual((self.work/'seed').stat().st_size, 5*1024*1024)
        self.assertEqual({p.name for p in self.cgroup_root.iterdir() if p.is_dir()}, before)


@unittest.skipUnless(ENABLED, 'requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class ResourceKernelTests(KernelFixture):
    def setUp(self):
        super().setUp()
        # Test cgroup enforcement independently from namespace containment.
        command = patch.object(self.backend, 'command', side_effect=lambda argv, *args: argv)
        command.start(); self.addCleanup(command.stop)

    def test_aggregate_memory_exhaustion_is_recorded_and_tree_emptied(self):
        code = '''import os,time
for _ in range(2):
 if os.fork() == 0:
  buffer=bytearray(80*1024*1024)
  for offset in range(0,len(buffer),4096): buffer[offset]=1
  time.sleep(30)
  os._exit(0)
time.sleep(30)
'''
        run = self.run_code(code, limits=Limits(seconds=5, memory_bytes=128*1024*1024, processes=16))
        self.assertGreater(run['attestation']['cgroup_v2']['events']['memory.events']['oom_kill'], 0)
        self.assertEqual(run['status'], 'resource_limit')
        self.assertTrue(run['attestation']['cgroup_v2']['empty_after_cleanup'])

    def test_fork_limit_and_cleanup_include_children_that_create_sessions(self):
        code = '''import os,time
pids=[]
for _ in range(20):
 try: pid=os.fork()
 except OSError: break
 if pid == 0:
  os.setsid(); os.close(1); os.close(2); time.sleep(30); os._exit(0)
 pids.append(pid)
print(*pids,flush=True)
'''
        run = self.run_code(code, limits=Limits(seconds=5, processes=6))
        self.assertEqual(run['status'], 'resource_limit')
        self.assertGreater(run['attestation']['cgroup_v2']['events']['pids.events']['max'], 0)
        for pid in run['stdout'].split():
            path = Path('/proc')/pid/'stat'
            if path.exists():
                try: self.assertEqual(path.read_text().split(') ')[1].split()[0], 'Z')
                except FileNotFoundError: pass
