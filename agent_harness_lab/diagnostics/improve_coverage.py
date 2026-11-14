"""Admit the proposer's mechanisms into a private coverage report beside the ``ahl improve`` receipt (#10).

Admission and coverage are bookkeeping over unverified hypotheses: nothing here reviews a route,
evaluates a mechanism, charges a budget or says which mechanism a combined patch repaired. Every
admitted record stays ``proposed`` and unreviewed, so a tie or an accepted patch never closes one.
"""
import hashlib

from agent_harness_lab.diagnostics import brief as analysis_brief
from agent_harness_lab.diagnostics.coverage import COVERAGE_PROFILE, render_markdown, report
from agent_harness_lab.diagnostics.mechanisms import from_proposal
from agent_harness_lab.monitoring.state import atomic_json

# The controller owns these proposal fields; the admission contract does not know them.
CONTROLLER_FIELDS = ('instruction_plan',)


def citable(brief):
    """Evidence identifiers the proposer was shown: observed brief mechanisms and signals, never unknowns."""
    if not isinstance(brief, dict):
        return frozenset()
    return frozenset([row['mechanism_id'] for row in brief.get('mechanisms', [])]
                     + [row['id'] for row in brief.get('weak_outcome_signals', []) if row.get('status') == 'observed_pattern'])


def admit(proposal, brief, component_version):
    """The admission of one candidate proposal against the brief its proposer saw."""
    version = 'brief:' + hashlib.sha256(analysis_brief.rendered(brief)).hexdigest()[:16] if isinstance(brief, dict) else 'brief_unavailable'
    return from_proposal({key: value for key, value in proposal.items() if key not in CONTROLLER_FIELDS},
                         evidence_refs=citable(brief), evidence_version=version, component_version=component_version)


def attach(output, admissions):
    """Write the private admission receipts and coverage report; return the compact receipt field.

    ``admissions`` is one ``admit`` result per candidate attempt. Capture stays ``unknown``: the
    controller has no declaration that every mechanism was seen. Records repeated across attempts
    with different content are listed as conflicts by the report, never merged.
    """
    for attempt, admission in enumerate(admissions):
        atomic_json(output / f'mechanism-admission-{attempt}.json', admission.receipt)
    body = report([record for admission in admissions for record in admission.records])
    atomic_json(output / 'mechanism-coverage.json', body)
    (output / 'mechanism-coverage.md').write_text(render_markdown(body))
    return dict(profile=COVERAGE_PROFILE, authority='none', publication_allowed=False, causal_claim=False,
                artifact='mechanism-coverage.json', report='mechanism-coverage.md', sha256=body['sha256'],
                capture=body['capture'], mechanisms=body['totals']['mechanisms'], coverage=body['totals']['coverage'],
                coverage_complete=body['coverage_complete'], limitations=body['limitations'],
                admission=[dict(attempt=attempt, artifact=f'mechanism-admission-{attempt}.json',
                                status=admission.status, sha256=admission.receipt['sha256'], admitted=len(admission.records),
                                rejected=len(admission.receipt['rejected']),
                                rejection_reasons=sorted({row['reason'] for row in admission.receipt['rejected']}))
                           for attempt, admission in enumerate(admissions)])
