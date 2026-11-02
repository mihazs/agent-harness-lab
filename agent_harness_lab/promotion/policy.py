"""Fail-closed behavioral promotion. Implemented statistics are not certification."""
from dataclasses import dataclass


@dataclass(frozen=True)
class PromotionDecision:
    status: str
    reason: str
    automatic: bool = False


def decide(spec, analysis, *, isolation_verified=False, effective_runtime_verified=False,
           graders_audited=False, lineage_reviewed=False, certification_reserved=False):
    spec.validate()
    if analysis['status'] in ('rejected', 'blocked', 'incomplete'):
        return PromotionDecision(analysis['status'], analysis.get('reason', 'invalid_evidence'))
    if spec.profile != 'certification':
        return PromotionDecision('inconclusive', 'descriptive_smoke_cannot_certify')
    if analysis.get('oracle_validity') != 'current':
        return PromotionDecision('blocked', 'oracle_history_unverified_or_invalidated')
    if not all((isolation_verified, effective_runtime_verified, graders_audited, lineage_reviewed, certification_reserved)):
        return PromotionDecision('blocked', 'certification_prerequisites_unverified')
    if analysis['status'] != 'supported':
        return PromotionDecision('inconclusive', analysis.get('reason', 'uncertain'))
    # Recommendation only until a controller integrates authenticated attestations.
    return PromotionDecision('recommended', 'fixed_final_evidence_supports_declared_claim')



def legacy_coverage(manifest, trials):
    expected = {(version, task['id']) for version in ('baseline', 'candidate') for task in manifest['tasks']}
    observed = [(trial.get('version'), trial.get('task')) for trial in trials if isinstance(trial, dict)]
    return len(observed) == len(trials) == len(expected) and set(observed) == expected
