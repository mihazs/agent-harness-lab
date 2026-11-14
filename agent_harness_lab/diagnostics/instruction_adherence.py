"""Evidence-linked findings where an agent ignored a harness rule or a user-prompt instruction.

Input is explicit, private and already captured: harness instruction files and a sessions file of turns. Extraction and
checks are deterministic and lexical (instruction_rules); a rule is judged against tool calls with three-valued results
(violated, satisfied, unknown), using the vocabulary of the controller trajectory assertions (forbidden, required,
sequence) but never their engine, which accepts only controller-issued live captures. Findings carry digests, turn
indexes and stable references, never prompt bodies, paths or session identifiers. A finding is an observed pattern or a
possible violation, never a cause; unknown capture, loading or lineage never becomes a violation, a success or an
independence label. A model judge is not part of this module.
"""
import argparse
from collections import Counter
import json
import posixpath
import re
import shlex
import sys
from pathlib import Path

from ..telemetry.schema import message_fingerprint
from . import instruction_rules as rules

PROFILE = 'ahl-instruction-adherence-v1'
SESSIONS_PROFILE = 'ahl-instruction-sessions-v1'
CAPTURE = ('complete', 'partial', 'unknown')
LOADING = ('loaded', 'truncated', 'not_loaded', 'unknown')
LAYERS = ('project', 'global')
MECHANISMS = dict(forbidden='forbidden_action_taken', required='required_step_skipped', before='order_violated',
                  gated='approval_not_sought', scope='scope_exceeded', halt='stop_not_honored')
CLAIMED = ('observed_violation', 'possible_violation')
MAX_BYTES = 16 * 1024 * 1024
MAX_SESSIONS = 5000
MAX_TURNS = 20000
MAX_TEXT = 65536
MAX_FINDINGS = 5000
MAX_TURN_REFS = 5
MAX_USER_ITEMS = 200
_SEPARATORS = re.compile(r'\s*(?:&&|\|\||;|\||\n)\s*')
_WRAPPERS = ('sudo', 'time', 'nohup', 'command', 'exec')
_SUPERSEDE = re.compile(r"\b(?:actually|never\s*mind|scratch\s+that|disregard|go\s+ahead|you\s+(?:can|may)|it'?s\s+(?:fine|ok|okay)|changed\s+my\s+mind|ignore\s+(?:that|the\s+previous|my\s+previous))\b", re.IGNORECASE)
_CORRECTION = re.compile(r"\b(?:i\s+(?:already\s+)?(?:told|said|asked)|as\s+i\s+(?:said|mentioned)|again)\b", re.IGNORECASE)
LIMITATIONS = (
    'Lexical extraction: only a backticked command or tool, or a path, makes a sentence machine-checkable; the rest is reviewable or unverifiable.',
    'A finding is an observed pattern or a possible violation, never a cause, and a harness rule counts as ignored only when it was loaded.',
    'Restatement, supersession and clarification are read from sentence shapes; a model judge is not used and revert requests are not read.',
    'Calibration labels are authored synthetic fixtures: they measure that the documented rules behave as documented, not accuracy on real sessions.')


def _bounded(value, name, limit=MAX_TEXT, empty=False):
    if not isinstance(value, str) or (not value and not empty) or len(value) > limit or '\0' in value:
        raise ValueError(f'{name} must be a bounded string')
    return value


def _fields(value, allowed, required, name):
    if not isinstance(value, dict) or not required <= set(value) <= allowed:
        raise ValueError(f'{name} fields differ from the frozen schema')


def _segments(command):
    """Simple commands of a shell line as token lists; None when the line is absent or does not parse."""
    if not isinstance(command, str):
        return None
    found = []
    for part in _SEPARATORS.split(command):
        try:
            tokens = shlex.split(part)
        except ValueError:
            return None
        while tokens and (tokens[0] in _WRAPPERS or re.fullmatch(r'\w+=\S*', tokens[0])):
            tokens.pop(0)
        if tokens:
            found.append(tokens)
    return found


