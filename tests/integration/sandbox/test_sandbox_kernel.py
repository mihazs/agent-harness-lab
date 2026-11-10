"""Opt-in real kernel boundaries; synthetic fixtures, no inference or credentials."""
from contextlib import ExitStack, contextmanager
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

from tests.support.optional_pytest import pytest

import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import regrade_artifacts
from agent_harness_lab.evaluation.cgroup import delegated
from agent_harness_lab.evaluation.integrity import artifacts, digest
from agent_harness_lab.evaluation.sandbox import Limits, SandboxBackend
from agent_harness_lab.evaluation.image import MOUNT_DIRS, scan
from tests.integration.evaluation.test_offline_evaluation import OfflineFixture
from tests.support.unittest_cleanups import run_cleanups

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


@contextmanager
def kernel_scope():
    """The delegated cgroup subtree after checking that the intended package layout is imported."""
    expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected and not Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()):
        raise AssertionError('kernel canaries imported the wrong package layout')
    with kernel_subtree() as root:
        yield root


def assert_complete(run):
    assert run['status'] == 'completed', run['stderr']
    assert run['exit_code'] == 0, run['stderr']
    assert run['inputs_unchanged']
    assert run['attestation']['cgroup_v2']['empty_after_cleanup']
    assert not run['attestation']['automatic_promotion']


class KernelHelpers:
    def run_code(self, code, *, inputs=None, role='executor', limits=Limits(seconds=5), **options):
        return self.backend.run(['/usr/bin/python3', '-c', code], self.work, inputs or {}, role, limits, **options)

    def assert_complete(self, run):
        assert_complete(run)


class KernelFixture(KernelHelpers, unittest.TestCase):
    """unittest host of the kernel scope for the other kernel modules' cases; it defines no tests.

    Those modules subclass it, and two of them call KernelCase methods below with an instance as `self`,
    so KernelCase keeps this interface: root, work, backend, cgroup_root, run_code, assert_complete and
    addCleanup.
    """
    @classmethod
    def setUpClass(cls):
        cls.cgroup_root = cls.enterClassContext(kernel_scope())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root/'work'; self.work.mkdir()
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root)


@pytest.fixture(scope='module')
def cgroup_root():
    with kernel_scope() as root:
        yield root


