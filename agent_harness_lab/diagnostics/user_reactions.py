"""Where users intervened: user reactions counted by the agent's tool calls just before them.

Content-free by construction: a row holds a reaction class, tokens of the closed tool, command and extension vocabularies,
and counts. No user text, path, identifier or digest is kept. A row is an observed pattern (where), never a cause (why).
"""
from collections import Counter
import re

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


def _events(sessions):
    """(session number, reaction, locus, user turn) for each user turn that reacts to agent activity."""
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
            yield number, reaction, _locus([one for one in span if one['role'] == 'tool_call']), turn


def summarize(sessions):
    """The brief section over raw sessions (turns of role user, agent or tool_call) the adherence pass read."""
    occurrences, seen = Counter(), {}
    for number, reaction, locus, _ in _events(sessions):
        key = (reaction, *locus)
        occurrences[key] += 1
        seen.setdefault(key, set()).add(number)
    ranked = sorted(occurrences, key=lambda key: (-len(seen[key]), -occurrences[key], key))
    rows = [dict(reaction=key[0], after_tool=key[1], command_family=key[2], file_extensions=list(key[3]), sessions=len(seen[key]),
                 occurrences=occurrences[key]) for key in ranked[:TOP_ROWS]]
    return dict(authority='descriptive_only', status='completed', meaning=MEANING, sessions_read=len(sessions), rows=rows, rows_total=len(ranked))


def not_run(reason):
    return dict(authority='descriptive_only', status='not_run', reason=reason, meaning=MEANING)


# Opt-in only (improve --correction-excerpts): everything below keeps user text, scrubbed and bounded, for the proposer session.
CORRECTIONS_PROFILE = 'ahl-user-corrections-v1'
CORRECTIONS_ARTIFACT = 'user-corrections.json'
MAX_CORRECTIONS = 12
MAX_PER_SESSION = 2
MAX_EXCERPT = 240
# English and Portuguese shapes of a correction; they only select a follow-up for an excerpt, never change the content-free rows.
_CORRECTIONS = tuple(re.compile(shape, re.IGNORECASE) for shape in (
    r"\b(?:no|not|wrong|don'?t|do\s+not|stop|still|instead|again|revert|undo)\b",
    r"\bi\s+(?:said|told|asked)\b",
    r"(?<!\w)(?:não|nao|errad[oa]|de\s+novo|novamente|revert[ae]\w*|desfa[zç]\w*|par[ae]|ainda|em\s+vez)(?!\w)",
    r"(?<!\w)eu\s+(?:disse|falei|pedi)(?!\w)"))
_SCRUB = tuple((re.compile(shape), token) for shape, token in (
    (r'-----BEGIN[\s\S]*?(?:-----END[^-]*-----|\Z)', '<secret>'),
    (r'\b(?:sk-|ghp_|gho_)[A-Za-z0-9_\-]{8,}|\bAKIA[0-9A-Z]{12,}', '<secret>'),
    (r'\b[A-Za-z][A-Za-z0-9+.\-]*://\S+', '<url>'),
    (r'[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+', '<email>'),
    (r'(?<![\w/<.~])(?:~?/|[A-Za-z]:\\)[^\s\'"`<>]+', '<path>'),
    # shortcut: a 20+ character run counts as a secret only with a digit in it, so long plain identifiers survive; tighten if a real key shape slips through.
    (r'(?<![A-Za-z0-9+/=_\-])(?=[A-Za-z+/=_\-]*\d)[A-Za-z0-9+/=_\-]{20,}', '<secret>'),
    (r'\d{6,}', '<number>')))
_ONLY_PLACEHOLDERS = re.compile(r'<(?:path|url|email|secret|number)>|[\W_]')


def scrub(text):
    """The bounded excerpt of a user turn with paths, URLs, emails, secret shapes and long numbers replaced; None when nothing else is left."""
    for shape, token in _SCRUB:
        text = shape.sub(token, text)
    text = ' '.join(text.split())[:MAX_EXCERPT]
    return text if _ONLY_PLACEHOLDERS.sub('', text) else None


def corrections(sessions):
    """The private user-corrections document: scrubbed excerpts of user turns that corrected the agent, newest session first."""
    rows = []
    for number, session in enumerate(sessions):
        kept = 0
        for _, reaction, locus, turn in _events([session]):
            # A turn the runtime wrote (a tagged wrapper, an interrupt marker) is not the user's words.
            if kept == MAX_PER_SESSION or len(rows) == MAX_CORRECTIONS or turn['text'].lstrip().startswith(('<', '[Request interrupted')):
                continue
            if reaction == 'followup_after_stop' and not any(shape.search(turn['text']) for shape in _CORRECTIONS):
                continue
            if (excerpt := scrub(turn['text'])) is not None:
                kept += 1
                rows.append(dict(correction_id=f'c{len(rows) + 1}', reaction=reaction, after_tool=locus[0], command_family=locus[1],
                                 file_extensions=list(locus[2]), excerpt=excerpt))
    return dict(profile=CORRECTIONS_PROFILE, authority='untrusted_evidence_only', rows=rows)
