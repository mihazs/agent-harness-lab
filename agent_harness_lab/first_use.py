"""First-use outcome: say per cause why findings became no candidate, and label spend that evaluated nothing.

Counts only; no path, body or identifier. Unknown capture or lineage is never turned into a success, failure or independence label.
"""
from collections import Counter

from .exit_guidance import REMEDIATION

NO_OUTCOME = 'no_outcome_evidence'
LABEL = 'spent_without_evaluation'


def verified_outcomes(data):
    return [row for row in data.get('evaluation_outcomes', [])
            if row.get('outcome_provenance') == 'executable_grader' and row.get('outcome') in ('success', 'failure')]


def hypothesis_sources(args):
    """Declared sources that can seed a candidate without outcome evidence; a static-convention source joins here."""
    return [name for name, present in (('audit_recheck', getattr(args, '_audit_hypothesis', None) is not None),
                                       ('user_focus', getattr(args, 'focus', None) is not None)) if present]


def blocked(data, args):
    """True for a history run whose findings nothing could relate to an outcome and that has no hypothesis source.

    Only a history run reads evaluation results (explicit events, a source root and the closed profile never do), so only
    there can added evidence lift the block; every other run keeps its path."""
    return (bool(getattr(args, 'history', False)) and bool(data['analysis']['findings'])
            and not verified_outcomes(data) and not hypothesis_sources(args))


def explain(data, args, *, prepared):
    """Per cause, how many findings could not become a candidate; causes may overlap and are never an independence claim."""
    findings = data['analysis']['findings']
    causes = {}
    if not verified_outcomes(data):
        causes[NO_OUTCOME] = len(findings)
    if not hypothesis_sources(args):
        causes['no_hypothesis_source'] = len(findings)
    if any(row.get('pin_compatibility') == 'unverified' for row in data.get('sources', [])):
        causes['capture_unverified'] = len(findings)
    if prepared:
        causes['proposer_returned_no_change'] = len(findings)
    return dict(reason='findings_not_actionable', findings=len(findings), kinds=dict(Counter(row['kind'] for row in findings)),
                causes=causes, causes_may_overlap=True, candidates=0)


def attach(result, data, args, *, prepared):
    """Receipts without a candidate carry the per-cause explanation whenever findings exist, and a remediation."""
    if data['analysis']['findings']:
        result['findings_not_candidates'] = explain(data, args, prepared=prepared)
    result.setdefault('remediation', REMEDIATION.get(result.get('reason'), None))
    if result['remediation'] is None: del result['remediation']


def spend_label(result, args, *, prepared, evaluated):
    """Mark a run whose preparation stage called the model while no trial was evaluated; provider spend stays unverified."""
    if not prepared or evaluated:
        return
    stages = getattr(args, '_stages', None)
    result['spend_label'] = LABEL
    result[LABEL] = dict(stages=['preparation'], provider_reported_spend='unverified',
                         preparation_seconds=round(stages.seconds()['preparation'], 1) if stages is not None else 'unknown')
