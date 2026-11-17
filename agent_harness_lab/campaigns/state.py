"""Which campaign lifecycle state may follow which; a table only, with no executor, store or budget."""

_NEXT = {
    None: ('planned',),
    'planned': ('ready', 'cancelled'),
    'ready': ('running', 'blocked', 'cancelled'),
    'running': ('awaiting_review', 'paused', 'blocked', 'interrupted', 'budget_exhausted', 'cancelled', 'failed'),
    'paused': ('running', 'cancelled'),
    'blocked': ('ready', 'cancelled'),
    # Leaves interrupted only by an explicit reconciliation (running) or an operator decision, never on its own.
    'interrupted': ('running', 'cancelled', 'failed'),
    'budget_exhausted': ('awaiting_review', 'cancelled'),
    'awaiting_review': ('completed', 'cancelled'),
    'completed': (), 'cancelled': (), 'failed': (),
}
STATES = tuple(state for state in _NEXT if state is not None)
TERMINAL = frozenset(state for state, following in _NEXT.items() if state is not None and not following)


def may_follow(previous, state):
    """True only for a known state that the table allows after `previous` (None for a new campaign)."""
    return type(state) is str and (previous is None or type(previous) is str) and state in _NEXT.get(previous, ())
