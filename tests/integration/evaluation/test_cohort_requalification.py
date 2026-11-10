"""Public synthetic fresh qualification; actual grades, simulated sandbox attestations."""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import evaluator
from agent_harness_lab.private_journal import encoded, record
from agent_harness_lab.evaluation import cohort_requalification as core, cohorts
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.oracle import identity
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.support.bank_correction import bank_correction
from tests.integration.evaluation import test_offline_evaluation as offline


class RequalificationFixture(unittest.TestCase):
    """Reusable real controller fixture; the initial bank already has structural v2.

    It stays a unittest.TestCase: test_correction_cohorts builds it through setUp() and doCleanups(), and
    execute() reuses offline.OfflineFixture.emulate, which calls TestCase assertions on it.
    """
    def setUp(self):
        self.application = self.enterContext(bank_correction())
        f = self.application
        self.root, self.history = f.root, f.history
        self.applied = f.apply(); self.bank = f.bank(self.applied)
        self.runner = evaluator()
        self.before, self.baseline, self.candidate = [self.root / name for name in ('original-D', 'original-B', 'original-C')]
        for path, count, suffix in ((self.before, 0, ''), (self.baseline, 3, ''), (self.candidate, 3, '\n# candidate\n')):
            path.mkdir()
            (path / 'solve.py').write_text("from pathlib import Path\nPath('result.json').write_text(" +
                repr(json.dumps(dict(count=count, label='ok'))) + ')' + suffix + '\n')
        self.export = self.root / 'old-qualification'
        source = export_manifest(self.bank, self.export)
        for task in source['tasks']:
            task['offline_executor'] = ['/usr/bin/python3', '-I', '/inputs/harness/solve.py']
        tasks = tuple((v['id'], v['root'], v['family']) for v in source['tasks'])
        families = sorted({t[2] for t in tasks})
        spec = ExperimentSpec('synthetic-old-qualification', tasks, 1, 17, 2*len(tasks), 2, .2, .3, .1,
            tuple((f, 1/len(families)) for f in families), self.runner.digest(self.before), self.runner.digest(self.baseline),
            identity(self.export, source['tasks'], source['grader_bundles']), 'e'*64)
        source.update(experiment=spec.to_dict(), max_trials=spec.max_trials, budget_seconds=60, timeout_seconds=3)
        (self.export / 'manifest.json').write_bytes(encoded(source))
        with self.workers():
            run = self.history.run(self.export / 'manifest.json', self.before, self.baseline, **self.profile())
        self.assertEqual(run['state'], 'sealed', run)
        prior_q = self.history.historical_plan(run['run_id'])
        inputs = record(self.history.case(run['run_id']) / 'inputs.json')[0]
        bank = record(self.bank)[0]
        members = []
        for task in bank['tasks']:
            bound = inputs['bank_tasks'][task['task_id']]
            members.append(dict(task_id=task['task_id'], root_id=task['root_id'], family=task['family'],
                bank_sha256=inputs['bank_authority']['bank_sha256'], bank_revision_sha256=inputs['bank_authority']['revision_sha256'],
                task_revision_sha256=bound['task_revision_sha256'], fixture_sha256=task['fixture_digest'],
                materialized_sha256=bound['materialized_sha256'], execution_fixture_sha256=bound['execution_fixture_sha256'],
                bundle_sha256=task['grader_digest'], grader_version=task['grader_version']))
        qualification = dict(history_store=str(self.history.root), arm='candidate', rule=dict(min_trials=1, min_successes=1),
            review=dict(identity='synthetic-old-reviewer', source='operator_attestation', basis_kind='independent_artifact_review',
                        evidence_refs=[dict(ref='synthetic:old-review', sha256='a'*64)]),
            **{k: prior_q[k] for k in ('run_id', 'event_sha256', 'evidence_sha256', 'plan_sha256')})
        old_manifest = dict(schema_version=1, profile=cohorts.PROFILE, author='synthetic-old-author', cohorts=[])
        for name, role in (('passed', 'historical_pass'), ('repaired', 'previously_repaired')):
            old_manifest['cohorts'].append(dict(cohort_id=name, role=role, required=True, margin=.15,
                exposure='development_regression', severity_policy='verified_critical_veto',
                qualification=deepcopy(qualification), members=deepcopy(members)))
        old_path = self.export / 'cohort-manifest.json'; old_path.write_bytes(encoded(old_manifest))
        pin = record(old_path, core.MAX_BYTES)[1]
        source['cohort_manifest'] = dict(path='cohort-manifest.json', sha256=pin)
        source['experiment'] = replace(spec, experiment_id='synthetic-old-comparison', baseline_hash=spec.candidate_hash,
            candidate_hash=self.runner.digest(self.candidate), cohort_manifest_sha256=pin).to_dict()
        (self.export / 'manifest.json').write_bytes(encoded(source))
        with self.workers():
            run = self.history.run(self.export / 'manifest.json', self.baseline, self.candidate, **self.profile())
        self.assertEqual(run['state'], 'sealed', run)
        self.prior = self.history.historical_plan(run['run_id'])
        self.protocol_path = self.root / 'qualification-protocol.json'
        self.protocol = dict(schema_version=2, algorithm=core.ALGORITHM, prior_run_id=self.prior['run_id'],
            prior_plan_sha256=self.prior['plan_sha256'], cohort_manifest_sha256=pin, author='synthetic-protocol-author',
            image_sha256='e'*64, max_total_trials=6*len(tasks),
            before_harnesses=[dict(history_store=str(self.history.root), run_id=prior_q['run_id'], path=str(self.before))])
        self.write_protocol()

    def write_protocol(self):
        self.protocol_path.write_bytes(encoded(self.protocol))
        self.protocol_sha256 = record(self.protocol_path, core.MAX_BYTES)[1]
        self.review = dict(identity='synthetic-protocol-reviewer', source='operator_attestation',
            basis_kind='independent_artifact_review', evidence_refs=[dict(ref='synthetic:predeclared-protocol', sha256=self.protocol_sha256)])

    def profile(self):
        return dict(cgroup_root=self.root/'delegation', image_root=self.root/'image', image_sha256='e'*64,
                    disk_bytes=64*1024*1024, budget_seconds=60)

    def execute(self, *args, **kwargs):
        result = offline.OfflineFixture.emulate(self, *args)
        result['attestation'] = dict(backend='bubblewrap-offline-v1', role=args[3], network='none',
            aggregate_disk_bound_verified=True, cgroup_v2={'empty_after_cleanup':True},
            runtime_image=dict(sha256='e'*64, verified_before=True, verified_after=True))
        return result

    def workers(self, execute=None):
        stack = ExitStack()
        stack.enter_context(patch.object(SandboxBackend, 'capability', return_value={'status':'available'}))
        self.worker = stack.enter_context(patch.object(SandboxBackend, 'run', side_effect=execute or self.execute))
        return stack

    def qualify(self, bank_path=None, **changes):
        profile = self.profile(); profile.pop('budget_seconds')
        kwargs = dict(review=self.review, parent_deadline=time.monotonic()+300, **profile)
        kwargs.update(changes)
        with self.workers():
            result = core.qualify(self.protocol_path, self.protocol_sha256, self.prior, bank_path or self.bank,
                self.baseline, self.candidate, self.root/'fresh-qualification', self.history, **kwargs)
        return result