def _turn(raw, index):
    role = raw.get('role') if isinstance(raw, dict) else None
    if role in ('user', 'agent'):
        _fields(raw, {'role', 'text'}, {'role', 'text'}, 'turn')
        return dict(role=role, text=_bounded(raw['text'], 'turn text', empty=True))
    if role != 'tool_call':
        raise ValueError('turn role must be user, agent or tool_call')
    _fields(raw, {'role', 'tool', 'command', 'path'}, {'role', 'tool'}, 'turn')
    for name in ('command', 'path'):
        if name in raw:
            _bounded(raw[name], name, empty=True)
    return dict(role=role, text='', turn=index, tool=_bounded(raw['tool'], 'tool', 128), segments=_segments(raw.get('command')),
                path=raw.get('path') or None, command=raw.get('command'))


def _state(value):
    state, begin = (value, 0) if isinstance(value, str) else (value.get('state'), value.get('turn', 0)) if isinstance(value, dict) else (None, 0)
    if state not in LOADING or type(begin) is not int or begin < 0 or isinstance(value, dict) and set(value) - {'state', 'turn'}:
        raise ValueError('loaded entries are a state or {state, turn}')
    return state, begin


def _session(raw, seen):
    _fields(raw, {'session', 'root', 'capture', 'project_dir', 'loaded', 'turns'}, {'session', 'turns'}, 'session')
    identity = _bounded(raw['session'], 'session', 256)
    if identity in seen or not isinstance(raw['turns'], list) or len(raw['turns']) > MAX_TURNS:
        raise ValueError('sessions need distinct ids and at most %d turns' % MAX_TURNS)
    seen.add(identity)
    # An absent capture state is unknown, never complete.
    capture = raw.get('capture', 'unknown')
    project = raw.get('project_dir')
    if capture not in CAPTURE or project is not None and not (isinstance(project, str) and project.startswith('/')):
        raise ValueError('capture must be complete, partial or unknown and project_dir absolute')
    loaded = raw.get('loaded', {})
    if not isinstance(loaded, dict) or len(loaded) > 64:
        raise ValueError('loaded must map at most 64 instruction sources to a state')
    turns = [_turn(turn, index) for index, turn in enumerate(raw['turns'])]
    return dict(ref=rules.digest('session', identity), root_ref=rules.digest('root', _bounded(raw['root'], 'root', 256)) if 'root' in raw else None,
                capture=capture, project=posixpath.normpath(project) if project else None,
                loaded={_bounded(name, 'source', 256): _state(value) for name, value in loaded.items()}, turns=turns,
                calls=[turn for turn in turns if turn['role'] == 'tool_call'])


def load_sessions(value):
    """Validate a sessions document (profile, sessions) into normalized sessions; anything unexpected fails closed."""
    _fields(value, {'profile', 'sessions'}, {'profile', 'sessions'}, 'sessions file')
    if value['profile'] != SESSIONS_PROFILE or not isinstance(value['sessions'], list) or len(value['sessions']) > MAX_SESSIONS:
        raise ValueError('sessions file needs profile %s and at most %d sessions' % (SESSIONS_PROFILE, MAX_SESSIONS))
    seen = set()
    return [_session(raw, seen) for raw in value['sessions']]


def _norm(path, project):
    clean = posixpath.normpath(path)
    prefix = project + '/' if project else None
    return clean[len(prefix):] if prefix and clean.startswith(prefix) else clean


def _within(path, target, project):
    """Whether path is the target file or inside the target directory; None when the spellings cannot be compared."""
    if path is None:
        return None
    path, goal = _norm(path, project), _norm(target, project)
    if path.startswith('/') != goal.startswith('/'):
        return False if project else None
    return path == goal or target.endswith('/') and path.startswith(goal + '/')


def _hit(call, match, project):
    if call['tool'] not in match['tools']:
        return False
    if 'command' in match:
        size = len(match['command'])
        return None if call['segments'] is None else any(part[:size] == match['command'] for part in call['segments'])
    return _within(call['path'], match['path'], project) if 'path' in match else True


