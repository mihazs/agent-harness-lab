"""Typed loop declaration and ordering errors; each reason carries one remediation step and never echoes a value."""

REASONS = {
    'loop_declaration_invalid': 'Write the loop declaration as one JSON object that uses only the documented loop fields.',
    'loop_field_unknown': 'Remove the unknown field; the loop declares accept_score, accept_basis, regression_margin, noise_margin, max_rounds, plateau_rounds, on_regression and alpha.',
    'loop_authority_refused': 'Remove the authority field; a loop declaration sets a bar, never apply, global scope, promotion or certification, and the loop only recommends.',
    'loop_accept_score_invalid': 'Set accept_score to a number above 0 and at most 1, or to a non-empty map of declared area ids to such numbers.',
    'areas_not_declared': 'Declare capability areas in the ahl.json areas list before giving accept_score as a per-area map, or give one number.',
    'loop_area_undeclared': 'Use only area ids declared in the ahl.json areas list as accept_score keys.',
    'loop_accept_basis_invalid': 'Set accept_basis to lower_bound, the only basis this release accepts, or omit it.',
    'loop_margin_invalid': 'Set regression_margin and noise_margin to numbers from 0 up to but not including 1.',
    'loop_max_rounds_invalid': 'Set max_rounds to a whole number from 1 to 10, or omit it for the default of 3.',
    'loop_plateau_rounds_invalid': 'Set plateau_rounds to a whole number from 1 up to max_rounds, or omit it to use the smaller of 2 and max_rounds.',
    'loop_on_regression_invalid': 'Set on_regression to stop or rollback_continue, or omit it for stop.',
    'loop_alpha_invalid': 'Set alpha to a number above 0 and below 1, or omit it for 0.05.',
    'loop_limits_invalid': 'Pass the project limits as seconds above 0 and a whole number of trials above 0.',
    'loop_port_invalid': 'This is an AHL adapter defect: the evaluator, proposer or clock returned something outside its port contract; report it with the named field.',
    'loop_ledger_invalid': 'Keep the round ledger in a new owner-only directory outside the project that is not another store, then start the loop again.',
    'loop_ledger_full': 'The round ledger reached its event cap, which is above anything a run within max_rounds writes; report it, and start a new ledger for a new run.',
    'loop_requires_frozen_bank': 'Pin a task bank in ahl.json (bank.path and bank.sha256, with development, calibration and certification or transfer tasks) and use the cooperative runtime it names, then rerun; the loop evaluates every round on one frozen bank, so it never falls back to a single pass.',
    'loop_receipt_invalid': 'This is an AHL defect, not an input defect: the loop receipt failed its own validation, so it was not reported as a result; report it with the named field. Nothing was applied.',
    'loop_order_violation': 'This is an AHL ordering defect, not an input defect: improve only after a valid evaluation of the current best and report a result only after a post-improvement evaluation; report it with the named state and event.',
}

# AHL's own defects: an invalid adapter, ledger, ordering or receipt, never something the declaration or the harness did.
DEFECTS = frozenset({'loop_port_invalid', 'loop_ledger_invalid', 'loop_ledger_full', 'loop_order_violation', 'loop_receipt_invalid'})


class LoopError(ValueError):
    """A typed loop violation; str() names the reason, field and source, never the offending value."""

    def __init__(self, reason, field=None, source=None):
        self.reason, self.field, self.source, self.remediation = reason, field, source, REASONS[reason]
        self.next_step, self.stage = self.remediation, 'run' if reason in DEFECTS else 'usage'  # the typed exit receipt reads both
        super().__init__(f'{reason}: field={field or "-"} source={source or "-"}. Next: {self.remediation}')

    def receipt(self):
        return dict(reason=self.reason, field=self.field, source=self.source, next_step=self.remediation)