@contextmanager
def requalification():
    fixture = RequalificationFixture()
    try:
        fixture.setUp(); yield fixture
    finally:
        fixture.doCleanups()


@pytest.fixture(scope='module')
def rejecting():
    """One fixture for cases that each restore what they change and must fail before any dispatch."""
    with requalification() as fixture: yield fixture


@pytest.fixture(scope='module')
def qualified():
    """One completed qualification for cases that only verify mutated copies of its binding."""
    with requalification() as fixture: yield fixture, fixture.qualify()


@contextmanager
def changed_protocol(f, change):
    original = deepcopy(f.protocol); f.protocol.update(change); f.write_protocol()
    try: yield
    finally: f.protocol = original; f.write_protocol()


RAW_PROTOCOLS = [pytest.param(b'{"schema_version":2,"schema_version":2}', id='duplicate-key'),
                 pytest.param(b'{"schema_version":NaN}', id='nan'), pytest.param(b'[]', id='list')]
PROTOCOL_CHANGES = [pytest.param(dict(schema_version=True), id='schema-version-bool'),
                    pytest.param(dict(max_total_trials=True), id='max-trials-bool'),
                    pytest.param(dict(max_total_trials=6.0), id='max-trials-float'),
                    pytest.param(dict(prior_plan_sha256='f'*64), id='prior-plan'),
                    pytest.param(dict(image_sha256='f'*64), id='image'),
                    pytest.param(dict(unexpected='field'), id='unexpected-field')]