def _asked(turns, lo, hi):
    """Whether a question to the user, then a user reply, both fall in turns[lo:hi]."""
    question = next((i for i in range(lo, hi) if _question(turns[i])), None)
    return question is not None and any(turns[i]['role'] == 'user' for i in range(question + 1, hi))


def _question(turn):
    return turn.get('tool') == 'AskUserQuestion' or turn['role'] == 'agent' and turn['text'].rstrip().endswith('?')


def _verdict(rule, session, lo, hi):
    """('violated' | 'satisfied' | 'unknown', turns, reason) of one rule over the calls in turns[lo:hi]."""
    calls, project = [c for c in session['calls'] if lo <= c['turn'] < hi], session['project']
    complete, kind, bad, unsure = session['capture'] == 'complete', rule['kind'], [], False
    scan = lambda match, among=calls: [(call, _hit(call, match, project)) for call in among]
    if kind == 'halt':
        bad = [call['turn'] for call in calls]
    elif kind in ('forbidden', 'scope'):
        pairs = scan(rule['match'], calls if kind == 'forbidden' else [c for c in calls if c['tool'] in rule['match']['tools']])
        bad = [call['turn'] for call, hit in pairs if hit is (kind == 'forbidden')]
        unsure = any(hit is None for _, hit in pairs)
    elif kind == 'required':
        hits = [hit for _, hit in scan(rule['match'])]
        if True in hits:
            return 'satisfied', [], None
        unsure = None in hits
        if hi <= lo:
            return 'unknown', [], 'empty_window'
        bad = [lo, hi - 1] if complete and not unsure else []
    else:
        firsts = scan(rule['first']) if kind == 'before' else []
        for call, hit in scan(rule['then'] if kind == 'before' else rule['match']):
            unsure |= hit is None
            if not hit:
                continue
            if kind == 'before':
                earlier = [h for c, h in firsts if c['turn'] < call['turn']]
                unsure |= True not in earlier and None in earlier
                if True not in earlier and None not in earlier:
                    bad.append(call['turn'])
            elif not _asked(session['turns'], lo, call['turn']):
                bad.append(call['turn'])
        # Absence of an earlier step or a question is only evidence when nothing was dropped from the capture.
        bad, unsure = ([], True) if bad and not complete else (bad, unsure)
    if bad:
        return 'violated', bad, None
    if unsure:
        return 'unknown', [], 'undecidable_call'
    return ('satisfied', [], None) if complete else ('unknown', [], 'capture_partial')


def inventory(harness):
    """Classify every harness instruction; rows keep the rule, summaries keep only counts and digests."""
    found, summary = [], []
    for source in harness:
        key = f"{source['layer']}:{source['name']}"
        counts = Counter()
        for row in rules.extract(source['text']):
            if len(found) >= rules.MAX_RULES:
                raise ValueError('harness instruction count bound exceeded')
            counts[row['klass']] += 1
            ref = rules.digest('harness', key, rules.key(row['rule']) if row['rule'] else ' '.join(re.findall(r'\w+', row['sentence'].casefold())))
            found.append(dict(row, ref=ref, layer=source['layer'], source=key))
        summary.append(dict(source_ref=rules.digest('source', key), layer=source['layer'], **{name: counts[name] for name in rules.CLASSES}))
    return found, sorted(summary, key=lambda row: row['source_ref'])


def _user_items(session):
    """User-turn instructions as items with a window of calls after the prompt, ended by supersession."""
    turns, items, sentences = session['turns'], [], {}
    for index, turn in enumerate(turns):
        if turn['role'] != 'user':
            continue
        sentences[index] = [sentence.lower() for _, sentence in rules.sentences(turn['text'])]
        for row in rules.extract(turn['text'], user=True):
            fingerprint = message_fingerprint(row['sentence'], PROFILE)
            ref = rules.digest('user_prompt', rules.key(row['rule']) if row['rule'] else fingerprint or '')
            if row['klass'] != 'unverifiable' and (row['rule'] or fingerprint) and len(items) < MAX_USER_ITEMS:
                items.append(dict(row, ref=ref, direction='user_prompt', layer='prompt', turn=index, lo=index + 1, hi=len(turns),
                                  until=len(turns), loading='loaded', begin=0))
    at = {(item['ref'], item['turn']) for item in items}
    for item in items:
        for later in (u for u in sentences if u > item['turn']):
            same = (item['ref'], later) in at
            named = [s for s in sentences[later] if any(subject in s for subject in rules.subjects(item['rule'] or {}))]
            if item['rule'] and not same and any(_SUPERSEDE.search(s) for s in named):
                item['until'] = item['hi'] = later
                break
        if item['rule'] and item['rule']['kind'] == 'halt':
            item['hi'] = min(item['hi'], next((u for u in sentences if u > item['turn']), len(turns)))
        item['restated'] = next((u for u in sentences if item['turn'] < u < item['until'] and _restates(item, at, sentences[u], u, turns)), None)
    return items


