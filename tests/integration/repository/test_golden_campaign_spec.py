"""A saved synthetic campaign remains a private, manually reviewed declaration."""
import json
from pathlib import Path

import pytest

from agent_harness_lab.campaigns.schema import CampaignSpec

GOLDEN = Path(__file__).resolve().parents[2]/'support/golden_profiles/ahl-campaign-spec-v1.json'


def test_saved_campaign_spec_preserves_identity_and_review_policy():
    saved = json.loads(GOLDEN.read_text())
    spec = CampaignSpec.parse(saved)
    assert spec.to_dict() == saved
    assert spec.digest == 'a1d1b5dddf20d4784295be0cfad707840c85cae75d64303dbb9e423451ffeea3'
    assert spec.retry_policy == 'manual_reconciliation_only' and spec.approval_policy == 'explicit_review'
    assert spec.publication_allowed is False and spec.automatic_promotion is False
    with pytest.raises(ValueError, match='unsupported campaign declaration'):
        CampaignSpec.parse(saved | {'profile': 'ahl-campaign-spec-v999'})
