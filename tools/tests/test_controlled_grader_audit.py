"""Synthetic Responses and grader routing; no provider, credential read or native agent."""
from contextlib import ExitStack
import json
import time
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import permission_audit, responses


class TestControlledGraderAudit:
    @pytest.fixture(autouse=True)
    def cleanups(self):
        with ExitStack() as self.cleanups: yield

    def evaluate(self, observed):
        import test_sandbox_inference as fixtures
        fixture = fixtures.ResponsesEvaluationTests()
        fixture.setUp()
        self.cleanups.callback(fixture.doCleanups)
        (fixture.root/'baseline/solve.py').write_bytes((fixture.root/'candidate/solve.py').read_bytes())
        fixture.args.permission_audit = permission_audit.PROFILE
        fixture.backend.permission_audit = permission_audit.PROFILE
        fixture.backend.permission_reports = []
        fixture.backend.permission_qualification.side_effect = lambda: dict(
            profile=permission_audit.PROFILE, status='observed',
            qualification=permission_audit.qualification(fixture.backend.permission_reports))
        client = responses.Client(fixture.settings, 'SYNTHETIC_CONTROLLER_KEY', time.monotonic()+60,
                                  transport=fixture.transport)
        fixture.args.inference_client = client
        def execute(*args, **kwargs):
            run = fixture.execute(*args, **kwargs)
            qualification = observed if args[3]=='grader' and fixture.grades==1 else 'qualified'
            audit = dict(status='unknown' if qualification=='unknown' else 'complete', qualification=qualification)
            fixture.backend.permission_reports.append(audit)
            run['attestation']['permission_audit'] = audit
            return run
        fixture.backend.run.side_effect = execute
        with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend', return_value=fixture.backend), \
                patch.object(responses, 'request', side_effect=AssertionError('no external provider')), \
                patch.object(responses, 'read_credential', side_effect=AssertionError('no credential read')), \
                patch.object(fixture.runner, 'execute', side_effect=AssertionError('no native CLI')):
            code = fixture.runner.evaluate(fixture.args)
        result = fixture.fixture.results()
        assert not result['automatic_promotion']
        assert not result['isolation']['full_certification']
        assert result['inputs_unchanged']
        assert 'SYNTHETIC_CONTROLLER_KEY' not in json.dumps(result)
        return fixture, client, code, result

    @pytest.mark.parametrize('observed', ['violated', 'unknown'])
    def test_unqualified_grader_aborts_remaining_inference_without_rewriting_artifact_grade(self, observed):
        fixture, client, code, result = self.evaluate(observed)
        assert code == 1
        assert (fixture.requests, fixture.commands, fixture.grades) == (2, 1, 1)
        assert client.poisoned
        assert result['runtime']['inference']['further_requests_blocked']
        assert result['isolation_qualification']['qualification'] == observed
        assert len(result['trials']) == 14
        first, *remaining = result['trials']
        assert first['status'] == 'passed'
        assert first['grader']['outcome']
        assert first['isolation_qualification'] == observed
        assert all(t['status']=='not_executed' for t in remaining)
        assert all('grader' not in t and 'execution' not in t for t in remaining)

    def test_qualified_grader_preserves_the_full_paired_schedule(self):
        fixture, client, code, result = self.evaluate('qualified')
        assert code == 0
        assert (fixture.requests, fixture.commands, fixture.grades) == (28, 14, 14)
        assert not client.poisoned
        assert all(t['status']=='passed' for t in result['trials'])
        assert result['isolation_qualification']['qualification'] == 'qualified'