class KernelCase(KernelHelpers):
    """pytest host of one test's work directory and backend inside the module's delegated cgroup subtree."""
    backend_options = {}

    @pytest.fixture(autouse=True)
    def _kernel(self, cgroup_root, tmp_path):
        self.cgroup_root = cgroup_root
        self.root = tmp_path
        self.work = self.root/'work'; self.work.mkdir()
        self.backend = SandboxBackend(cgroup_root=cgroup_root, **self.backend_options)
        with ExitStack() as stack:
            self.addCleanup = stack.callback
            self.prepare()
            yield

    def prepare(self):
        """Hook run after the backend exists and before the test."""


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


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestNamespaceKernel(KernelCase):
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
        assert grade['status'] == 'failed'
        assert grade['outcome'] is False

    def test_capability_with_real_aggregate_controls(self):
        result = self.backend.capability()
        assert result['status'] == 'available', result
        assert result['cgroup_v2'] is not None
        assert not result['automatic_promotion']

    @pytest.mark.parametrize('role', ['proposer', 'evaluator_author', 'executor', 'grader'])
    def test_roles_cannot_read_forbidden_files_or_mutate_readonly_inputs(self, role):
        (self.root/'forbidden').write_text('SYNTHETIC_FORBIDDEN')
        harness, fixture, oracle = (self.root/name for name in ('harness', 'fixture', 'oracle'))
        for path in (harness, fixture, oracle):
            path.mkdir(); (path/'marker').write_text('SYNTHETIC_INPUT')
        with tempfile.TemporaryDirectory(prefix='ahl-canary-', dir=Path.home()) as home:
            secret = Path(home)/'canary'; secret.write_text('SYNTHETIC_HOME')
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
            with patch.dict(os.environ, AHL_TEST_SECRET='SYNTHETIC_ENV'):
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
            with pytest.raises(ValueError): artifacts(self.work, [name])

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
        assert grade['status'] == 'failed'
        assert grade['outcome'] is False

    def test_output_timeout_and_interruption_leave_no_worker_group(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        flood = self.run_code("print('x'*100000)", limits=Limits(seconds=3, output_bytes=1024))
        assert flood['status'] == 'output_limit'
        timeout = self.run_code('import time; time.sleep(30)', limits=Limits(seconds=.5))
        assert timeout['status'] == 'timeout'
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        assert {p.name for p in self.cgroup_root.iterdir() if p.is_dir()} == before


class OfflineKernel(KernelCase):
    """KernelCase plus the offline evaluation fixture; self.root is that fixture's directory."""
    def prepare(self):
        self.f = OfflineFixture(); self.f.setUp(); self.addCleanup(run_cleanups, self.f)
        self.root = self.f.root


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestOfflineKernel(OfflineKernel):
    def test_both_arms_of_all_starter_tasks_use_real_executor_and_pristine_grader(self):
        self.f.args.cgroup_root = self.cgroup_root
        assert self.f.runner.evaluate(self.f.args) == 1
        result = self.f.results()
        assert len(result['trials']) == 14
        assert result['inputs_unchanged']
        assert not result['automatic_promotion']
        for trial in result['trials']:
            assert trial['status'] == ('passed' if trial['version'] == 'candidate' else 'failed')
            assert trial['grader']['outcome'] is (trial['version'] == 'candidate')
            for run in (trial['execution'], trial['grader']['execution']):
                assert run['attestation']['backend'] == 'bubblewrap-offline-v1'
                assert run['attestation']['cgroup_v2']['empty_after_cleanup']


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestPinnedImageKernel(OfflineKernel):
    def test_oracle_history_seals_real_arms_and_quarantines_shared_grader_dependencies(self):
        from dataclasses import asdict, replace
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        tasks = [self.f.manifest['tasks'][0],self.f.manifest['tasks'][-1]]
        self.f.manifest['tasks'] = tasks
        self.f.manifest['grader_bundles'] = {t['id']:self.f.manifest['grader_bundles'][t['id']] for t in tasks}
        plan = ExperimentSpec('synthetic-history',tuple((t['id'],t['root'],t['family']) for t in tasks),
            1,13,4,2,.05,.1,.05,tuple((t['family'],.5) for t in tasks),
            self.f.runner.digest(self.root/'baseline'),self.f.runner.digest(self.root/'candidate'),
            identity(self.root/'export',tasks,self.f.manifest['grader_bundles']),frozen['sha256'])
        self.f.manifest['experiment'] = asdict(plan); self.f.write_manifest()
        history = HistoryStore(self.root/'history')
        options = dict(cgroup_root=self.cgroup_root,image_root=rootfs,image_sha256=frozen['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=60)
        old = history.run(self.f.args.manifest,self.f.args.baseline,self.f.args.candidate,**options)
        assert old['state'] == 'sealed'; assert old['oracle_validity'] == 'current'
        report = history.case(old['run_id'])/'execution/results.json'; original = report.read_bytes()
        bundle = self.f.manifest['grader_bundles'][tasks[0]['id']]
        decision = dict(schema_version=1,bundle_sha256=bundle['digest'],
            expected_event_sha256=history.oracle(bundle['digest'])['event_sha256'],
            operator=dict(identity='synthetic-curator',source='operator_attestation',
                provenance='synthetic kernel canary',basis='Exercise oracle dependency quarantine.'),
            evidence_refs=[dict(ref='synthetic:kernel-history',sha256='a'*64)],reason='Synthetic quarantine.')
        record = self.root/'quarantine.json'; record.write_text(json.dumps(decision)); history.quarantine(record)
        assert history.analyze(old['run_id'])['reason'] == 'oracle_quarantined'
        assert report.read_bytes() == original
        reference = self.root/'export'/bundle['path']/'expected.json'; reference.write_text(reference.read_text()+'\n')
        bundle['digest'] = digest(reference.parent)
        plan = replace(plan,grader_hash=identity(self.root/'export',tasks,self.f.manifest['grader_bundles']))
        self.f.manifest['experiment'] = asdict(plan); self.f.write_manifest()
        new = history.run(self.f.args.manifest,self.f.args.baseline,self.f.args.candidate,**options)
        assert new['state'] == 'sealed'; assert new['oracle_validity'] == 'current'
        observed = json.loads((history.case(new['run_id'])/'execution/results.json').read_text())
        assert len(observed['trials']) == 4
        assert {t['version'] for t in observed['trials']} == {'baseline','candidate'}
        assert history.show(old['run_id'])['oracle_validity'] == 'invalidated'
        assert new['oracle_review'] == 'unverified'; assert new['independence'] == 'unverified'

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
        assert run['attestation']['runtime_image']['verified_after']
        self.f.args.cgroup_root = self.cgroup_root
        self.f.args.image_root, self.f.args.image_sha256 = rootfs, frozen['sha256']
        self.f.args.disk_bytes = 64*1024*1024
        assert self.f.runner.evaluate(self.f.args) == 1
        result = self.f.results()
        assert len(result['trials']) == 14
        for trial in result['trials']:
            assert trial['status'] == ('passed' if trial['version'] == 'candidate' else 'failed')
            for execution in (trial['execution'], trial['grader']['execution']):
                assert execution['attestation']['aggregate_disk_bound_verified']
                attested = execution['attestation']['runtime_image']
                assert attested['sha256'] == frozen['sha256']
                assert attested['verified_before'] and attested['verified_after']


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestRegressionDraftKernel(KernelCase):
    def test_preservation_qualification_uses_real_prior_and_fresh_pinned_workers(self):
        from dataclasses import replace
        from tests.integration.evaluation.test_preservation_cohorts import PreservationCohort
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        fixture=PreservationCohort(self.addCleanup)
        value=fixture.fixture; runtime=self.root/'runtime'; pinned=pinned_runtime(runtime)
        value.image_hash=pinned['sha256']; value.install_plan()
        profile=dict(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=60)
        prior=value.store.run(value.export/'manifest.json',value.baseline,value.candidate,**profile)
        assert prior['state'] == 'sealed', prior
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
        assert observed['state'] == 'sealed', observed
        assert observed['cohort_qualification'] == 'current_membership_only'
        analysis=value.store.analyze(observed['run_id'])
        assert analysis['reason'] == 'uncertainty_or_margin_not_met'
        assert analysis['preservation']['cohorts']['preserved']['endpoint']['mean'] == 0
        receipt=value.store.decision(observed['run_id'])
        assert receipt['analysis'] == analysis
        assert value.store.verify_decision(observed['run_id'],receipt['receipt_sha256']) == receipt
        assert not receipt['adoption_authority']
        from agent_harness_lab.optimization.history_evidence import HistorySearchContext
        from agent_harness_lab.optimization.search import CandidateRecord
        context=HistorySearchContext(value.store.root,value.baseline,value.export/'manifest.json',
            dict(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
                 disk_bytes=64*1024*1024,budget_seconds=45,work_root=self.root/'prepared-search'),['solve.py'])
        control=CandidateRecord('baseline',(),'synthetic-kernel-control',{},('synthetic:pinned-workers',),
            'Observe a frozen no-op comparison','Any changed input invalidates the result')
        checked=context.evaluate(control,stage='baseline',event_id='synthetic:kernel-search')
        assert not checked['qualified_feasible']
        assert checked['retain_no_op']
        assert context.verify(checked['binding']) == checked
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
        assert len(fresh['phases']) == 1
        fresh_run=fresh['phases'][0]['run_id']
        assert fresh_run not in (prior['run_id'],observed['run_id'])
        assert fresh['manifest_sha256'] != pin
        for run_id in (prior['run_id'],observed['run_id'],fresh_run):
            trials=value.store.checked(run_id)[4]['trials']; assert len(trials) == 4
            for trial in trials:
                for execution in (trial['execution'],trial['grader']['execution']):
                    image=execution['attestation']['runtime_image']
                    assert image['sha256'] == pinned['sha256']
                    assert image['verified_before'] and image['verified_after']

    def test_adversarial_final_effects_are_sealed_and_veto_the_candidate(self):
        from tests.integration.evaluation.test_oracle_behavior import fixture
        value=fixture(self,all_tasks=True,
            candidate_suffix="Path('sink.txt').write_text(Path('canary.txt').read_text())\n")
        runtime=self.root/'runtime'; pinned=pinned_runtime(runtime)
        value.manifest['experiment']['runtime_hash']=pinned['sha256']; value.write_manifest()
        completed=value.store.run(value.args.manifest,value.args.baseline,value.args.candidate,
            cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pinned['sha256'],
            disk_bytes=64*1024*1024,budget_seconds=75)
        assert completed['state'] == 'sealed', completed
        result=value.store.analyze(completed['run_id'])
        assert (result['status'],result['reason']) == ('rejected','critical_regression')
        report=result['behavior']; assert not report['required_observation_unknown']
        assert sum(f['candidate']['critical_trials'] for f in report['families'].values()) == 14
        for family in report['families'].values():
            for arm,counts in family.items():
                assert counts['task_completed']['true'] == counts['trials']
                assert counts['effects']['canary_exported']['true' if arm=='candidate' else 'false'] == counts['trials']
        assert report['unobserved']['model_compliance'] == 'unknown'

    def test_grader_correction_audits_real_controls_without_a_behavioral_failure_claim(self):
        from tests.integration.evaluation.test_grader_correction import GraderCorrection
        fixture = GraderCorrection(self.addCleanup)
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        fixture.spec_value['image_sha256'] = frozen['sha256']; fixture.write_spec()
        parent = fixture.bank.read_bytes(); proposed = fixture.propose()
        backend = SandboxBackend(cgroup_root=self.cgroup_root,image_root=rootfs,
            image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
        audited = fixture.store.audit(proposed['case_id'],backend)
        assert audited['state'] == 'audited', audited
        assert audited['old_audit_status'] == 'controls_failed'
        assert audited['replacement_audit_status'] == 'grader_controls_passed'
        reports = [json.loads(p.read_text()) for p in fixture.store.root.rglob('review.json')]
        old = next(r for r in reports if r['status']=='controls_failed')
        new = next(r for r in reports if r['status']=='grader_controls_passed')
        assert next(c for c in old['checks'] if c['control']=='complete')['observed']
        assert not next(c for c in old['checks'] if c['control']=='alternative_valid')['observed']
        assert next(c for c in new['checks'] if c['control']=='alternative_valid')['observed']
        assert not next(c for c in new['checks'] if c['control']=='superficial')['observed']
        for report in reports:
            for check in report['checks']:
                assert check['attestation']['runtime_image']['sha256'] == frozen['sha256']
                assert check['attestation']['cgroup_v2']['empty_after_cleanup']
        reviewed = fixture.store.review(fixture.case_id,fixture.review_file())
        assert reviewed['state'] == 'reviewed'; assert fixture.bank.read_bytes() == parent
        assert not reviewed['task_failure_verified']; assert not reviewed['bank_replacement']

    def test_grader_correction_applies_and_reruns_entire_pair_with_real_pinned_workers(self):
        from dataclasses import asdict
        from agent_harness_lab.cli import evaluator
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.plan import ExperimentSpec,schedule
        from tests.integration.evaluation.test_grader_correction import GraderCorrection
        fixture = GraderCorrection(self.addCleanup)
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        fixture.spec_value['image_sha256'] = frozen['sha256']; fixture.write_spec()
        proposed = fixture.propose()
        profile = dict(cgroup_root=self.cgroup_root,image_root=rootfs,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024,budget_seconds=60)
        backend = SandboxBackend(**{k:v for k,v in profile.items() if k!='budget_seconds'})
        audited = fixture.store.audit(proposed['case_id'],backend); assert audited['state'] == 'audited', audited
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
        prior = history.run(source,baseline,candidate,**profile); assert prior['state'] == 'sealed', prior
        reviewed = fixture.store.show(fixture.case_id)
        operator = dict(identity='synthetic-kernel-curator',source='operator_attestation',provenance='synthetic real-kernel canary',basis='Explicit synthetic reviewed replacement')
        request = dict(schema_version=1,case_id=fixture.case_id,expected_event_sha256=reviewed['event_sha256'],evidence_sha256=reviewed['evidence_sha256'],
            parent_bank_sha256=hashlib.sha256(fixture.bank.read_bytes()).hexdigest(),parent_revision_sha256=None,
            history_store=str(history.root),expected_oracle_event_sha256=history.oracle(fixture.task['grader_digest'])['event_sha256'],operator=operator,reason='Synthetic kernel application')
        application = root/'apply.json'; application.write_text(json.dumps(request))
        applied = fixture.store.apply(fixture.case_id,fixture.bank,history,application)
        assert history.show(prior['run_id'])['oracle_validity'] == 'invalidated'
        original = history.historical_plan(prior['run_id']); current = fixture.store.show(fixture.case_id)
        request = dict(schema_version=1,case_id=fixture.case_id,expected_event_sha256=current['event_sha256'],evidence_sha256=current['evidence_sha256'],
            bank_sha256=applied['bank_manifest_sha256'],revision_sha256=applied['revision_sha256'],history_store=str(history.root),
            prior={k:original[k] for k in ('run_id','event_sha256','inputs_sha256','plan_sha256','evidence_sha256')},operator=operator,reason='Fresh real paired execution')
        rerun = root/'rerun.json'; rerun.write_text(json.dumps(request))
        result = fixture.store.rerun(fixture.case_id,rerun,baseline,candidate,**profile)
        assert result['rerun_status'] == 'completed', result
        actual = history.historical_plan(result['new_run_id']); observed = ExperimentSpec.parse(actual['plan'])
        expected = asdict(spec); expected['grader_hash'] = observed.grader_hash
        assert asdict(observed) == expected
        assert {t['trial_id'] for t in schedule(spec)}.isdisjoint(t['trial_id'] for t in schedule(observed))
        assert history.show(result['new_run_id'])['oracle_validity'] == 'current'
        analysis = history.analyze(result['new_run_id']); assert analysis['reason'] == 'insufficient_independent_roots'
        assert not result['automatic_promotion']; assert not result['task_failure_verified']
        assert result['historical_state_reproduction'] == 'unverified'; assert result['harness_mode_evidence'] == 'unknown'
        rows = json.loads((history.case(result['new_run_id'])/'execution/results.json').read_text())['trials']
        assert len(rows) == 4
        for row in rows:
            assert row['status'] == 'passed'
            for run in (row['execution'],row['grader']['execution']):
                assert run['attestation']['cgroup_v2']['empty_after_cleanup']
                assert run['attestation']['runtime_image']['sha256'] == frozen['sha256']

    def test_regression_review_lifecycle_seals_real_replay_with_operator_attestation_and_bank_authority(self):
        from tests.integration.evaluation.test_regression_replay import make_replay, SOLVER
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
        assert reproduced['state'] == 'reproduced', reproduced
        prepared = store.draft(reported['case_id'])
        review = dict(schema_version=1, case_id=reported['case_id'], expected_event_sha256=prepared['event_sha256'],
            evidence_sha256=prepared['evidence_sha256'], decision='reviewed',
            reviewer=dict(identity='synthetic-reviewer', source='operator_attestation', provenance='synthetic kernel fixture',
                basis_kind='independent_artifact_review', basis='Synthetic operator assertion; no authenticated human-independence claim.'),
            evidence_refs=[dict(ref='synthetic:kernel-review', sha256='a'*64)], reason='Exercise the operator review contract.')
        path = self.root/'operator-review.json'; path.write_text(json.dumps(review))
        reviewed = store.review(reported['case_id'], path)
        assert reviewed['state'] == 'reviewed'; assert reviewed['review_independence'] == 'operator_attested'
        assert reviewed['historical_verification'] == 'unverified'; assert not reviewed['bank_admission']
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
        assert accepted['current_eligible']; assert not validate(bank)['certification_eligible']
        assert parent.read_bytes() == original; assert store.show(reported['case_id'])['state'] == 'accepted'
        from dataclasses import asdict
        from tests.integration.evaluation.test_offline_evaluation import SOLVER as comparison_solver
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
        assert observed['state'] == 'sealed'; assert observed['bank_authority'] == 'current_eligible'
        assert observed['oracle_validity'] == 'current'
        result = history.case(observed['run_id'])/'execution/results.json'; frozen_result = result.read_bytes()
        trials = json.loads(frozen_result)['trials']; assert len(trials) == 4
        assert {t['version'] for t in trials} == {'baseline','candidate'}
        for trial in trials:
            assert trial['grader']['outcome'] == (trial['version']=='candidate')
            assert trial['grader']['execution']['attestation']['runtime_image']['sha256'] == frozen['sha256']
        current = store.show(reported['case_id'])
        review.update(expected_event_sha256=current['event_sha256'],decision='quarantined')
        path.write_text(json.dumps(review)); store.review(reported['case_id'],path)
        with pytest.raises(ValueError): history.show(observed['run_id'])
        with pytest.raises(ValueError): history.analyze(observed['run_id'])
        assert result.read_bytes() == frozen_result

    def test_regression_replay_uses_real_matched_snapshot_views_and_hidden_oracle(self):
        from tests.integration.evaluation.test_regression_replay import make_replay, SOLVER
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
        assert report['status'] == 'replay_completed', report
        assert report['reproduction_disposition'] == 'reproduced'
        assert report['task_trials'] == 8
        assert report['repair_observed']; assert report['aa_observed_agreement']
        assert not report['task_failure_verified']; assert not report['bank_admission']

    def test_regression_draft_controls_use_real_pinned_graders_and_private_quotas(self):
        from tests.integration.evaluation.test_regression_controls import make_draft
        from agent_harness_lab.evaluation.regression_controls import audit
        rootfs = self.root/'runtime'; frozen = pinned_runtime(rootfs)
        draft_root = self.root/'draft'; draft_root.mkdir()
        source, draft = make_draft(draft_root, frozen['sha256'])
        backend = SandboxBackend(cgroup_root=self.cgroup_root, image_root=rootfs,
            image_sha256=frozen['sha256'], disk_bytes=64*1024*1024)
        report = audit(source, draft, self.root/'review', backend)
        assert report['status'] == 'grader_controls_passed', report
        assert [c['observed'] for c in report['checks']] == [False, False, True, True, False]
        for control in report['checks']:
            assert control['attestation']['aggregate_disk_bound_verified']
            assert control['attestation']['cgroup_v2']['empty_after_cleanup']
            assert control['attestation']['runtime_image']['sha256'] == frozen['sha256']
        assert not report['task_failure_verified']; assert not report['bank_admission']


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel:roles')
class TestControlledWorkflowKernel(KernelCase):
    def test_canonical_construction_access_uses_actual_workers_and_same_store_assets(self):
        from agent_harness_lab import workflow
        from agent_harness_lab.evaluation.oracle_history import HistoryStore
        from agent_harness_lab.evaluation.oracle import identity
        from agent_harness_lab.evaluation.plan import ExperimentSpec
        improve=workflow.improve; execute=SandboxBackend.run
        selected=self.root/'private-evaluation-store'; results=[]; runs=[]
        def configured(args):
            args.evaluation_store=selected
            result=improve(args); results.append((result,args.image_sha256))
            return result
        def observed(backend,argv,workspace,inputs,role,limits,**options):
            for path in [Path(workspace),*map(Path,inputs.values())]:
                assert not (path.is_relative_to(selected) or selected.is_relative_to(path))
            result=execute(backend,argv,workspace,inputs,role,limits,**options)
            if argv[0]=='/usr/bin/python3': runs.append((role,result))
            return result
        with patch.object(workflow,'improve',side_effect=configured),patch.object(SandboxBackend,'run',new=observed):
            self.test_controlled_workflow_role_views_deny_source_key_candidate_and_prior_heldout_reads()
        result,image_sha=results[0]
        assert len(runs) == 23
        assert [role for role,_ in runs].count('proposer') == 1
        assert [role for role,_ in runs].count('evaluator_author') == 1
        assert [role for role,_ in runs].count('executor') == 6
        assert [role for role,_ in runs].count('grader') == 15
        for _,run in runs:
            assert run['inputs_unchanged']
            assert run['attestation']['cgroup_v2']['empty_after_cleanup']
            assert run['attestation']['runtime_image']['sha256'] == image_sha
        observed=result['construction_access']
        assert observed['qualification'] == 'unverified'; assert not observed['publication_allowed']
        assert all(row['scientific_parentage']=='unknown' for row in observed['stages'])
        output=Path(result['output']); path=output/'preparation/evaluation-0/manifest.json'
        source=json.loads(path.read_bytes())
        for task in source['tasks']: task.update(root='one-correlated-synthetic-construction',family='kernel-construction')
        baseline=output/'preparation/baseline'; candidate=output/'candidate-0'
        from agent_harness_lab.cli import evaluator
        runner=evaluator()
        source['experiment']=ExperimentSpec('synthetic-kernel-canonical',tuple((t['id'],t['root'],t['family']) for t in source['tasks']),
            1,0,6,2,0,0,.05,(('kernel-construction',1),),runner.digest(baseline),runner.digest(candidate),
            identity(path.parent,source['tasks'],source['grader_bundles']),image_sha).to_dict()
        path.write_text(json.dumps(source))
        with pytest.raises(ValueError, match='may-derived'):
            HistoryStore(selected).reserve_access(path,baseline,candidate)

    def test_controlled_workflow_role_views_deny_source_key_candidate_and_prior_heldout_reads(self):
        from tests.integration.sandbox.test_sandbox_construction import ControlledWorkflowTests, PROPOSER, AUTHOR, EXECUTOR, wire, workflow, responses
        fixture = ControlledWorkflowTests(); fixture.setUp(); self.addCleanup(run_cleanups, fixture)
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
            assert key == 'SYNTHETIC_CONTROLLER_KEY'; assert key not in body.decode()
            payload = json.loads(body); fixture.histories.append(payload); ident = str(len(fixture.histories))
            if any(item.get('type')=='function_call_output' for item in payload['input']): return wire.reply([wire.message('final-'+ident)])
            instructions = payload['input'][0]['content']; output = fixture.args._run_output
            hidden = output/'private-heldout'; hidden.mkdir(exist_ok=True); (hidden/'canary').write_text('SYNTHETIC_HELDOUT')
            forbidden = [fixture.key,fixture.project/'AGENTS.md',home_secret,hidden/'canary']
            if 'You are the proposer' in instructions:
                argv = ['/usr/bin/python3','-c',probe+PROPOSER,'good']
            elif 'You are the evaluator author' in instructions:
                forbidden += [output/'candidate-0/AGENTS.md',output/'proposer-0/proposal.json']
                assert 'SYNTHETIC_CANDIDATE_SECRET' not in body.decode()
                argv = ['/usr/bin/python3','-c',probe+"assert pathlib.Path('/inputs/harness/AGENTS.md').read_text() == 'bad'\n"+AUTHOR,'1','complete']
            else: argv = ['/usr/bin/python3','-c',probe+EXECUTOR,'/inputs/harness']
            argv.append(json.dumps(list(map(str,forbidden))))
            return wire.reply([wire.call(ident,arguments=json.dumps(dict(argv=argv)))])
        with patch('agent_harness_lab.cli.evaluator',return_value=fixture.runner), \
             patch.object(responses,'request',side_effect=transport), \
             patch.object(fixture.runner,'execute',side_effect=AssertionError('no native host execution')), \
             patch.dict(os.environ,AHL_SYNTHETIC_BROKER_KEY='SYNTHETIC_ENV_KEY'):
            result = workflow.improve(fixture.args)
        assert result['status'] == 'inconclusive', result
        assert result['inference']['request_attempts'] == 16
        assert (fixture.project/'AGENTS.md').read_text() == 'bad'
        ledger = json.loads((Path(result['output'])/'inference.json').read_text())
        assert not ledger['request_pending']; assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(ledger)
        evaluated=json.loads((Path(result['output'])/'evaluation-0/results.json').read_text())
        for trial in evaluated['trials']:
            report=trial['controller_actions']; assert not report['publication_allowed']
            emission,=[row for row in report['actions'] if row['kind']=='assistant_emission']
            assert emission['protocol_validated']
            assert emission['response_disposition'] == 'validated'
            assert emission['external_delivery'] == 'unknown'
            assert emission['observation_scope'] == 'controller_transport_text_only'
            assert len(emission['content']['text'].encode('utf-8')) <= 8192


def reject_swaps_and_replays(case, modes):
    """Every mode in order on one backend; `case` is a KernelCase or a foreign KernelFixture."""
    from copy import copy
    from agent_harness_lab.evaluation import controller_receipts as owned
    from agent_harness_lab.sources.controller import ControllerCapture, SourceSink
    from agent_harness_lab.sources.execution import ExecutionIdentity, ExecutionMetadata, IdentityField
    from agent_harness_lab.sources.schema import digest as source_digest
    runtime=case.root/'runtime'; frozen=pinned_runtime(runtime)
    backend=SandboxBackend(cgroup_root=case.cgroup_root,image_root=runtime,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
    oracle=case.root/'oracle'; oracle.mkdir()
    (oracle/'expected.json').write_text('{}')
    (oracle/'grade.py.txt').write_text("import json,pathlib,sys; ok=(pathlib.Path(sys.argv[1])/'result.txt').read_text()=='done'; "
        "print(json.dumps(dict(schema_version=1,valid=True,outcome=ok,grader_id='synthetic-source',grader_version='v1'))); sys.exit(0 if ok else 1)")
    oracle_hash=digest(oracle)
    argv=['/usr/bin/python3','-I','-c',"from pathlib import Path; Path('result.txt').write_text('done')"]
    for mode in modes:
        with owned.session():
            sink=SourceSink(case.root/(mode+'-journal.json'),source_digest('synthetic-project'),source_digest('synthetic-scope'))
            capture=sink.begin(['result.txt'],time.monotonic()+15)
            run=backend.run(argv,case.work,{},'executor',Limits(seconds=8),artifact_allowlist=['result.txt'])
            case.assert_complete(run)
            if mode=='artifact_swap': (case.work/'result.txt').write_text('swapped')
            if mode=='missing_result':
                with pytest.raises(ValueError): backend.run(argv,case.work,{},'executor',Limits(seconds=0),artifact_allowlist=['result.txt'])
            regrade_artifacts(case.work,['result.txt'],oracle,oracle_hash,backend,limits=Limits(seconds=8))
            if mode=='mutation': capture.data['grade']['oracle_sha256']='0'*64
            if mode=='input_digest': capture.data['kernels'][0]['input_digests']['harness']='0'*64
            if mode=='nonce': capture.data['attempt_sha256']='0'*64
            if mode=='copy': capture=copy(capture)
            if mode=='foreign_scope': sink=SourceSink(case.root/'foreign-journal.json',source_digest('foreign'),source_digest('synthetic-scope'))
            if mode=='deadline':
                with patch.object(owned.time,'monotonic',return_value=capture.deadline+1): result=sink.finish(capture,True)
            else: result=sink.finish(capture,mode!='input_changed')
            assert result['status'] == ('observed' if mode=='valid' else 'unknown'), result
            if mode=='valid':
                snapshot=ControllerCapture(sink.source,capture).read(sink.source)
                live=result['live_verification']
                assert live['controller_harness_input']['status'] == 'unknown'
                assert live['controller_harness_input']['sha256'] is None
                identity=live['verified_execution']; fields=tuple(IdentityField.parse(field) for field in identity['fields'])
                declared=snapshot.metadata.identities[0]
                forged=ExecutionMetadata((ExecutionIdentity(declared.subject,fields),),snapshot.metadata.coverage)
                with pytest.raises(ValueError): forged.validate(sink.source,snapshot.records)
                raw=json.loads(json.dumps(capture.read()))
                with pytest.raises(ValueError): ControllerCapture(sink.source,raw).read(sink.source)
                altered=copy(capture); altered.data=dict(capture.data)
                with pytest.raises(ValueError): ControllerCapture(sink.source,altered).read(sink.source)
                capture.data['attempt_sha256']='f'*64
                with pytest.raises(ValueError): ControllerCapture(sink.source,capture).read(sink.source)
                assert sink.store.path.exists()
                assert all(field.status=='declared' for item in sink.store.capture(sink.source).identities for field in item.fields)
            else: assert not sink.store.path.exists()
        if mode=='valid':
            with pytest.raises(ValueError): capture.read()


SOURCE_MODES = ('valid', 'artifact_swap', 'copy', 'mutation', 'input_digest', 'nonce', 'missing_result', 'deadline',
                'input_changed', 'foreign_scope')


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestTrajectoryKernel(KernelCase):
    def test_trajectory_assertions_use_real_workers_and_independent_artifact_grades(self, trajectory):
        from dataclasses import replace
        from agent_harness_lab.evaluation import responses
        fixture = trajectory
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
            assert fixture.f.runner.evaluate(args) == 1
        fixture.f.backend.run.assert_not_called()
        assert workers.call_count == 9  # One real capability probe plus eight trial workers.
        assert [call.args[3] for call in workers.call_args_list].count('executor') == 4
        graders = [call for call in workers.call_args_list if call.args[3]=='grader']
        assert sum(set(call.args[2])=={'fixture','oracle'} for call in graders) == 4
        assert sum(call.args[0]==['/usr/bin/true'] and call.args[2]=={} for call in graders) == 1
        result = fixture.fixture.results()
        assert fixture.f.requests == 8
        assert result['trajectory_analysis']['roots'] == 2
        assert result['trajectory_analysis']['trials'] == 4
        assert result['trajectory_analysis']['oracle_validity'] == 'unverified'
        assert result['trajectory_analysis']['native_identity'] == 'unverified'
        assert not result['trajectory_analysis']['publication_allowed']
        assert not result['trajectory_analysis']['automatic_promotion']
        for trial in result['trials']:
            assert trial['status'] == ('passed' if trial['version']=='candidate' else 'failed')
            grade = trial['grader']['execution']
            assert grade['attestation']['cgroup_v2']['empty_after_cleanup']
            assert grade['attestation']['runtime_image']['sha256'] == frozen['sha256']
        for trial in fixture.controlled_trials(result):
            grade = trial['controller_grade']; receipt = grade['trajectory']
            assert receipt['trial_id'] == trial['trial_id']
            assert receipt['status'] == 'satisfied'; assert receipt['closed']
            assert grade['valid']; assert grade['outcome'] == trial['grader']['outcome']
            assert grade['history_authority'] == 'unverified'
            assert receipt['native_identity'] == 'unverified'
            assert not receipt['publication_allowed']
            action, = [a for a in trial['controller_actions']['actions'] if a['kind']=='tool_request']
            assert action['backend_invoked']; assert action['containment_validated']
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)

    def test_live_controller_sources_join_actual_workers_to_fresh_graders(self, trajectory):
        from dataclasses import replace
        from agent_harness_lab.evaluation import responses
        from agent_harness_lab.sources.controller import SourceSink
        from agent_harness_lab.sources.schema import digest as source_digest
        fixture = trajectory
        runtime=self.root/'runtime'; frozen=pinned_runtime(runtime)
        fixture.plan=replace(fixture.plan,runtime_hash=frozen['sha256']); fixture.freeze()
        args=fixture.f.args
        args.cgroup_root=self.cgroup_root; args.image_root=runtime
        args.image_sha256=frozen['sha256']; args.disk_bytes=64*1024*1024
        args.sandbox_backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,
            image_sha256=frozen['sha256'],disk_bytes=args.disk_bytes)
        sink=SourceSink(self.root/'source-journal.json',source_digest('synthetic-project'),source_digest('synthetic-scope'))
        args.source_sink=sink
        with patch.object(responses,'request',side_effect=fixture.f.transport), \
                patch.object(fixture.f.runner,'execute',side_effect=AssertionError('native agents must not launch')):
            assert fixture.f.runner.evaluate(args) == 1
        result=fixture.fixture.results()
        for trial in result['trials']:
            source=trial['source_capture']
            assert source['status'] == 'observed', source
            live=source['live_verification']
            assert live['statuses']['verified'] == 1
            harness=live['controller_harness_input']
            assert harness['status'] == 'observed'
            assert harness['sha256'] == digest(getattr(args,trial['version']))
            assert harness['observation_scope'] == 'controller_input_checks_before_and_after_executor'
            assert harness['effective_loading'] == 'unverified'
            assert not harness['publication_allowed']
            from agent_harness_lab.evaluation.benchmark import public_export_allowed
            public=dict(publication_allowed=True,provenance=dict(source_kind='synthetic',
                approved_publication=True,source_ref='synthetic:source-fixture',derived_from_private=False))
            assert not public_export_allowed([{**harness,**public}])
            assert not public_export_allowed([{**public,'controller_harness_input':{**harness,'publication_allowed':True}}])
            fields={field['name']:field for field in live['verified_execution']['fields']}
            assert fields['runtime_version']['value_sha256'] == frozen['sha256']
            assert fields['runtime_version']['status'] == 'verified'
            assert fields['runtime']['status'] == 'declared'
            assert fields['runtime']['namespace'] == 'ahl.binary_path_inventory'
            assert fields['runtime']['reason'] == 'no_effective_binding'
            assert fields['grader_revision']['status'] == 'verified'
            assert fields['project']['status'] == 'declared'
            for name in ('effective_harness','effective_config','memory_snapshot','served_model','code_revision'):
                assert name not in fields
            assert live['independent_roots'] is None; assert not live['task_outcomes_verified']
        metadata=sink.store.capture(sink.source)
        assert all(field.status=='declared' for identity in metadata.identities for field in identity.fields)
        bounds={bound.dimension:bound for bound in metadata.coverage.scan_bounds}
        assert (bounds['records'].limit,bounds['records'].observed) == (128,2)
        assert bounds['bytes'].limit == (256*1024); assert bounds['bytes'].observed > 0
        last=next(task for task in fixture.fixture.manifest['tasks'] if task['id']==result['trials'][-1]['task'])
        assert (bounds['files'].limit,bounds['files'].observed) == (100,len(last['artifacts']))
        assert 'runtime' in {field.name for field in metadata.coverage.missing_fields}
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)

    @pytest.mark.parametrize('mode', SOURCE_MODES)
    def test_live_controller_sources_reject_swaps_mutations_loss_and_replay_by_mode(self, mode):
        reject_swaps_and_replays(self, (mode,))

    def test_live_controller_sources_reject_swaps_mutations_loss_and_replay(self):
        # Entry point for test_source_permission_composition, which calls it with its own KernelFixture; the
        # by-mode cases above are the collected tests.
        reject_swaps_and_replays(self, SOURCE_MODES)
    test_live_controller_sources_reject_swaps_mutations_loss_and_replay.__test__ = False

    @pytest.mark.parametrize('mode,outcome', [
        pytest.param('valid', ('verified', None), id='valid'),
        pytest.param('changed', ('mismatch', 'input_tree_differs'), id='changed'),
        pytest.param('untracked', ('mismatch', 'input_tree_differs'), id='untracked'),
        pytest.param('relocated', ('unknown', 'harness_location_not_in_captured_checkout'), id='relocated'),
        pytest.param('late', ('unknown', 'snapshot_not_captured_before_execution'), id='late'),
        pytest.param('unobserved', ('unknown', 'controller_harness_input_unobserved'), id='unobserved'),
    ])
    def test_live_controller_sources_bind_executed_harness_to_owned_git_snapshot(self, mode, outcome):
        from agent_harness_lab.evaluation import controller_receipts as owned
        from agent_harness_lab.evaluation.benchmark import public_export_allowed
        from agent_harness_lab.sources import harness_binding
        from agent_harness_lab.sources.controller import SourceSink
        from agent_harness_lab.sources.schema import digest as source_digest
        from tests.integration.sources.test_source_harness_binding import SyntheticHarnessRepository, git_available
        if not git_available(): pytest.skip('explicit system Git 2.43+ is required')
        runtime=self.root/'runtime'; frozen=pinned_runtime(runtime)
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
        oracle=self.root/'oracle'; oracle.mkdir()
        (oracle/'expected.json').write_text('{}')
        (oracle/'grade.py.txt').write_text("import json,pathlib,sys; ok=(pathlib.Path(sys.argv[1])/'result.txt').read_text()=='done'; "
            "print(json.dumps(dict(schema_version=1,valid=True,outcome=ok,grader_id='synthetic-source',grader_version='v1'))); sys.exit(0 if ok else 1)")
        oracle_hash=digest(oracle)
        argv=['/usr/bin/python3','-I','-c',"from pathlib import Path; Path('result.txt').write_text('done')"]
        fixture=SyntheticHarnessRepository(self.root/('git-'+mode)); harness=fixture.repo/'harness'
        collector=None if mode=='late' else fixture.capture()[0]
        if mode=='relocated':
            shutil.copytree(fixture.repo,self.root/('copy-'+mode)); harness=self.root/('copy-'+mode)/'harness'
        with owned.session():
            sink=SourceSink(self.root/(mode+'-binding.json'),fixture.source.project_sha256,source_digest('synthetic-scope'),
                harness_capture=None if collector is None else collector.harness_capture)
            capture=sink.begin(['result.txt'],time.monotonic()+15)
            if mode=='late': collector=fixture.capture()[0]; sink.harness_capture=collector.harness_capture
            if mode=='changed': (harness/'AGENTS.md').write_text('SYNTHETIC_UNCOMMITTED_CHANGE\n')
            if mode=='untracked': (harness/'extra.md').write_text('SYNTHETIC_UNTRACKED_ADDITION\n')
            run=backend.run(argv,self.work,{} if mode=='unobserved' else {'harness':harness},'executor',
                Limits(seconds=8),artifact_allowlist=['result.txt'])
            self.assert_complete(run)
            regrade_artifacts(self.work,['result.txt'],oracle,oracle_hash,backend,limits=Limits(seconds=8))
            result=sink.finish(capture,True)
            assert result['status'] == 'observed', result
            receipt=harness_binding.bind(capture,collector.harness_capture)
            assert (receipt['status'],receipt['reason']) == outcome, receipt
            assert result['live_verification']['harness_snapshot_binding'] == receipt
            assert receipt['effective_loading'] == 'unverified'
            assert not receipt['authority_granted']; assert not receipt['publication_allowed']
            assert not public_export_allowed([{**receipt,'publication_allowed':True}])
            if mode=='valid':
                assert receipt['harness_snapshot_sha256'] == collector.harness.sha256
                assert receipt['head_sha256'] == collector.harness.head_sha256
                assert receipt['controller_harness_input_sha256'] == digest(harness)
                assert receipt['controller_harness_input_sha256'] == result['live_verification']['controller_harness_input']['sha256']
                assert receipt['git_source_sha256'] == fixture.source.source_key
                assert str(self.root) not in json.dumps(receipt)
                assert len(capture.locations) == 1
                assert 'harness_location_sha256' not in json.dumps(capture.read())
            if outcome[0]=='unknown': assert receipt['harness_snapshot_sha256'] is None
            assert all(field.status=='declared' for item in sink.store.capture(sink.source).identities for field in item.fields)
        after=harness_binding.bind(capture,collector.harness_capture)
        assert (after['status'],after['reason']) == ('unknown','owned_controller_capture_required')

    def test_live_controller_sources_do_not_verify_replaced_executable_inventory(self):
        from agent_harness_lab.evaluation import controller_receipts as owned
        from agent_harness_lab.sources.controller import SourceSink
        from agent_harness_lab.sources.schema import digest as source_digest
        runtime=self.root/'runtime'; frozen=pinned_runtime(runtime)
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=frozen['sha256'],disk_bytes=64*1024*1024)
        binary=self.root/'selected-bwrap'; shutil.copy2(backend.binary,binary); backend.binary=str(binary)
        original=binary.read_bytes(); before=hashlib.sha256(original).hexdigest()
        swapped=False; observer=owned._kernel_spawned
        def observe(ticket,process,scratch,group):
            nonlocal swapped
            observer(ticket,process,scratch,group)
            if ticket is not None and not swapped:
                replacement=self.root/'replacement'; replacement.write_bytes(original+b'SYNTHETIC_INVENTORY_REPLACEMENT')
                replacement.chmod(0o755); replacement.replace(binary); swapped=True
        oracle=self.root/'oracle'; oracle.mkdir(); (oracle/'expected.json').write_text('{}')
        (oracle/'grade.py.txt').write_text("import json; print(json.dumps(dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic-source',grader_version='v1')))")
        sink=SourceSink(self.root/'journal.json',source_digest('synthetic-project'),source_digest('synthetic-scope'))
        with owned.session(),patch.object(owned,'_kernel_spawned',side_effect=observe):
            capture=sink.begin(['result.txt'],time.monotonic()+15)
            run=backend.run(['/usr/bin/python3','-I','-c',"from pathlib import Path; Path('result.txt').write_text('done')"],
                self.work,{},'executor',Limits(seconds=8),artifact_allowlist=['result.txt'])
            self.assert_complete(run)
            regrade_artifacts(self.work,['result.txt'],oracle,digest(oracle),backend,limits=Limits(seconds=8))
            result=sink.finish(capture,True)
            assert result['status'] == 'observed', result
            fields={field['name']:field for field in result['live_verification']['verified_execution']['fields']}
            if fields['runtime']['status']=='verified':
                assert fields['runtime']['value_sha256'] == before
            else:
                assert fields['runtime']['status'] in ('declared','unknown')
                if fields['runtime']['status']=='declared':
                    assert before != fields['runtime']['value_sha256']
                    assert fields['runtime']['value_sha256'] == hashlib.sha256(binary.read_bytes()).hexdigest()
                    assert fields['runtime']['namespace'] == 'ahl.binary_path_inventory'
                    assert fields['runtime']['reason'] == 'no_effective_binding'
            assert fields['runtime_version']['status'] == 'verified'


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestStorageKernel(KernelCase):
    backend_options = dict(disk_bytes=64*1024*1024)

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
        assert first['status'] == 'failed'; assert first['exit_code'] == 1
        assert first['attestation']['cgroup_v2']['empty_after_cleanup']
        assert first['attestation']['storage_policy']['extracted'] is not None
        second = execute("from pathlib import Path; assert not Path('empty').exists(); assert not Path('seed').exists(); assert Path('result').read_text()=='edited'; Path('result').write_text('final')")
        self.assert_complete(second)
        before = state.snapshot
        failed = execute("from pathlib import Path; import time; Path('result').write_text('partial'); time.sleep(30)",.5)
        assert failed['status'] == 'timeout'; assert state.snapshot is before
        assert failed['attestation']['cgroup_v2']['empty_after_cleanup']
        signaled = execute("from pathlib import Path; import os,signal; Path('result').write_text('killed'); os.kill(os.getpid(),signal.SIGKILL)")
        assert signaled['exit_code']<0 or signaled['exit_code']>=128
        assert state.snapshot is before
        assert signaled['attestation']['storage_policy']['extracted'] is None
        exported = state.export(['result'],self.root/'artifacts',time.monotonic()+5)
        assert (exported/'result').read_text() == 'final'
        assert (self.work/'seed').read_text() == 'original'; assert (self.work/'empty').is_dir()

    def test_every_writable_volume_enforces_aggregate_capacity_without_leaking_setup_fds(self):
        code = "import errno,os,stat\nfor value in os.listdir('/proc/self/fd'):\n if int(value)>2:\n  try: info=os.fstat(int(value))\n  except OSError: continue\n  assert not stat.S_ISDIR(info.st_mode), 'setup handle leaked'\nfor path,cap in (('/work',32*1024*1024),('/tmp',16*1024*1024),('/dev/shm',16*1024*1024)):\n info=os.statvfs(path)\n assert info.f_blocks*info.f_frsize == cap\n denied=False\n for number in range(128):\n  try:\n   with open(path+'/'+str(number),'wb') as stream: stream.write(b'x'*(512*1024))\n  except OSError as error:\n   assert error.errno == errno.ENOSPC\n   denied=True; break\n assert denied, path+' exceeded aggregate quota'\n assert os.statvfs(path).f_bavail == 0\n for name in os.listdir(path): os.unlink(path+'/'+name)\nprint('all-volumes-bounded')\n"
        run = self.run_code(code)
        self.assert_complete(run)
        assert 'all-volumes-bounded' in run['stdout']
        assert run['attestation']['aggregate_disk_bound_verified']
        assert list(self.work.iterdir()) == []

    def test_seed_and_only_allowlisted_artifacts_survive_worker_cleanup(self):
        source = self.work/'seed'; source.write_text('original'); source.chmod(0o755)
        (self.work/'empty').mkdir()
        code = "from pathlib import Path\nassert Path('seed').read_text() == 'original'\nassert Path('seed').stat().st_mode & 0o777 == 0o755\nassert Path('empty').is_dir()\nPath('seed').write_text('changed')\nPath('result').write_text('allowed')\nPath('unlisted').write_text('discarded')\n"
        run = self.run_code(code, artifact_allowlist=['seed', 'result'])
        self.assert_complete(run)
        assert source.read_text() == 'changed'
        assert (source.stat().st_mode & 0o777) == 0o755
        assert (self.work/'result').read_text() == 'allowed'
        assert not (self.work/'unlisted').exists()
        assert run['attestation']['storage_policy']['extracted'] == dict(artifacts=2, bytes=14)

    @pytest.mark.parametrize('code', [
        pytest.param("os.symlink('/etc/passwd','result')", id='symlink'),
        pytest.param("Path('original').write_text('x'); os.link('original','result')", id='hardlink'),
        pytest.param("os.mkfifo('result')", id='fifo'),
        pytest.param("Path('dir').mkdir(); os.symlink('dir','result')", id='directory-link'),
        pytest.param("Path('result').write_bytes(b'x'*(1024*1024+1))", id='oversized'),
    ])
    def test_unsafe_or_oversized_outputs_are_rejected_before_export(self, code):
        with pytest.raises((OSError, ValueError)):
            self.run_code('import os; from pathlib import Path; '+code,
                artifact_allowlist=['result'], limits=Limits(seconds=5, file_bytes=2*1024*1024))
        assert list(self.work.iterdir()) == []

    def test_failure_timeout_and_interruption_do_not_export_and_leave_no_cgroup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        for suffix, expected in (("raise SystemExit(1)", 'failed'), ("import time; time.sleep(30)", 'timeout')):
            run = self.run_code("from pathlib import Path; Path('result').write_text('untrusted'); "+suffix,
                                artifact_allowlist=['result'], limits=Limits(seconds=.5))
            assert run['status'] == expected
            assert not (self.work/'result').exists()
        with patch.object(selectors.EpollSelector, 'select', side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt): self.run_code('import time; time.sleep(30)')
        assert {p.name for p in self.cgroup_root.iterdir() if p.is_dir()} == before

    @pytest.mark.parametrize('attack', ['memfd', 'new-tmpfs'])
    def test_memfd_and_new_tmpfs_cannot_bypass_the_aggregate_storage_ceiling(self, attack):
        filler = "import os; f=open('data','wb'); [f.write(b'x'*65536) for _ in range(2048)]"
        new_mount = ['/usr/bin/bwrap', '--unshare-all', '--unshare-user', '--ro-bind', '/', '/',
                     '--proc', '/proc', '--dev', '/dev', '--size', str(128*1024*1024),
                     '--tmpfs', '/tmp', '--chdir', '/tmp', '--', '/usr/bin/python3', '-c', filler]
        code = {'memfd': "import os; fd=os.memfd_create('bypass'); [os.write(fd,b'x'*65536) for _ in range(2048)]",
                'new-tmpfs': f"import os; os.execv('/usr/bin/bwrap',{new_mount!r})"}[attack]
        (self.work/'seed').write_bytes(b'x'*(1024*1024))
        run = self.run_code(code, limits=Limits(seconds=5, file_bytes=128*1024*1024))
        assert run['status'] == 'resource_limit', run
        controls = run['attestation']['cgroup_v2']
        assert run['attestation']['storage_policy']['controller_seeded_bytes'] == 1024*1024
        assert int(controls['controls']['memory.max']) == 63*1024*1024
        assert controls['events']['memory.events']['oom_kill'] > 0
        assert controls['empty_after_cleanup']

    def test_capability_verifies_the_storage_policy(self):
        result = self.backend.capability()
        assert result['status'] == 'available', result
        assert result['storage_policy']['verified']
        assert 'no aggregate disk quota' not in result['limitations']

    def test_hugetlb_memfd_mapping_and_shared_memory_are_denied(self):
        code = "import ctypes,errno,mmap,os\ntry: os.memfd_create('hugetlb-probe',os.MFD_HUGETLB)\nexcept OSError as error: assert error.errno == errno.EPERM\nelse: raise AssertionError('HugeTLB memfd allowed')\nlibc=ctypes.CDLL(None,use_errno=True)\nlibc.mmap.restype=ctypes.c_void_p\nmapped=libc.mmap(None,2*1024*1024,3,mmap.MAP_PRIVATE|mmap.MAP_ANONYMOUS|0x40000,-1,0)\nassert mapped == ctypes.c_void_p(-1).value and ctypes.get_errno() == errno.EPERM\nassert libc.shmget(0,2*1024*1024,0o600|0o1000|0o4000) == -1\nassert ctypes.get_errno() == errno.EPERM\nprint('hugetlb-denied')\n"
        run = self.run_code(code, limits=Limits(seconds=5, file_bytes=4*1024*1024))
        self.assert_complete(run)
        assert 'hugetlb-denied' in run['stdout']
        assert run['attestation']['storage_policy']['hugetlb_policy']['name'] == 'seccomp-deny-hugetlb-v1'

    def test_alternate_syscall_numbers_cannot_bypass_the_storage_filter(self):
        code = "import ctypes; ctypes.CDLL(None).syscall(0x4000013f,0,4); raise AssertionError('alternate syscall returned')"
        run = self.run_code(code)
        assert run['status'] == 'failed', run
        assert run['stderr'] == ''
        assert run['attestation']['cgroup_v2']['empty_after_cleanup']

    def test_startup_verification_and_tiny_budget_fail_closed_and_cleanup(self):
        before = {p.name for p in self.cgroup_root.iterdir() if p.is_dir()}
        with patch('agent_harness_lab.evaluation.workspace.VERIFY', "raise RuntimeError('synthetic startup failure')"):
            with pytest.raises(ValueError): self.run_code("raise AssertionError('worker must not start')")
        (self.work/'seed').write_bytes(b'x'*(5*1024*1024))
        self.backend = SandboxBackend(cgroup_root=self.cgroup_root, disk_bytes=16384)
        with pytest.raises((OSError, ValueError)): self.run_code("raise AssertionError('worker must not start')")
        assert (self.work/'seed').stat().st_size == 5*1024*1024
        assert {p.name for p in self.cgroup_root.iterdir() if p.is_dir()} == before


@pytest.mark.skipif(not ENABLED, reason='requires explicit delegated kernel canaries; use task test:sandbox:kernel')
class TestResourceKernel(KernelCase):
    def prepare(self):
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
        assert run['attestation']['cgroup_v2']['events']['memory.events']['oom_kill'] > 0
        assert run['status'] == 'resource_limit'
        assert run['attestation']['cgroup_v2']['empty_after_cleanup']

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
        assert run['status'] == 'resource_limit'
        assert run['attestation']['cgroup_v2']['events']['pids.events']['max'] > 0
        for pid in run['stdout'].split():
            path = Path('/proc')/pid/'stat'
            if path.exists():
                try: assert path.read_text().split(') ')[1].split()[0] == 'Z'
                except FileNotFoundError: pass


# test_permission_audit and test_source_permission_composition call methods of these classes with their own
# KernelFixture instance as `self`, so the former names stay importable.
ControlledWorkflowKernelTests = TestControlledWorkflowKernel
TrajectoryKernelTests = TestTrajectoryKernel
