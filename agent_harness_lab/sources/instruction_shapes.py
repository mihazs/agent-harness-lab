"""Bounded lexical and structural hints over captured instruction text; never retains text."""
import re

CONSTRAINT = re.compile(r'\b(?:always|never|must|shall|required|forbidden)\b', re.IGNORECASE)
VERIFY = re.compile(r'\b(?:pytest|unittest|npm\s+(?:test|run\s+(?:build|test|check|verify))|'
                    r'cargo\s+(?:test|build|check)|go\s+(?:test|build)|make\s+(?:test|check|build|verify)|'
                    r'gradle\s+(?:test|build)|mvn\s+(?:test|verify)|ruff\s+check)\b', re.IGNORECASE)
HEADING = re.compile(r'^\s{0,3}#{1,6}\s')
NUMBERED = re.compile(r'^\s{0,3}\d+[.)]\s+')
FENCE = re.compile(r'^\s{0,3}(`{3,}|~{3,})')


def analyze(text):
    """Return boolean cues only; thresholds are conventions, not behavioral evidence."""
    constraint = bool(CONSTRAINT.search(text))
    verify = bool(VERIFY.search(text))
    numbered = blocks = 0
    procedure = False
    fence = None
    for line in text.splitlines():
        match = FENCE.match(line)
        if match:
            marker = match.group(1)
            if fence is None:
                fence = marker
                blocks += 1
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None: continue
        if HEADING.match(line):
            procedure |= numbered >= 5 or blocks >= 3
            numbered = blocks = 0
        elif NUMBERED.match(line):
            numbered += 1
    return constraint, verify, procedure or numbered >= 5 or blocks >= 3
