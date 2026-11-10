"""Fixed, content-free focus hints for an explicitly requested improvement run."""

# These are conventions to investigate, never evidence that a change helps or authority to edit.
HINTS = {
    'instruction_constraints_not_detected': 'Investigate whether project instruction constraints need clarification.',
    'instruction_verification_command_not_detected': 'Investigate whether project verification guidance is missing.',
    'instruction_procedure_section_detected': 'Investigate whether a project instruction procedure belongs in a skill or path rule.',
    'instruction_empty_import': 'Investigate whether an empty instruction import is intentional.',
    'instruction_launch_lines_above_soft_budget': 'Investigate whether the project instruction chain can be simplified safely.',
    'instruction_chain_truncated_at_default_project_doc_bytes': 'Investigate the assumed default Codex instruction budget truncation.',
    'instruction_chain_skipped_at_default_project_doc_bytes': 'Investigate the assumed default Codex instruction budget truncation.',
    'instruction_chain_truncated_at_declared_project_doc_bytes': 'Investigate the declared Codex instruction budget truncation.',
    'instruction_chain_skipped_at_declared_project_doc_bytes': 'Investigate the declared Codex instruction budget truncation.',
}


def from_findings(findings, runtime):
    """Return only an allowlisted fixed hint, with no report path, contents, counts or finding identity."""
    if runtime not in ('claude-code', 'codex'):
        raise ValueError('unsupported audit runtime')
    for row in findings:
        reason = row.get('reason')
        if row.get('dimension') == 'instructions' and row.get('status') in ('weak', 'missing') and reason in HINTS:
            return dict(reason=reason, focus=HINTS[reason], runtime=runtime, status='hypothesis', authority='none',
                        publication_allowed=False)
    return None