def _restates(item, at, sentences, later, turns):
    repeated = (item['ref'], later) in at
    corrected = any(_CORRECTION.search(s) and any(subject in s for subject in rules.subjects(item['rule'] or {})) for s in sentences)
    between = [turn for turn in turns[item['turn'] + 1:later] if turn['role'] != 'user']
    # A repeat straight after a question is the answer to a clarification, not a restatement.
    return (repeated or corrected) and bool(between) and not _question(between[-1])


def _polarity(rule):
    kind = rule['kind']
    return [(rules.key(rule['match']), -1)] if kind == 'forbidden' else [(rules.key(rule['match']), 1)] if kind == 'required' \
        else [(rules.key(rule['first']), 1)] if kind == 'before' else []


def _conflicts(items):
    """Pairs of rules in force together that require and forbid the same operand, found through a polarity index."""
    index, pairs = {}, []
    for item in items:
        for operand, sign in _polarity(item['rule']):
            index.setdefault(operand, {-1: [], 1: []})[sign].append(item)
    for sides in index.values():
        pairs += [(one, other) for one in sides[-1] for other in sides[1] if one['ref'] != other['ref'] and one['lo'] < other['hi'] and other['lo'] < one['hi']]
    return pairs


def _finding(session, item, status, mechanism, turns, evidence, detail, **extra):
    refs = extra.pop('refs', [item['ref']])
    record = dict(direction=extra.pop('direction', item['direction']), status=status, mechanism=mechanism, instruction_ref=refs[0],
                  source_layer=extra.pop('layer', item['layer']), loading=item['loading'], session_ref=session['ref'], root_ref=session['root_ref'],
                  lineage='declared' if session['root_ref'] else 'unknown', span=dict(first_turn=min(turns), last_turn=max(turns)),
                  occurrences=len(turns), turns=turns[:MAX_TURN_REFS], corroboration=extra.pop('corroboration', []),
                  evidence_sha256=rules.digest('evidence', *evidence))
    if len(refs) > 1:
        record['instruction_refs'] = refs
    if detail:
        record['detail'] = dict(instruction=item['sentence'][:240])
    record['finding_id'] = rules.digest('finding', record['direction'], status, mechanism, *refs, session['ref'])
    return record


