"""Preparation outcome (#822): what the preparation stage returned, as allow-listed codes and counts.

Free text of the proposal (reason, mechanisms, falsifiers) never enters the receipt. Every field is the proposer's own declaration,
matched against a closed vocabulary; the controller does not verify it, and a field left out is unknown, never a clean result.
"""
PROFILE = 'ahl-preparation-outcome-v1'
OUTCOMES = ('hypothesis_supplied', 'no_change_supported', 'no_hypothesis', 'invalid_proposal')
REASON_CODES = ('no_verified_outcome', 'pattern_not_recurring', 'too_few_independent_roots', 'capture_unverified',
                'cause_outside_harness', 'baseline_conforms', 'evidence_ambiguous')
MAX_LISTED = 32  # entries read per declared list; the rest count as unrecognized


def check_classes():
    """Baseline convention classes a proposer may report as passed: the ones the static audit evaluates."""
    from .audit.recheck import APPLICABLE
    return sorted({name for names in APPLICABLE.values() for name in names})


def guidance():
    """The prompt text that asks for the typed fields; any other entry is dropped and only counted."""
    return ('Also add to proposal.json bounded lists of exact names, never prose: "reason_codes" (why no repair is supported) from '
            + ', '.join(REASON_CODES) + '; "missing_evidence" from the ids in the brief\'s missing_evidence; "baseline_checks_passed" '
            '(baseline conventions you checked and found conforming) from ' + ', '.join(check_classes())
            + '. Entries outside these lists are discarded.')


def _named(value, allowed):
    """The recognized, deduplicated names of one declared list, and how many entries were not recognized."""
    if value is None: return [], 0
    if not isinstance(value, list): return [], 1
    read = value[:MAX_LISTED]
    kept = {item for item in read if isinstance(item, str) and item in allowed}
    return sorted(kept), sum(1 for item in read if not (isinstance(item, str) and item in kept)) + len(value) - len(read)


def block(proposal, *, identical=False, brief=None):
    """The receipt block for the last proposal the stage returned; ``proposal`` None means no usable one was written.

    ``identical`` marks a candidate that left the baseline unchanged: it supplied no hypothesis."""
    status = proposal.get('status') if isinstance(proposal, dict) else None
    if status not in ('candidate', 'no_change'):
        outcome, proposal = 'invalid_proposal', {}
    reasons, bad_reasons = _named(proposal.get('reason_codes'), REASON_CODES)
    listed = {row.get('id') for row in (brief or {}).get('missing_evidence', []) if isinstance(row, dict)}
    missing, bad_missing = _named(proposal.get('missing_evidence'), listed)
    checks, bad_checks = _named(proposal.get('baseline_checks_passed'), check_classes())
    if status == 'candidate': outcome = 'no_hypothesis' if identical else 'hypothesis_supplied'
    elif status == 'no_change': outcome = 'no_change_supported' if reasons else 'no_hypothesis'
    return dict(profile=PROFILE, outcome=outcome, reason_codes=reasons, missing_evidence=missing,
                baseline_checks_passed=dict(count=len(checks), classes=checks), unrecognized=bad_reasons + bad_missing + bad_checks,
                declared_by='proposer', controller_verified=False)


def line(value):
    """The one-line terminal form of a block."""
    names = lambda items: ','.join(items) or 'none'
    return (f"ahl: preparation outcome={value['outcome']} reasons={names(value['reason_codes'])} "
            f"missing_evidence={names(value['missing_evidence'])} baseline_checks_passed={value['baseline_checks_passed']['count']}")


def section(value):
    """The Markdown section appended to report.md."""
    names = lambda items: ', '.join(f'`{item}`' for item in items) or 'none'
    return (f"\n## Preparation outcome\n\nThe preparation stage returned `{value['outcome']}`. The fields below are the proposer's own "
            f"declaration, matched to a fixed vocabulary; AHL did not verify them.\n\n- Reason codes: {names(value['reason_codes'])}\n"
            f"- Missing evidence it named: {names(value['missing_evidence'])}\n"
            f"- Baseline checks it reported as passed ({value['baseline_checks_passed']['count']}): {names(value['baseline_checks_passed']['classes'])}\n"
            f"- Entries outside the vocabulary, discarded: {value['unrecognized']}\n")
