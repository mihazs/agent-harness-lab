"""Synthetic draft audits with simulated containment; kernel evidence is separate."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
from contextlib import ExitStack
from unittest.mock import Mock, patch

import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_controls import audit
from tests.integration.evaluation.test_regression_proposals import proposal
from tests.support.optional_pytest import pytest


def make_draft(root, image_sha256='e' * 64):
    bank = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    task = next(t for t in json.loads((bank/'bank.json').read_text())['tasks'] if t['task_id'] == 'regression')
    shutil.copytree(bank/task['fixture'], root/'fixture')
    shutil.copytree(bank/task['grader_bundle'], root/'oracle')
    shutil.copytree(root/'oracle/controls/complete', root/'oracle/controls/superficial')
    (root/'oracle/controls/superficial/original.txt').write_text('damaged\n')
    declared = proposal(); declared['root_id'] = task['root_id']; declared['family'] = task['family']
    declared['environment']['image_sha256'] = image_sha256
    source = root/'proposal.json'; source.write_text(json.dumps(declared))
    task.update(fixture='fixture', grader_bundle='oracle', grader_digest=digest(root/'oracle'),
                review_status='unreviewed', independence='unverified', reviewer='pending-review')
    controls = [dict(state=state, fixture='oracle/controls/'+state, digest=digest(root/'oracle/controls'/state))
                for state in ('broken', 'partial', 'complete', 'alternative_valid', 'superficial')]
    value = dict(schema_version=1, proposal_sha256=hashlib.sha256(source.read_bytes()).hexdigest(), task=task, controls=controls)
    draft = root/'draft.json'; draft.write_text(json.dumps(value))
    return source, draft


def emulate(argv, workspace, inputs, role, limits):
    assert role == 'grader'; assert set(inputs) == {'oracle', 'fixture'}
    translated = [arg.replace('/inputs/oracle', str(inputs['oracle'])).replace('/inputs/fixture', str(inputs['fixture'])) for arg in argv]
    run = subprocess.run(translated, cwd=workspace, env={'PATH':'/usr/bin:/bin'}, capture_output=True, text=True, timeout=limits.seconds)
    return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
        stdout=run.stdout, stderr=run.stderr, inputs_unchanged=True,
        attestation=dict(backend='bubblewrap-offline-v1', role='grader', network='none',
            aggregate_disk_bound_verified=True, cgroup_v2={'empty_after_cleanup':True},
            runtime_image=dict(sha256='e'*64, verified_before=True, verified_after=True)))


class RegressionControl:
    """Synthetic draft and fake backend for regression controls."""

    emulate = staticmethod(emulate)

    def __init__(self, add_cleanup):
        self.temp = tempfile.TemporaryDirectory(); add_cleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.draft = make_draft(self.root)
        self.output = self.root/'review'
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup', disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image', sha256='e'*64),
            capability=Mock(return_value={'status':'available'}), run=Mock(side_effect=emulate))

    def run_audit(self, **kwargs):
        return audit(self.source, self.draft, self.output, self.backend, **kwargs)

    def change(self, edit):
        value = json.loads(self.draft.read_text()); edit(value)
        self.draft.write_text(json.dumps(value))


@pytest.fixture
def case():
    with ExitStack() as cleanups:
        yield RegressionControl(cleanups.callback)


def test_complete_frozen_controls_pass_without_admission_or_historical_verification(case):
    before = digest(case.root/'fixture'), digest(case.root/'oracle')
    report = case.run_audit()
    assert report['status'] == 'grader_controls_passed'
    assert [c['observed'] for c in report['checks']] == [False, False, True, True, False]
    assert case.backend.run.call_count == 5
    assert before == (digest(case.root/'fixture'), digest(case.root/'oracle'))
    assert report['draft_review_status'] == 'unreviewed'
    for key in ('bank_admission', 'task_failure_verified', 'automatic_promotion', 'publication_allowed'):
        assert not report[key]
    assert (case.output.stat().st_mode & 0o777) == 0o700
    assert ((case.output/'review.json').stat().st_mode & 0o777) == 0o600
    assert json.loads((case.output/'review.json').read_text()) == report


DRAFT_MUTATIONS = [
    pytest.param(lambda v: v.update(proposal_sha256='f'*64), id='proposal-pin'),
    pytest.param(lambda v: v['task'].update(root_id='other'), id='root'),
    pytest.param(lambda v: v['task'].update(review_status='accepted'), id='review-status'),
    pytest.param(lambda v: v['task'].update(partition='certification'), id='partition'),
    pytest.param(lambda v: v['task'].update(independence='reviewed'), id='independence'),
    pytest.param(lambda v: v['task'].update(fixture_digest='f'*64), id='fixture-digest'),
    pytest.param(lambda v: v['task'].update(artifacts=['../escape']), id='escaping-artifact'),
    pytest.param(lambda v: v['controls'][-1].update(digest='f'*64), id='control-digest'),
    pytest.param(lambda v: v['controls'][-1].update(state='complete'), id='duplicate-control-state'),
]


@pytest.mark.parametrize('edit', DRAFT_MUTATIONS)
def test_every_identity_and_control_is_validated_before_any_worker(case, edit):
    case.change(edit)
    with pytest.raises(ValueError): case.run_audit()
    case.backend.run.assert_not_called(); assert not case.output.exists()


def test_always_passing_oracle_and_malformed_grade_cannot_pass_controls(case):
    original = case.emulate
    def fake(*args):
        run = original(*args); run.update(status='completed', exit_code=0,
            stdout=json.dumps(dict(schema_version=1, valid=True, outcome=True, grader_id='fake', grader_version='v2')))
        return run
    case.backend.run.side_effect = fake
    report = case.run_audit(); assert report['status'] == 'controls_failed'
    assert len(report['checks']) == 1
    shutil.rmtree(case.output)
    def malformed(*args):
        run = original(*args); run['stdout'] = '{}'; return run
    case.backend.run.side_effect = malformed
    assert case.run_audit()['status'] == 'grader_error'


def test_missing_attestation_and_timeout_remain_distinct(case):
    def corrupt(*args):
        run = case.emulate(*args); run['attestation'] = {}; return run
    case.backend.run.side_effect = corrupt
    assert case.run_audit()['status'] == 'containment_error'
    shutil.rmtree(case.output)
    def timeout(*args):
        run = case.emulate(*args); run['status'] = 'timeout'; return run
    case.backend.run.side_effect = timeout
    assert case.run_audit()['status'] == 'grader_timeout'


def test_valid_grade_with_wrong_declared_oracle_version_is_an_error(case):
    case.change(lambda value: value['task'].update(grader_version='unmatched-version'))
    assert case.run_audit()['status'] == 'grader_error'
    assert case.backend.run.call_count == 1


def test_original_mutation_stops_remaining_controls_and_retains_private_receipt(case):
    def mutate(*args):
        run = case.emulate(*args); (case.root/'oracle/expected.json').write_text('{}'); return run
    case.backend.run.side_effect = mutate
    report = case.run_audit()
    assert report['status'] == 'invalid_input'
    assert case.backend.run.call_count == 1
    assert json.loads((case.output/'review.json').read_text())['status'] == 'invalid_input'


def test_unsupported_or_expired_preflight_never_launches_worker(case):
    case.backend.capability.return_value = {'status':'unsupported'}
    assert case.run_audit()['status'] == 'blocked'
    case.backend.run.assert_not_called()
    shutil.rmtree(case.output)
    ticks = iter([0, 2])
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: next(ticks, 2)):
        assert case.run_audit(budget_seconds=1)['status'] == 'budget_exhausted'
    case.backend.run.assert_not_called()


def test_capability_latency_counts_against_same_budget(case):
    ticks = iter([0, .1, 2])
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: next(ticks, 2)):
        assert case.run_audit(budget_seconds=1)['status'] == 'budget_exhausted'
    assert case.backend.capability.call_args.kwargs['seconds'] == pytest.approx(.9, rel=0, abs=5e-8)
    case.backend.run.assert_not_called()


def test_grader_latency_exhausts_parent_budget_without_a_second_control(case):
    clock = [0]
    def delayed(*args):
        run = case.emulate(*args); clock[0] = 2; return run
    case.backend.run.side_effect = delayed
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]):
        assert case.run_audit(budget_seconds=1)['status'] == 'budget_exhausted'
    assert case.backend.run.call_count == 1


def test_integrity_check_latency_cannot_dispatch_after_parent_deadline(case):
    clock = [0]; probed = [False]
    def capability(**kwargs):
        probed[0] = True; return {'status':'available'}
    def expensive(path):
        result = digest(path)
        if probed[0] and Path(path) == case.root/'fixture': clock[0] = 2
        return result
    case.backend.capability.side_effect = capability
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]), \
         patch('agent_harness_lab.evaluation.regression_controls.digest', side_effect=expensive):
        assert case.run_audit(budget_seconds=1)['status'] == 'budget_exhausted'
    case.backend.run.assert_not_called()


def test_final_integrity_latency_cannot_report_pass_after_parent_deadline(case):
    clock = [0]; final_checks = [0]
    def expensive(path):
        result = digest(path)
        if case.backend.run.call_count == 5 and Path(path) == case.root/'fixture':
            final_checks[0] += 1
            if final_checks[0] == 2: clock[0] = 2
        return result
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]), \
         patch('agent_harness_lab.evaluation.regression_controls.digest', side_effect=expensive):
        assert case.run_audit(budget_seconds=1)['status'] == 'budget_exhausted'
    assert case.backend.run.call_count == 5


def test_preexec_failure_retains_a_typed_receipt_without_running_other_controls(case):
    case.backend.run.side_effect = subprocess.SubprocessError('SYNTHETIC_PRIVATE_PREEXEC_ERROR')
    report = case.run_audit()
    assert report['status'] == 'grader_error'
    assert case.backend.run.call_count == 1
    assert 'SYNTHETIC_PRIVATE_PREEXEC_ERROR' not in (case.output/'review.json').read_text()


def test_late_control_missing_artifact_fails_before_any_grader_launch(case):
    (case.root/'oracle/controls/superficial/result.json').unlink()
    case.change(lambda value: (value['task'].update(grader_digest=digest(case.root/'oracle')),
        value['controls'][-1].update(digest=digest(case.root/'oracle/controls/superficial'))))
    with pytest.raises(OSError): case.run_audit()
    case.backend.run.assert_not_called(); assert not case.output.exists()


def test_private_source_and_grader_output_remain_private_and_out_of_receipt(case):
    value = json.loads(case.source.read_text()); value['source']['kind'] = 'private'
    case.source.write_text(json.dumps(value))
    case.change(lambda value: (value.update(proposal_sha256=hashlib.sha256(case.source.read_bytes()).hexdigest()),
                               value['task'].update(source_kind='private')))
    def noisy(*args):
        run = case.emulate(*args)
        envelope = json.loads(run['stdout']); envelope['untrusted_message'] = 'SYNTHETIC_PRIVATE_SENTINEL'
        run['stdout'] = json.dumps(envelope); run['stderr'] = 'SYNTHETIC_PRIVATE_SENTINEL'
        return run
    case.backend.run.side_effect = noisy
    report = case.run_audit()
    assert report['status'] == 'grader_controls_passed'
    assert report['privacy'] == 'private'; assert not report['publication_allowed']
    assert 'SYNTHETIC_PRIVATE_SENTINEL' not in (case.output/'review.json').read_text()


def test_existing_output_and_overlap_cannot_overwrite_inputs(case):
    case.output.mkdir(); (case.output/'keep').write_text('preserve')
    with pytest.raises(FileExistsError): case.run_audit()
    assert (case.output/'keep').read_text() == 'preserve'
    case.output = case.root/'fixture/review'
    with pytest.raises(ValueError, match='overlap'): case.run_audit()
    case.backend.run.assert_not_called()


def test_partial_profiles_or_runtime_substitution_are_rejected(case):
    for key in ('cgroup_root', 'image', 'disk_bytes'):
        previous = getattr(case.backend, key); setattr(case.backend, key, None)
        with pytest.raises(ValueError): case.run_audit()
        setattr(case.backend, key, previous)
    case.backend.image.sha256 = 'f'*64
    with pytest.raises(ValueError, match='runtime image'): case.run_audit()
    case.backend.run.assert_not_called()


def test_parent_traversal_cannot_hide_output_overlap(case):
    (case.root/'alias').mkdir()
    case.output = case.root/'alias/../fixture/review'
    with pytest.raises(ValueError, match='canonical'): case.run_audit()
    assert not (case.root/'fixture/review').exists()
    case.backend.run.assert_not_called()


def test_cli_requires_explicit_profile_and_preserves_plain_validation(case):
    args = ['regression', '--proposal', str(case.source), '--draft', str(case.draft)]
    with pytest.raises(SystemExit) as exit: main(args)
    assert exit.value.code == 2
    with patch('builtins.print'):
        assert main(['regression', '--proposal', str(case.source)]) == 0
    args += ['--output', str(case.output), '--cgroup-root', str(case.root/'cgroup'),
             '--image-root', str(case.root/'image'), '--image-sha256', 'e'*64, '--disk-bytes', str(64*1024*1024)]
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=case.backend), patch('builtins.print') as out:
        assert main(args) == 0
    assert json.loads(out.call_args.args[0])['status'] == 'grader_controls_passed'


def test_parent_deadline_only_narrows_and_rejects_nonfinite_values(case):
    for invalid in (True, float('inf'), float('nan')):
        with pytest.raises(ValueError): case.run_audit(parent_deadline=invalid)
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', return_value=0):
        assert case.run_audit(parent_deadline=-1)['status'] == 'budget_exhausted'
    case.backend.run.assert_not_called(); case.backend.capability.assert_not_called()
    shutil.rmtree(case.output)
    ticks = iter([0, 2])
    with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: next(ticks, 2)):
        assert case.run_audit(budget_seconds=1, parent_deadline=100)['status'] == 'budget_exhausted'
    case.backend.run.assert_not_called()