def _session_findings(session, harness, detail):
    """Findings and decision counts for one session; an unknown capture decides nothing."""
    counts = Counter()
    if session['capture'] == 'unknown':
        counts['undecided_unknown_capture'] = sum(1 for row in harness if row['rule'])
        return [], counts, Counter()
    n = len(session['turns'])
    items = [dict(row, direction='harness', turn=None, lo=0, hi=n, until=n, restated=None,
                  loading=session['loaded'].get(row['source'], ('unknown', 0))[0], begin=session['loaded'].get(row['source'], ('unknown', 0))[1])
             for row in harness if row['rule']]
    users = _user_items(session)
    prompts = Counter(row['klass'] for row in users)
    items += [item for item in users if item['rule']]
    conflicted, findings = set(), {}
    for one, other in _conflicts([item for item in items if item['loading'] != 'not_loaded']):
        conflicted |= {one['ref'], other['ref']}
        direction = one['direction'] if one['direction'] == other['direction'] else 'mixed'
        refs = sorted((one['ref'], other['ref']))
        finding = _finding(session, one, 'conflict', 'conflicting_instructions', [one['lo'], other['lo']], refs, detail, direction=direction, refs=refs,
                           layer=one['layer'] if one['layer'] == other['layer'] else 'mixed')
        findings[finding['finding_id']] = finding
    for item in items:
        verdict, bad, reason = _verdict(item['rule'], session, item['lo'], item['hi'])
        counts['violated' if verdict == 'violated' else 'satisfied' if verdict == 'satisfied' else 'undecided_' + reason] += 1
        corroboration = ['user_restated'] if item['restated'] is not None else []
        if verdict == 'violated' and item['ref'] not in conflicted:
            # Only calls at or after the turn the rule was loaded can have ignored it.
            live = [turn for turn in bad if turn >= item['begin']]
            state, bad = (item['loading'], live) if live else ('not_loaded', bad)
            call = next((c for c in session['calls'] if c['turn'] == bad[0]), dict(tool='', command='', path=''))
            status = 'not_loaded' if state == 'not_loaded' else 'observed_violation' if state == 'loaded' else 'possible_violation'
            found = _finding(session, dict(item, loading=state), status, MECHANISMS[item['rule']['kind']], bad,
                             [str(call['tool']), str(call.get('command')), str(call['path']), rules.key(item['rule'])], detail, corroboration=corroboration)
            findings.setdefault(found['finding_id'], found)
    for item in users:
        if item['restated'] is not None and item['ref'] not in conflicted and not any(
                f['instruction_ref'] == item['ref'] for f in findings.values()) and (not item['rule'] or _undecided(item, session)):
            found = _finding(session, item, 'possible_violation', 'instruction_restated', [item['turn'], item['restated']],
                             ['restated', item['ref'], str(item['turn'])], detail)
            findings.setdefault(found['finding_id'], found)
    return list(findings.values()), counts, prompts


def _undecided(item, session):
    return _verdict(item['rule'], session, item['lo'], item['hi'])[0] == 'unknown'


def _recurrence(findings):
    groups = {}
    for finding in findings:
        group = groups.setdefault((finding['instruction_ref'], finding['mechanism'], finding['status'], finding['direction']), dict(sessions=set(), roots=set(), unknown=set(), occurrences=0))
        group['sessions'].add(finding['session_ref'])
        group['occurrences'] += finding['occurrences']
        (group['roots'] if finding['root_ref'] else group['unknown']).add(finding['root_ref'] or finding['session_ref'])
    return [dict(instruction_ref=ref, mechanism=mechanism, status=status, direction=direction, sessions=len(group['sessions']),
                 independent_roots=len(group['roots']), unknown_lineage_sessions=len(group['unknown']), occurrences=group['occurrences'])
            for (ref, mechanism, status, direction), group in sorted(groups.items())]


def analyze(sessions, harness, *, admitted=None, detail=False):
    """The deterministic report; statuses outside admitted (None admits all) are abstentions, not findings."""
    rows, summary = inventory(harness)
    found, decisions, prompts = [], Counter(), Counter()
    for session in sessions:
        one, counts, kinds = _session_findings(session, rows, detail)
        found += one
        decisions.update(counts)
        prompts.update(kinds)
    found = sorted(found, key=lambda f: f['finding_id'])
    admit = lambda f: admitted is None or f['status'] not in CLAIMED or f['status'] in admitted
    kept = [f for f in found if admit(f)]
    below = [dict(f, abstention='below_calibrated_threshold') for f in found if not admit(f)]
    unknown = [s['ref'] for s in sessions if s['capture'] == 'unknown']
    return dict(profile=PROFILE, status='completed', authority='descriptive_only', causal_diagnosis=False, promotion_authority=False,
                redaction='private_detail' if detail else 'digest_only', publication_allowed=False,
                inventory=dict(sources=summary, user_prompt_instructions={name: prompts[name] for name in rules.CLASSES[:2]}),
                sessions=dict(total=len(sessions), partial=sum(s['capture'] == 'partial' for s in sessions), unknown_capture=len(unknown),
                              unknown_capture_refs=sorted(unknown)[:50], declared_roots=len({s['root_ref'] for s in sessions if s['root_ref']}),
                              unknown_lineage=sum(s['root_ref'] is None for s in sessions)),
                decisions=dict(sorted(decisions.items())), findings=kept[:MAX_FINDINGS], abstentions=below[:MAX_FINDINGS], recurrence=_recurrence(kept),
                counts=dict(sorted(Counter(f['status'] for f in kept).items())), truncated=max(len(kept), len(below)) > MAX_FINDINGS, limitations=list(LIMITATIONS))


