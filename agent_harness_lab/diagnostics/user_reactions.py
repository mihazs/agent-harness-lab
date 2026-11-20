"""Where users intervened: user reactions counted by the agent's tool calls just before them.

Content-free by construction: a row holds a reaction class, tokens of the closed tool, command and extension vocabularies,
and counts. No user text, path, identifier or digest is kept. A row is an observed pattern (where), never a cause (why).
"""
from collections import Counter

from ..telemetry.session_signals import extract
from . import instruction_adherence as adherence
from .brief import BUILT_IN, OVERFLOW

TOP_ROWS = 8
MAX_LOCUS_CALLS = 5
NONE = 'none'
MEANING = ('A row says where users intervened (the agent tool calls just before a user reaction), not why. '
           'No rows means no reaction was classified in the sessions read, not that users never intervened.')
# Sentence shapes of the adherence report, strongest first, read here without an operand; a turn that matches none is placed by position.
_LEXICAL = (('revert_requested', adherence._REVERT), ('task_reopened', adherence._REOPEN), ('instruction_restated', adherence._CORRECTION))


def _locus(calls):
    """(tool label, command family, extensions) of the last calls before a reaction; a value outside the vocabularies is `other`."""
    family, extensions = NONE, set()
    for call in calls[-MAX_LOCUS_CALLS:]:
        if 'command' in call:
            family = extract('Bash', dict(command=call['command'])).get('command_family', OVERFLOW)
        if 'path' in call:
            extensions.update(extract('Read', dict(file_path=call['path'])).get('file_extensions', [OVERFLOW]))
    tool = NONE if not calls else calls[-1]['tool'] if calls[-1]['tool'] in BUILT_IN else OVERFLOW
    return tool, family, tuple(sorted(extensions))


def summarize(sessions):
    """The brief section over raw sessions (turns of role user, agent or tool_call) the adherence pass read."""
    occurrences, seen = Counter(), {}
    for number, session in enumerate(sessions):
        turns, start = session['turns'], 0
        for index, turn in enumerate(turns):
            if turn['role'] != 'user':
                continue
            before, span, start = turns[index - 1] if index else None, turns[start:index], index + 1
            # Only the first user turn after agent activity counts, and an answer to the agent's question is not a reaction.
            if before is None or before['role'] == 'user' or turn.get('reply_to_question') or adherence._question(before):
                continue
            reaction = next((name for name, shape in _LEXICAL if shape.search(turn['text'])),
                            'steering' if before['role'] == 'tool_call' else 'followup_after_stop')
            key = (reaction, *_locus([one for one in span if one['role'] == 'tool_call']))
            occurrences[key] += 1
            seen.setdefault(key, set()).add(number)
    ranked = sorted(occurrences, key=lambda key: (-len(seen[key]), -occurrences[key], key))
    rows = [dict(reaction=key[0], after_tool=key[1], command_family=key[2], file_extensions=list(key[3]), sessions=len(seen[key]),
                 occurrences=occurrences[key]) for key in ranked[:TOP_ROWS]]
    return dict(authority='descriptive_only', status='completed', meaning=MEANING, sessions_read=len(sessions), rows=rows, rows_total=len(ranked))


def not_run(reason):
    return dict(authority='descriptive_only', status='not_run', reason=reason, meaning=MEANING)
