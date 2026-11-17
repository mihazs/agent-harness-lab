"""A synthetic frozen plan retains its Bayesian declaration and identity."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.evaluation.bayes_spec import BayesDecisionError
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule

GOLDEN = Path(__file__).resolve().parents[2]/'support/golden_profiles/bayesian-rope-decision-v1-plan.json'


def test_saved_bayesian_declaration_preserves_plan_and_rejects_unknown_profile():
    saved = json.loads(GOLDEN.read_text())
    plan = ExperimentSpec.parse(saved)
    plan.validate()
    assert json.loads(json.dumps(plan.to_dict())) == saved
    assert plan.digest == 'f3399cbbd2b1d6bd3d5c11dd4c9344a501336022eab31e0c470598b19f088a63'
    assert len(schedule(plan)) == 24 and plan.bayes_decision['allow_bound_resolved_verdict'] is False
    saved['bayes_decision']['profile'] = 'bayesian-rope-decision-v999'
    with pytest.raises(BayesDecisionError, match='bayes_decision.profile'):
        ExperimentSpec.parse(saved).validate()
