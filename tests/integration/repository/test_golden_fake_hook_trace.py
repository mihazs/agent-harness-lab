"""Synthetic hook-trace input is a fake-runtime observation, not native evidence."""
import json
import os
from pathlib import Path

import pytest

from agent_harness_lab import hooks_validation
from agent_harness_lab.hooks_validation import _decisions, _trace

GOLDEN = Path(__file__).resolve().parents[2] / 'support/golden_profiles/ahl-fake-hook-trace-v1.jsonl'


def test_saved_fake_hook_trace_uses_the_real_parser_and_rejects_bad_types():
    raw = GOLDEN.read_text()
    parsed = _trace(raw)
    assert set(parsed) == {('SessionStart', 0, 0)}
    row, = parsed[('SessionStart', 0, 0)]
    assert _decisions(row) == (set(), set())
    invalid = json.loads(raw); invalid['timed_out'] = 'false'
    assert _trace(json.dumps(invalid)) is None


@pytest.mark.parametrize('containment_available', [True, False])
def test_saved_fake_trace_report_covers_protocol_selection_without_native_claim(tmp_path, monkeypatch, containment_available):
    from contextlib import ExitStack

    from agent_harness_lab.cli import main
    from agent_harness_lab.hooks_validation import validate
    from tests.integration.surfaces.test_hooks_validation import FakeRuntimeCase
    from tests.unit.evaluation.test_benchmark_contracts import record

    if not containment_available:
        monkeypatch.setattr(hooks_validation, '_subreaper', lambda value: None)
    with ExitStack() as cleanup:
        fixture = FakeRuntimeCase()
        fixture.addCleanup = cleanup.callback
        previous_umask = os.umask(0o022)
        try:
            fixture.set_up(tmp_path)
            _, actual = fixture.check()
        finally:
            os.umask(previous_umask)
        saved = json.loads((GOLDEN.parent / 'ahl-fake-hook-trace-v1-report.json').read_text())
        normalized = json.loads(json.dumps(actual))
        elapsed = normalized['run'].pop('elapsed_seconds')
        assert isinstance(elapsed, (int, float)) and elapsed >= 0
        containment = actual['workspace_diff']['process_containment']
        if containment == 'subreaper':
            assert normalized == saved
        else:
            assert containment == 'unavailable'
            assert 'process_containment_unavailable' in actual['findings']
            assert actual['candidate']['status'] == 'inconclusive'
            assert not actual['candidate']['improvement_eligible']
        assert actual['runtime_kind'] == 'fake' and actual['executed']
        assert actual['handlers'] and all(row['trace'] == 'ahl-fake-hook-trace-v1' for row in actual['handlers'])
        assert not actual['publication_allowed'] and not actual['automatic_promotion']
        with pytest.raises(ValueError, match='unknown hook trace protocol'):
            validate(tmp_path / 'unsupported', 'claude-code', fixture.execute, fixture=fixture.fixture,
                     binary=str(fixture.fake), trace='ahl-fake-hook-trace-v0', environ=fixture.environ,
                     protected=(fixture.fixture,))
        forged = tmp_path / 'forged.json'
        forged.write_text(json.dumps([actual | record() | {'publication_allowed': True}]))
        public = tmp_path / 'public'
        with pytest.raises(SystemExit) as exc:
            main(['benchmark-report', '--input', str(forged), '--output', str(public), '--public'])
        assert exc.value.code == 2 and not public.exists()