def build(sessions, harness, *, detail=False):
    """analyze() with the calibrated admission policy and its reviewed-sample measurements attached."""
    from . import adherence_calibration
    admitted, calibration = adherence_calibration.calibrate()
    return dict(analyze(sessions, harness, admitted=admitted, detail=detail), calibration=calibration)


def summary(report):
    counts, sessions, calibration = report['counts'], report['sessions'], report['calibration']
    interval = lambda name: '%.2f [%.2f, %.2f]' % (calibration[name]['value'], *calibration[name]['wilson95']) if calibration[name]['value'] is not None else 'n/a'
    return '\n'.join((
        'ahl instruction-adherence: sessions=%d unknown_capture=%d findings=%d abstentions=%d' % (
            sessions['total'], sessions['unknown_capture'], len(report['findings']), len(report['abstentions'])),
        '  ' + ' '.join(f'{name}={counts.get(name, 0)}' for name in (*CLAIMED, 'not_loaded', 'conflict')),
        '  calibration (reviewed synthetic sample, n=%d): precision %s, recall %s, abstention %s' % (
            calibration['sample']['cases'], interval('precision'), interval('recall'), interval('abstention_rate')),
        '  report is private, redacted by default and descriptive only; it names no cause'))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl instruction-adherence', description='Report where captured sessions ignored harness rules or user-prompt instructions. '
        'Deterministic, offline and read-only toward its inputs; no model, network or inference. Findings are observed patterns with digests and turn indexes, never causes.',
        epilog='Exit: 0 report printed; 2 invalid input. Output is private JSON; a short summary goes to stderr. Redacted by default.')
    parser.add_argument('sessions', type=Path, help='Existing JSON {"profile":"%s","sessions":[...]} of explicitly captured sessions' % SESSIONS_PROFILE)
    parser.add_argument('--rules', action='append', default=[], metavar='LAYER=FILE', help='Existing harness instruction file for layer project or global; repeatable')
    parser.add_argument('--scope', choices=('project', 'global', 'project+global'), default='project', help='Layers whose rule files may be read (default: project)')
    parser.add_argument('--output', type=Path, help='New private file for the JSON report instead of stdout')
    parser.add_argument('--private-detail', action='store_true', help='Add each instruction sentence (at most 240 characters) to findings; the report stays private')
    args = parser.parse_args(argv)
    from ..evaluation.integrity import read_regular
    from ..private_journal import record, write_bytes
    try:
        harness = []
        for entry in args.rules:
            layer, _, name = entry.partition('=')
            if layer not in LAYERS or not name or layer not in args.scope.split('+'):
                raise ValueError('--rules LAYER=FILE needs a layer inside --scope: ' + ' '.join(args.scope.split('+')))
            harness.append(dict(layer=layer, name=Path(name).name, text=read_regular(Path(name), rules.MAX_SOURCE_BYTES).decode('utf-8')))
        if len({(h['layer'], h['name']) for h in harness}) != len(harness) or len(harness) > 16:
            raise ValueError('rule files need distinct layer and file name pairs, at most 16')
        report = build(load_sessions(record(args.sessions, MAX_BYTES)[0]), harness, detail=args.private_detail)
        text = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n'
        if args.output:
            write_bytes(args.output, text.encode())
        else:
            sys.stdout.write(text)
    except (OSError, ValueError, UnicodeError) as error:
        print(f'ahl instruction-adherence: {error}', file=sys.stderr)
        return 2
    print(summary(report), file=sys.stderr)
    return 0
