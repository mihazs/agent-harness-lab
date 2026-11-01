"""Synthetic draft audits with simulated containment; kernel evidence is separate."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import agent_harness_lab
from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.regression_controls import audit
if __package__:
    from .test_regression_proposals import proposal
else:
    from test_regression_proposals import proposal


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


class RegressionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.draft = make_draft(self.root)
        self.output = self.root/'review'
        self.backend = SimpleNamespace(cgroup_root=self.root/'cgroup', disk_bytes=64*1024*1024,
            image=SimpleNamespace(root=self.root/'image', sha256='e'*64),
            capability=Mock(return_value={'status':'available'}), run=Mock(side_effect=self.emulate))

    def emulate(self, argv, workspace, inputs, role, limits):
        self.assertEqual(role, 'grader'); self.assertEqual(set(inputs), {'oracle', 'fixture'})
        translated = [arg.replace('/inputs/oracle', str(inputs['oracle'])).replace('/inputs/fixture', str(inputs['fixture'])) for arg in argv]
        run = subprocess.run(translated, cwd=workspace, env={'PATH':'/usr/bin:/bin'}, capture_output=True, text=True, timeout=limits.seconds)
        return dict(status='completed' if run.returncode == 0 else 'failed', exit_code=run.returncode,
            stdout=run.stdout, stderr=run.stderr, inputs_unchanged=True,
            attestation=dict(backend='bubblewrap-offline-v1', role='grader', network='none',
                aggregate_disk_bound_verified=True, cgroup_v2={'empty_after_cleanup':True},
                runtime_image=dict(sha256='e'*64, verified_before=True, verified_after=True)))

    def run_audit(self, **kwargs):
        return audit(self.source, self.draft, self.output, self.backend, **kwargs)

    def change(self, edit):
        value = json.loads(self.draft.read_text()); edit(value)
        self.draft.write_text(json.dumps(value))

    def test_complete_frozen_controls_pass_without_admission_or_historical_verification(self):
        before = digest(self.root/'fixture'), digest(self.root/'oracle')
        report = self.run_audit()
        self.assertEqual(report['status'], 'grader_controls_passed')
        self.assertEqual([c['observed'] for c in report['checks']], [False, False, True, True, False])
        self.assertEqual(self.backend.run.call_count, 5)
        self.assertEqual(before, (digest(self.root/'fixture'), digest(self.root/'oracle')))
        self.assertEqual(report['draft_review_status'], 'unreviewed')
        for key in ('bank_admission', 'task_failure_verified', 'automatic_promotion', 'publication_allowed'):
            self.assertFalse(report[key])
        self.assertEqual((self.output.stat().st_mode & 0o777), 0o700)
        self.assertEqual(((self.output/'review.json').stat().st_mode & 0o777), 0o600)
        self.assertEqual(json.loads((self.output/'review.json').read_text()), report)

    def test_every_identity_and_control_is_validated_before_any_worker(self):
        mutations = [lambda v: v.update(proposal_sha256='f'*64),
            lambda v: v['task'].update(root_id='other'), lambda v: v['task'].update(review_status='accepted'),
            lambda v: v['task'].update(partition='certification'), lambda v: v['task'].update(independence='reviewed'),
            lambda v: v['task'].update(fixture_digest='f'*64), lambda v: v['task'].update(artifacts=['../escape']),
            lambda v: v['controls'][-1].update(digest='f'*64), lambda v: v['controls'][-1].update(state='complete')]
        original = self.draft.read_bytes()
        for edit in mutations:
            with self.subTest(edit=edit):
                self.change(edit)
                with self.assertRaises(ValueError): self.run_audit()
                self.backend.run.assert_not_called(); self.assertFalse(self.output.exists())
                self.draft.write_bytes(original)

    def test_always_passing_oracle_and_malformed_grade_cannot_pass_controls(self):
        original = self.emulate
        def fake(*args):
            run = original(*args); run.update(status='completed', exit_code=0,
                stdout=json.dumps(dict(schema_version=1, valid=True, outcome=True, grader_id='fake', grader_version='v2')))
            return run
        self.backend.run.side_effect = fake
        report = self.run_audit(); self.assertEqual(report['status'], 'controls_failed')
        self.assertEqual(len(report['checks']), 1)
        shutil.rmtree(self.output)
        def malformed(*args):
            run = original(*args); run['stdout'] = '{}'; return run
        self.backend.run.side_effect = malformed
        self.assertEqual(self.run_audit()['status'], 'grader_error')

    def test_missing_attestation_and_timeout_remain_distinct(self):
        def corrupt(*args):
            run = self.emulate(*args); run['attestation'] = {}; return run
        self.backend.run.side_effect = corrupt
        self.assertEqual(self.run_audit()['status'], 'containment_error')
        shutil.rmtree(self.output)
        def timeout(*args):
            run = self.emulate(*args); run['status'] = 'timeout'; return run
        self.backend.run.side_effect = timeout
        self.assertEqual(self.run_audit()['status'], 'grader_timeout')

    def test_valid_grade_with_wrong_declared_oracle_version_is_an_error(self):
        self.change(lambda value: value['task'].update(grader_version='unmatched-version'))
        self.assertEqual(self.run_audit()['status'], 'grader_error')
        self.assertEqual(self.backend.run.call_count, 1)

    def test_original_mutation_stops_remaining_controls_and_retains_private_receipt(self):
        def mutate(*args):
            run = self.emulate(*args); (self.root/'oracle/expected.json').write_text('{}'); return run
        self.backend.run.side_effect = mutate
        report = self.run_audit()
        self.assertEqual(report['status'], 'invalid_input')
        self.assertEqual(self.backend.run.call_count, 1)
        self.assertEqual(json.loads((self.output/'review.json').read_text())['status'], 'invalid_input')

    def test_unsupported_or_expired_preflight_never_launches_worker(self):
        self.backend.capability.return_value = {'status':'unsupported'}
        self.assertEqual(self.run_audit()['status'], 'blocked')
        self.backend.run.assert_not_called()
        shutil.rmtree(self.output)
        ticks = iter([0, 2])
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: next(ticks, 2)):
            self.assertEqual(self.run_audit(budget_seconds=1)['status'], 'budget_exhausted')
        self.backend.run.assert_not_called()

    def test_capability_latency_counts_against_same_budget(self):
        ticks = iter([0, .1, 2])
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: next(ticks, 2)):
            self.assertEqual(self.run_audit(budget_seconds=1)['status'], 'budget_exhausted')
        self.assertAlmostEqual(self.backend.capability.call_args.kwargs['seconds'], .9)
        self.backend.run.assert_not_called()

    def test_grader_latency_exhausts_parent_budget_without_a_second_control(self):
        clock = [0]
        def delayed(*args):
            run = self.emulate(*args); clock[0] = 2; return run
        self.backend.run.side_effect = delayed
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]):
            self.assertEqual(self.run_audit(budget_seconds=1)['status'], 'budget_exhausted')
        self.assertEqual(self.backend.run.call_count, 1)

    def test_integrity_check_latency_cannot_dispatch_after_parent_deadline(self):
        clock = [0]; probed = [False]
        def capability(**kwargs):
            probed[0] = True; return {'status':'available'}
        def expensive(path):
            result = digest(path)
            if probed[0] and Path(path) == self.root/'fixture': clock[0] = 2
            return result
        self.backend.capability.side_effect = capability
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.evaluation.regression_controls.digest', side_effect=expensive):
            self.assertEqual(self.run_audit(budget_seconds=1)['status'], 'budget_exhausted')
        self.backend.run.assert_not_called()

    def test_final_integrity_latency_cannot_report_pass_after_parent_deadline(self):
        clock = [0]; final_checks = [0]
        def expensive(path):
            result = digest(path)
            if self.backend.run.call_count == 5 and Path(path) == self.root/'fixture':
                final_checks[0] += 1
                if final_checks[0] == 2: clock[0] = 2
            return result
        with patch('agent_harness_lab.evaluation.regression_controls.time.monotonic', side_effect=lambda: clock[0]), \
             patch('agent_harness_lab.evaluation.regression_controls.digest', side_effect=expensive):
            self.assertEqual(self.run_audit(budget_seconds=1)['status'], 'budget_exhausted')
        self.assertEqual(self.backend.run.call_count, 5)

    def test_preexec_failure_retains_a_typed_receipt_without_running_other_controls(self):
        self.backend.run.side_effect = subprocess.SubprocessError('SYNTHETIC_PRIVATE_PREEXEC_ERROR')
        report = self.run_audit()
        self.assertEqual(report['status'], 'grader_error')
        self.assertEqual(self.backend.run.call_count, 1)
        self.assertNotIn('SYNTHETIC_PRIVATE_PREEXEC_ERROR', (self.output/'review.json').read_text())

    def test_late_control_missing_artifact_fails_before_any_grader_launch(self):
        (self.root/'oracle/controls/superficial/result.json').unlink()
        self.change(lambda value: (value['task'].update(grader_digest=digest(self.root/'oracle')),
            value['controls'][-1].update(digest=digest(self.root/'oracle/controls/superficial'))))
        with self.assertRaises(OSError): self.run_audit()
        self.backend.run.assert_not_called(); self.assertFalse(self.output.exists())

    def test_private_source_and_grader_output_remain_private_and_out_of_receipt(self):
        value = json.loads(self.source.read_text()); value['source']['kind'] = 'private'
        self.source.write_text(json.dumps(value))
        self.change(lambda value: (value.update(proposal_sha256=hashlib.sha256(self.source.read_bytes()).hexdigest()),
                                   value['task'].update(source_kind='private')))
        def noisy(*args):
            run = self.emulate(*args)
            envelope = json.loads(run['stdout']); envelope['untrusted_message'] = 'SYNTHETIC_PRIVATE_SENTINEL'
            run['stdout'] = json.dumps(envelope); run['stderr'] = 'SYNTHETIC_PRIVATE_SENTINEL'
            return run
        self.backend.run.side_effect = noisy
        report = self.run_audit()
        self.assertEqual(report['status'], 'grader_controls_passed')
        self.assertEqual(report['privacy'], 'private'); self.assertFalse(report['publication_allowed'])
        self.assertNotIn('SYNTHETIC_PRIVATE_SENTINEL', (self.output/'review.json').read_text())

    def test_existing_output_and_overlap_cannot_overwrite_inputs(self):
        self.output.mkdir(); (self.output/'keep').write_text('preserve')
        with self.assertRaises(FileExistsError): self.run_audit()
        self.assertEqual((self.output/'keep').read_text(), 'preserve')
        self.output = self.root/'fixture/review'
        with self.assertRaisesRegex(ValueError, 'overlap'): self.run_audit()
        self.backend.run.assert_not_called()

    def test_partial_profiles_or_runtime_substitution_are_rejected(self):
        for key in ('cgroup_root', 'image', 'disk_bytes'):
            previous = getattr(self.backend, key); setattr(self.backend, key, None)
            with self.assertRaises(ValueError): self.run_audit()
            setattr(self.backend, key, previous)
        self.backend.image.sha256 = 'f'*64
        with self.assertRaisesRegex(ValueError, 'runtime image'): self.run_audit()
        self.backend.run.assert_not_called()

    def test_parent_traversal_cannot_hide_output_overlap(self):
        (self.root/'alias').mkdir()
        self.output = self.root/'alias/../fixture/review'
        with self.assertRaisesRegex(ValueError, 'canonical'): self.run_audit()
        self.assertFalse((self.root/'fixture/review').exists())
        self.backend.run.assert_not_called()

    def test_cli_requires_explicit_profile_and_preserves_plain_validation(self):
        args = ['regression', '--proposal', str(self.source), '--draft', str(self.draft)]
        with self.assertRaises(SystemExit) as exit: main(args)
        self.assertEqual(exit.exception.code, 2)
        with patch('builtins.print'):
            self.assertEqual(main(['regression', '--proposal', str(self.source)]), 0)
        args += ['--output', str(self.output), '--cgroup-root', str(self.root/'cgroup'),
                 '--image-root', str(self.root/'image'), '--image-sha256', 'e'*64, '--disk-bytes', str(64*1024*1024)]
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=self.backend), patch('builtins.print') as out:
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(out.call_args.args[0])['status'], 'grader_controls_passed')
