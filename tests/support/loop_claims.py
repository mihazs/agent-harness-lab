"""The claim a loop message must never make: that the harness is fine or improved without the evaluated evidence behind it (#814)."""
import re

CLAIM = re.compile(r'\b(is|are|looks?|seems?) (fine|good|healthy|improved|better|correct)\b|\bimproved\b|\bno problem', re.I)
NEGATED = 'does not show the harness is fine'  # the one negated phrase the loop's steps use


def claims(text):
    """Whether the text says the harness is fine or improved."""
    return CLAIM.search(text.replace(NEGATED, '')) is not None
