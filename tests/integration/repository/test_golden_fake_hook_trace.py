"""Synthetic hook-trace input is a fake-runtime observation, not native evidence."""
import json
from pathlib import Path

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