# Callables of the fixture's own protocol and review, which exist only once the fixture is built.
BEFORE_CHANGES = [pytest.param(lambda f: dict(before_harnesses=[]), id='missing-before'),
                  pytest.param(lambda f: dict(before_harnesses=f.protocol['before_harnesses']*2), id='duplicate-before'),
                  pytest.param(lambda f: dict(max_total_trials=5), id='ceiling')]
RUNTIME_CHANGES = [pytest.param(lambda f: dict(image_sha256='f'*64), id='wrong-image'),
                   pytest.param(lambda f: dict(review=dict(f.review, identity=f.protocol['author'])), id='author-review'),
                   pytest.param(lambda f: dict(review=dict(f.review, evidence_refs=[])), id='review-without-evidence')]
BINDING_MUTANTS = [
    pytest.param(lambda f, b: b['phases'][0].update(run_id=f.prior['run_id']), id='prior-run'),
    pytest.param(lambda f, b: b['phases'][0].update(event_sha256='f'*64), id='event'),
    pytest.param(lambda f, b: b['phases'][0].update(manifest_sha256='f'*64), id='manifest'),
    pytest.param(lambda f, b: b['cohort_binding']['qualification_bindings']['passed']['counts'][next(iter(
        b['cohort_binding']['qualification_bindings']['passed']['counts']))].update(successes=0), id='successes'),
    pytest.param(lambda f, b: b['snapshots']['candidate'].update(mode_sha256='f'*64), id='candidate-mode'),
    pytest.param(lambda f, b: b.update(publication_allowed=True), id='publication')]
MEMBERSHIP_CHANGES = [pytest.param(lambda member, second: [member], id='tail-loss'),
                      pytest.param(lambda member, second: [second, member], id='reordered'),
                      pytest.param(lambda member, second: [member, second, second], id='duplicated')]


def first_members(f):
    original = record(f.history.case(f.prior['run_id'])/'cohort-binding.json', core.MAX_BYTES)[0]
    member = original['manifest']['cohorts'][0]['members'][0]
    return member, dict(member, task_id='synthetic-second-member')


@pytest.mark.parametrize('raw', RAW_PROTOCOLS)
def test_protocol_raw_parser_rejects_before_dispatch(rejecting, raw):
    f = rejecting
    f.protocol_path.write_bytes(raw)
    try:
        with pytest.raises(ValueError):
            core.load_protocol(f.protocol_path, hashlib.sha256(raw).hexdigest(), f.prior,
                               f.baseline, f.candidate, 'e'*64)
    finally:
        f.write_protocol()


@pytest.mark.parametrize('change', PROTOCOL_CHANGES)
def test_protocol_types_and_exact_identity_reject_before_dispatch(rejecting, change):
    f = rejecting
    with changed_protocol(f, change):
        with patch.object(core.HistoryStore, 'run') as run:
            with pytest.raises(ValueError): f.qualify()
            run.assert_not_called()
        assert not (f.root/'fresh-qualification').exists()


@pytest.mark.parametrize('change', BEFORE_CHANGES)
def test_missing_duplicate_D_and_ceiling_fail_before_dispatch(rejecting, change):
    f = rejecting
    with changed_protocol(f, change(f)):
        with patch.object(core.HistoryStore, 'run') as run:
            with pytest.raises(ValueError): f.qualify()
            run.assert_not_called()
        assert not (f.root/'fresh-qualification').exists()


@pytest.mark.parametrize('change', RUNTIME_CHANGES)
def test_wrong_runtime_and_review_fail_before_dispatch(rejecting, change):
    f = rejecting
    with patch.object(core.HistoryStore, 'run') as run:
        with pytest.raises(ValueError): f.qualify(**change(f))
        run.assert_not_called()


