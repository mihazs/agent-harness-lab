"""What a later loop run carries from the earlier one: mechanism dispositions and a labelled historical score, never a trial.

A later `ahl improve` loop starts from the harness that is on disk and evaluates its baseline fresh. From the previous loop run
(read by incremental.begin, whose detection of applied, ignored and edited harness files is consumed here, not repeated) it takes
the dispositions rejected, inconclusive and deferred, so a mechanism an earlier run judged is not offered again, and the earlier
scores as a historical observation beside the fresh ones. The previous score is never the baseline evaluation, never a fresh
trial and never an input to a decision: only the dispositions reach the controller.

The dispositions are reopened, never silently kept, when the frozen inputs differ (the earlier verdicts were measured on another
bank, grader or runtime, so the scores are also not comparable and no delta is shown) or when the harness was edited outside the
earlier candidate (a changed component). Anything unknown reopens too. Carried state is bounded; what is pruned is counted and
marked truncated, and a pruned entry is never read as a success or an independence label.
"""
from agent_harness_lab import incremental
import hashlib
import json
import re

from .controller import CARRIED
from .errors import LoopError
from .receipt import CARRY_PROFILE as PROFILE, MAX_CARRIED as MAX_MECHANISMS, MAX_HISTORY, PROFILE as RECEIPT, validate_receipt

KEEP = ('applied', 'ignored', 'no_candidate')  # #806 relations after which the earlier dispositions still describe this harness
_SETTLED = ('accepted', 'improved')  # a mechanism whose candidate was kept is done, not carried


def evidence_versions(brief):
    """Fingerprint distinct observed rows with references; missing or ambiguous evidence is unknown."""
    rows = brief.get('mechanisms') if isinstance(brief, dict) else None
    if not isinstance(rows, list) or len(rows) > MAX_MECHANISMS: return {}
    versions, seen = {}, set()
    for row in rows:
        if not isinstance(row, dict): continue
        name, refs = row.get('mechanism_id'), row.get('evidence_refs')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_./:-]{1,128}', name): continue
        if name in seen:
            versions.pop(name, None); continue
        seen.add(name)
        if not isinstance(refs, list) or not refs or row.get('counts_are_lower_bounds') is not False: continue
        try: encoded = json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        except (TypeError, ValueError): continue
        versions[name] = hashlib.sha256(encoded).hexdigest()
    return versions


def previous_block(receipt):
    """The loop block of the previous run's workflow.json when it is a complete and honest one, else None."""
    block = (receipt or {}).get('loop') if isinstance(receipt, dict) else None
    if not isinstance(block, dict) or block.get('profile') != RECEIPT: return None
    try: return validate_receipt(block)
    except LoopError: return None


def _score(block, name):
    found = (block.get(name) or {}).get('score')
    return found if isinstance(found, dict) else None


def carry(previous, run, relation, frozen_inputs):
    """The carry_over block for one run: previous is a validated loop block, run its run name, relation #806's disposition of the
    harness since then and frozen_inputs this run's digest. dispositions are what the controller receives."""
    comparable = previous['frozen_inputs_sha256'] == frozen_inputs
    reopened = None if comparable and relation in KEEP else 'frozen_inputs_changed' if not comparable else 'harness_edited' if relation in ('edited', 'partially_applied') else 'harness_relation_unknown'
    older = (previous.get('carry_over') or {})
    known = {**(older.get('mechanisms') or {}).get('carried', {}), **{name: item for name, item in previous['mechanisms'].items() if item in CARRIED}}
    known = {name: item for name, item in known.items() if previous['mechanisms'].get(name) not in _SETTLED and item in CARRIED}
    kept = dict(list(known.items())[-MAX_MECHANISMS:])
    entry = dict(run=run, stop=previous['stop_criterion'], spec_sha256=previous['spec']['digest'], frozen_inputs_sha256=previous['frozen_inputs_sha256'],
                 best_round=previous.get('best_round'), best=_score(previous, 'after'))
    earlier = [item for item in older.get('history', []) if isinstance(item, dict)]
    history = ([entry] + earlier)[:MAX_HISTORY]
    return dict(profile=PROFILE, status='reopened' if reopened else 'carried', previous_run=run, previous_stop=previous['stop_criterion'], relation=relation,
                comparable=comparable, **({'reason': reopened} if reopened else {}),
                historical=dict(label='historical_observation', run=run, comparison='comparable' if comparable else 'not_comparable',
                                before=_score(previous, 'before'), best=_score(previous, 'after'),
                                best_snapshot=(previous.get('after') or {}).get('snapshot')),
                mechanisms=dict(carried={} if reopened else kept, reopened=sorted(known)[:MAX_MECHANISMS] if reopened else []),
                history=history, truncated=dict(mechanisms=len(known) > MAX_MECHANISMS or bool((older.get('truncated') or {}).get('mechanisms')),
                                                history=len(earlier) + 1 > MAX_HISTORY or bool((older.get('truncated') or {}).get('history'))),
                recommendation_only=True, automatic_promotion=False)


def read(state, original, frozen_inputs, evidence=None):
    """The carry-over of this run from the previous run incremental.begin found, or None for a first run.

    state is the incremental state, original this run's harness file states and frozen_inputs this run's digest. Returns
    {'block': the carry_over block, 'dispositions': what the controller gets}. Nothing is carried from a previous run that
    was not a valid loop run, or that could not be read: that is stated, and the run starts as a first one."""
    if state is None: return None
    unavailable = lambda reason, **more: dict(block=dict(profile=PROFILE, status='unavailable', reason=reason, **more, recommendation_only=True, automatic_promotion=False), dispositions={})
    if state.get('problem'): return unavailable(state['problem'])
    prior = state.get('prior')
    if prior is None: return None
    found = incremental.compare(prior['record'], incremental._plain(original), state['scope'])
    # A harness file edited outside the earlier candidate is a changed component whatever the candidate's own disposition was.
    relation = 'edited' if found['disposition'] in KEEP and 'edited' in found.get('paths', {}).values() else found['disposition']
    previous = previous_block(prior['receipt'])
    if previous is None: return unavailable('previous_run_not_a_loop', previous_run=prior['run'], relation=relation)
    block = carry(previous, prior['run'], relation, frozen_inputs)
    versions = previous.get('mechanism_evidence') or {}
    evidence = evidence or {}
    held = block['mechanisms']['carried']
    changed = sorted(name for name in held if name not in evidence or versions.get(name) != evidence[name])
    if changed:
        block['mechanisms']['carried'] = {name: value for name, value in held.items() if name not in changed}
        block['mechanisms']['reopened'] = changed
        block['status'], block['reason'] = 'reopened', 'mechanism_evidence_changed_or_unknown'
    return dict(block=block, dispositions=block['mechanisms']['carried'])


def finish(block, loop):
    """Add what only the finished run knows: the fresh scores beside the historical ones and, for identical frozen inputs, the
    difference between them. A fresh score is the one this run measured; a historical one is never moved into its place."""
    fresh = dict(label='fresh_evaluation', before=_score(loop, 'before'), best=_score(loop, 'after'))
    shown = {}
    old = (block.get('historical') or {}).get('best')
    if block.get('comparable') and fresh['before'] and old: shown = dict(fresh_baseline_minus_historical_best=round(fresh['before']['point'] - old['point'], 6))
    elif block.get('status') in ('carried', 'reopened'): shown = dict(delta='omitted', delta_reason='frozen_inputs_changed' if not block.get('comparable') else 'score_unavailable')
    return dict(block, fresh=fresh, **shown)
