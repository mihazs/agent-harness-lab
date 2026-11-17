"""Content-free inputs for a synthetic hypothesis probe."""

from agent_harness_lab.diagnostics.brief import BUILT_IN, ERROR_CATEGORIES, NEXT_ACTIONS


def structural_pattern(mechanism):
    """Select only closed structural labels; reject patterns that cannot define one follow-up."""
    if not isinstance(mechanism, dict) or mechanism.get('status') != 'observed_pattern':
        raise ValueError('probe requires an observed pattern')
    tool, category, actions = (mechanism.get(name) for name in ('tool_class', 'error_category', 'next_action'))
    if not isinstance(tool, str) or tool not in BUILT_IN or category not in ERROR_CATEGORIES or not isinstance(actions, dict):
        raise ValueError('probe requires known structural labels')
    if len(actions) > len(NEXT_ACTIONS) or set(actions) - set(NEXT_ACTIONS) or any(type(value) is not int or value < 0 for value in actions.values()):
        raise ValueError('invalid follow-up counts')
    highest = max(actions.values(), default=0)
    selected = [name for name, value in actions.items() if value == highest and value > 0]
    if len(selected) != 1: raise ValueError('probe follow-up is ambiguous')
    return dict(tool_class=tool, error_category=category, follow_up=selected[0])
