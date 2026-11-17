"""Synthetic content-free session signal summaries for the advisory area classification (#799); no real session, path or text."""
import json
from pathlib import Path

# One session per built-in area plus the cases that cannot be placed. Names are tool, extension and command-family tokens only.
CODING = dict(session='s-coding', tools={'Read': 6, 'Edit': 4, 'Bash': 3}, extensions={'py': 8, 'md': 1}, commands={'pytest': 2})
UX_UI = dict(session='s-ux-ui', tools={'Read': 3, 'Edit': 3}, extensions={'css': 3, 'tsx': 4}, commands={'playwright': 1})
DOCS = dict(session='s-docs', tools={'Read': 2, 'Write': 2}, extensions={'md': 6, 'rst': 1})
REVIEW = dict(session='s-review', tools={'Read': 5, 'Grep': 3, 'Bash': 3}, commands={'git-diff': 2, 'gh-pr': 1})
AMBIGUOUS = dict(session='s-ambiguous', tools={'Read': 4}, extensions={'py': 3, 'css': 3})
EMPTY = dict(session='s-empty')
BY_AREA = dict(coding=CODING, **{'ux-ui': UX_UI}, docs=DOCS, review=REVIEW)


def sessions(*entries):
    return [dict(entry) for entry in entries]


def write(root, entries, name='sessions.json'):
    """Write the summaries beside the project; returns the path."""
    path = Path(root) / name
    path.write_text(json.dumps(entries))
    return path
