"""A synthetic saved loop receipt keeps its score and recommendation-only meaning."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.loop.errors import LoopError
from agent_harness_lab.loop.receipt import validate_receipt

GOLDEN = Path(__file__).resolve().parents[2]/'support/golden_profiles/ahl-loop-receipt-v1.json'


def test_saved_loop_receipt_keeps_scored_candidate_and_no_authority():
    saved = json.loads(GOLDEN.read_text())
    assert validate_receipt(saved) is saved
    assert saved['stop_criterion'] == 'acceptance_score_reached' and saved['best_round'] == 1
    assert saved['before']['score']['point'] == 0.25 and saved['after']['score']['point'] == 1.0
    assert saved['rounds'][0]['decision'] == 'accepted' and saved['usage']['trials'] == 24
    assert saved['recommendation_only'] is True and saved['automatic_promotion'] is False and saved['certification'] == 'unverified'
    with pytest.raises(LoopError, match='loop_receipt_invalid'):
        validate_receipt(saved | {'profile': 'ahl-loop-receipt-v999'})