@pytest.mark.parametrize('mutate', BINDING_MUTANTS)
def test_binding_mutants_cannot_replace_actual_sealed_evidence(qualified, mutate):
    f, binding = qualified
    changed = deepcopy(binding); mutate(f, changed)
    with pytest.raises(ValueError):
        core.verify_completion(changed, f.prior, f.bank, f.history)


@pytest.mark.parametrize('updated', MEMBERSHIP_CHANGES)
def test_whole_membership_comparison_rejects_tail_loss_and_order_changes(rejecting, updated):
    member, second = first_members(rejecting)
    with pytest.raises(ValueError, match='membership'):
        core._members([member, second], updated(member, second))


class TestCohortRequalification:
    @pytest.fixture(autouse=True)
    def _requalification(self):
        with requalification() as fixture:
            self.f = fixture; yield

    def test_actual_fresh_phases_freeze_same_members_roles_rules_and_private_attribution(self):
        f = self.f; started = []
        binding = f.qualify(on_started=lambda phase, run: started.append((phase, run)))
        assert len(binding['phases']) == 2
        assert f.worker.call_count == 8
        assert len(started) == 2
        assert all(p['run_id'] != f.prior['run_id'] for p in binding['phases'])
        for phase in binding['phases']:
            actual = f.history.historical_plan(phase['run_id'])
            assert actual['plan']['candidate_hash'] == f.prior['plan']['baseline_hash']
            if phase['role'] == 'historical_pass':
                assert actual['plan']['baseline_hash'] == actual['plan']['candidate_hash']
        assert not binding['publication_allowed']
        assert 'supported' not in binding
        assert core.verify_completion(binding, f.prior, f.bank, f.history) == binding
        assert core.verify_completion(binding, f.prior, f.bank, f.history, current=True) == binding

    def test_protocol_digest_must_match(self):
        f = self.f
        with pytest.raises(ValueError, match='digest'):
            core.load_protocol(f.protocol_path, 'f'*64, f.prior, f.baseline, f.candidate, 'e'*64)

    def test_missing_original_before_bytes_fail_before_dispatch(self):
        f = self.f
        (f.before/'solve.py').write_text('# missing original before bytes\n')
        with patch.object(core.HistoryStore, 'run') as run:
            with pytest.raises(ValueError, match='before-harness'): f.qualify()
            run.assert_not_called()

    def test_actual_repaired_phase_without_new_failure_to_pass_cannot_freeze(self):
        f = self.f
        def all_pass(*args, **kwargs):
            result = f.execute(*args, **kwargs)
            if args[3] == 'executor':
                (args[1]/'result.json').write_text('{"count":3,"label":"ok"}')
            return result
        profile = f.profile(); profile.pop('budget_seconds')
        with f.workers(all_pass), pytest.raises(ValueError, match='failure-to-pass'):
            core.qualify(f.protocol_path, f.protocol_sha256, f.prior, f.bank, f.baseline, f.candidate,
                f.root/'fresh-qualification', f.history, review=f.review,
                parent_deadline=time.monotonic()+300, **profile)
        assert f.worker.call_count == 8
        assert f.history.historical_plan(f.prior['run_id'])['state'] == 'sealed'
        assert not (f.root/'fresh-qualification/cohort-binding.json').exists()

    def test_historical_completion_survives_external_deletion_and_later_revocation(self):
        f = self.f; binding = f.qualify()
        f.protocol_path.unlink(); (f.baseline/'solve.py').unlink(); (f.before/'solve.py').unlink()
        assert core.verify_completion(binding, f.prior, f.bank, f.history) == binding
        with pytest.raises((ValueError, OSError)):
            core.verify_completion(binding, f.prior, f.bank, f.history, current=True)
        bundle = binding['cohort_binding']['manifest']['cohorts'][0]['members'][0]['bundle_sha256']
        active = f.history.oracle(bundle)
        request = dict(schema_version=1, bundle_sha256=bundle, expected_event_sha256=active['event_sha256'],
            operator=dict(identity='synthetic-quarantine-reviewer', source='operator_attestation',
                          provenance='synthetic evidence', basis='Revoke synthetic oracle'),
            evidence_refs=[dict(ref='synthetic:revocation', sha256='b'*64)], reason='Synthetic later revocation')
        path = f.root/'quarantine.json'; path.write_bytes(encoded(request)); f.history.quarantine(path)
        assert core.verify_completion(binding, f.prior, f.bank, f.history) == binding

    def test_changed_started_run_custody_cannot_replace_actual_sealed_evidence(self):
        f = self.f; binding = f.qualify()
        phase = binding['phases'][0]; Path(phase['started_path']).write_bytes(encoded(dict(run_id=f.prior['run_id'])))
        with pytest.raises(ValueError, match='custody'):
            core.verify_completion(binding, f.prior, f.bank, f.history)

    def test_interrupt_preserves_started_real_run_without_redispatch_or_success(self):
        f = self.f; started = []
        def interrupt(phase, run):
            started.append((phase, run)); raise KeyboardInterrupt
        with pytest.raises(KeyboardInterrupt): f.qualify(on_started=interrupt)
        assert f.worker.call_count == 0
        assert len(started) == 1
        phase, run = started[0]
        assert record(f.root/'fresh-qualification'/phase/'started-run.json')[0] == dict(run_id=run)
        assert f.history.historical_plan(run)['state'] == 'incomplete'
        with pytest.raises(ValueError, match='must be new'): f.qualify()

    def test_one_deadline_includes_preflight_and_final_revalidation(self):
        f = self.f
        with patch.object(core.HistoryStore, 'run') as run:
            with pytest.raises(TimeoutError): f.qualify(parent_deadline=time.monotonic()-1)
            run.assert_not_called()
        binding = f.qualify()
        clock = [time.monotonic()]; real = core.verify_completion
        def late(*args, **kwargs):
            result = real(*args, **kwargs); clock[0] += 301; return result
        with patch.object(core, 'verify_completion', side_effect=late), \
                patch.object(core.time, 'monotonic', side_effect=lambda:clock[0]), pytest.raises(TimeoutError):
            core.revalidate(binding, f.prior, f.baseline, f.candidate, f.bank, f.history, parent_deadline=clock[0]+300)

    def test_late_original_edit_during_live_authority_recheck_rejects_completion(self):
        f = self.f; binding = f.qualify(); original = cohorts.revalidate
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            (f.baseline/'solve.py').write_text('# changed during final check\n')
            return result
        with patch.object(cohorts, 'revalidate', side_effect=changed), pytest.raises(ValueError, match='original harness'):
            core.revalidate(binding, f.prior, f.baseline, f.candidate, f.bank, f.history,
                            parent_deadline=time.monotonic()+300)
        assert core.verify_completion(binding, f.prior, f.bank, f.history) == binding

    def test_whole_membership_comparison_accepts_a_changed_bundle_in_the_same_order(self):
        member, second = first_members(self.f)
        core._members([member, second], [dict(member, bundle_sha256='f'*64), second])

    def test_snapshot_uses_bounded_captured_bytes_without_following_late_source_symlink(self):
        f = self.f; source = f.root/'source-race'; source.mkdir()
        (source/'allowed.txt').write_text('original synthetic bytes')
        outside = f.root/'synthetic-outside.txt'; outside.write_text('distinct synthetic sentinel')
        expected = core.runtime_hash(core.scan(source)[1]); actual_scan = core.scan
        def changed(path):
            result = actual_scan(path)
            if Path(path) == source:
                (source/'allowed.txt').unlink(); (source/'allowed.txt').symlink_to(outside)
            return result
        with patch.object(core, 'scan', side_effect=changed):
            binding = core._copy(source, f.root/'bounded-copy', expected)
        assert (Path(binding['path'])/'allowed.txt').read_text() == 'original synthetic bytes'
        assert outside.read_text() == 'distinct synthetic sentinel'

    def test_destination_under_original_before_harness_rejects_without_source_writes(self):
        f = self.f; profile = f.profile(); profile.pop('budget_seconds')
        destination = f.before/'unapproved-custody'
        with f.workers(), pytest.raises(ValueError, match='overlaps'):
            core.qualify(f.protocol_path, f.protocol_sha256, f.prior, f.bank, f.baseline, f.candidate,
                destination, f.history, review=f.review, parent_deadline=time.monotonic()+300, **profile)
        assert not destination.exists()
        assert f.worker.call_count == 0
