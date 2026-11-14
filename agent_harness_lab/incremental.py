"""Incremental runs: a second ``ahl improve`` reads the first and reports what changed instead of repeating it (#806).

Observed pattern only; nothing here grants adoption, promotion or publication authority. Applied, ignored, partially applied
and edited are read from the bytes and modes of the harness files against the previous run's recorded baseline and frozen
candidate. Regressed is read from a receipt that measured the candidate and never inferred; a candidate that was never
measured is unknown, not unregressed. A measured regression is copied into each later run's record, so the finished comparison
is found whatever ran in between. An earlier score is a labelled historical observation beside the fresh evaluation and is
never reused as a fresh trial. A record that is unreadable or from another scope stays unknown: it is never read as applied,
ignored, regressed or edited, and never as a first run.
"""
import json
from pathlib import Path
import re

from agent_harness_lab.evaluation.integrity import read_regular, relative
from agent_harness_lab.monitoring.state import atomic_json
from agent_harness_lab.sources.schema import digest

PROFILE = 'ahl-incremental-run-v1'
RECORD_PROFILE = 'ahl-incremental-run-record-v1'
RECORD, POINTER = 'harness-states.json', 'latest-run.json'
REPEAT_REASON = 'candidate_already_regressed'
MAX_FILES, LISTED, REGRESSIONS_KEPT = 1000, 20, 50  # an older regression than the newest 50 is forgotten and evaluated again
_HEX, _RUN = re.compile(r'[0-9a-f]{64}'), re.compile(r'run-[A-Za-z0-9_-]{1,80}')
_RERUN = dict(evidence_collection='no_verified_prior_output', candidate_preparation='nonreplayable_model_stage',
              evaluation_construction='fresh_heldout_required', trials='earlier_trials_are_historical_only')
_AUTHORITY = dict(adoption_authority=False, automatic_promotion=False, publication_allowed=False)


def _plain(states):
    """JSON form of a snapshot: {path: [sha256, mode]}."""
    return {path: [state[0], state[1]] for path, state in states.items()}


def _checked(states):
    if not isinstance(states, dict) or len(states) > MAX_FILES: raise ValueError('invalid harness states')
    for path, state in states.items():
        if (not isinstance(path, str) or relative(path) != path or not isinstance(state, list) or len(state) != 2
                or not isinstance(state[0], str) or not _HEX.fullmatch(state[0]) or type(state[1]) is not int or not 0 <= state[1] <= 0o7777):
            raise ValueError('invalid harness state')
    return states


def _read(path, limit):
    value = json.loads(read_regular(path, limit))
    if not isinstance(value, dict): raise ValueError('expected an object')
    return value


def _evidence(entry):
    """One carried regression: the candidate's identity, the run that measured it and the measurement it was read from."""
    seen = entry.get('historical') if isinstance(entry, dict) else None
    return (isinstance(seen, dict) and isinstance(seen.get('frozen_inputs'), (dict, type(None))) and entry.get('cause') == 'candidate_caused'
            and isinstance(entry.get('key'), str) and bool(_HEX.fullmatch(entry['key'])) and isinstance(entry.get('run'), str) and bool(_RUN.fullmatch(entry['run'])))


def _record(path):
    value = _read(path, 1024 * 1024)
    candidate, scope = value.get('candidate'), value.get('scope')
    if (value.get('profile') != RECORD_PROFILE or not (scope is None or isinstance(scope, list) and all(isinstance(name, str) for name in scope))
            or not (candidate is None or isinstance(candidate, dict) and type(candidate.get('attempt')) is int)):
        raise ValueError('invalid incremental record')
    _checked(value['baseline'])
    if candidate is not None: _checked(candidate['states'])
    regressions = value.setdefault('regressions', [])
    if not isinstance(regressions, list) or len(regressions) > REGRESSIONS_KEPT or not all(_evidence(entry) for entry in regressions):
        raise ValueError('invalid regression memory')
    return value


def _save(state):
    atomic_json(state['output'] / RECORD, dict(profile=RECORD_PROFILE, scope=state['scope'], baseline=state['baseline'], candidate=state['candidate'],
                                           regressions=state['regressions']))


def begin(args, output, original, scope):
    """Read the previous run of this project, then record this run's baseline and make it the next run's previous.

    Advisory: a failure is kept as ``problem`` and never stops the run. A run outside the private run store reads the
    previous run but is not recorded.
    """
    from .workflow import identity, state_root
    store, output = state_root() / 'runs' / identity(args.project), Path(output)
    state = dict(output=output, prior=None, problem=None, skipped=False, scope=scope, baseline=_plain(original), candidate=None,
                 regressions=[], recorded=output.parent == store)
    try:
        if _exists(store / POINTER):
            name = _read(store / POINTER, 4096).get('run')
            if not isinstance(name, str) or not _RUN.fullmatch(name): raise ValueError('invalid previous run')
            try: receipt = _read(store / name / 'workflow.json', 8 * 1024 * 1024)
            except (OSError, ValueError): receipt = None  # an unreadable receipt leaves the outcome unknown, not the harness comparison
            prior = dict(run=name, record=_record(store / name / RECORD), receipt=receipt)
            state.update(prior=prior, regressions=carried(prior))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:  # a receipt of the wrong shape is unreadable too, never a crash
        state.update(problem='previous_run_unreadable', error=type(exc).__name__)
    try:
        if state['recorded']:
            _save(state)
            atomic_json(store / POINTER, dict(run=output.name))
    except OSError: state['recorded'] = False  # nothing is claimed about a record that was not written
    return state


def _exists(path):
    return path.exists() or path.is_symlink()


def froze(state, attempt, proposed):
    """Record the frozen candidate; the last one frozen is the one a later run finds applied or ignored."""
    if state is None: return
    state['candidate'] = dict(attempt=attempt, states=_plain(proposed))
    try:
        if state['recorded']: _save(state)
    except OSError: state['recorded'] = False


def compare(record, current, scope):
    """Per path unchanged, applied, ignored or edited against the previous baseline and candidate, and the candidate's disposition."""
    if record['scope'] != scope: return dict(disposition='unknown', reason='scope_changed')
    base = record['baseline']
    target = base if record['candidate'] is None else record['candidate']['states']
    paths = {}
    for path in sorted(base.keys() | target.keys() | current.keys()):
        now, before, after = current.get(path), base.get(path), target.get(path)
        paths[path] = ('unchanged' if now == before else 'edited') if before == after else 'applied' if now == after else 'ignored' if now == before else 'edited'
    changed = {paths[path] for path in paths if base.get(path) != target.get(path)}
    return dict(paths=paths, disposition='no_candidate' if not changed else 'edited' if 'edited' in changed else
                changed.pop() if len(changed) == 1 else 'partially_applied')


def _score(block, arm):
    value = (block.get('scores') or {}).get(arm)
    return value['mean'] if isinstance(value, dict) and type(value.get('mean')) in (int, float) else None


def _historical(receipt):
    """The earlier paired evaluation as a labelled observation, or None when the receipt holds no usable one."""
    block = (receipt or {}).get('before_after')
    if not isinstance(block, dict) or block.get('profile') != 'ahl-before-after-guard-v1' or _score(block, 'baseline') is None: return None
    delta = block.get('delta')
    return dict(label='historical_observation', verdict=block.get('verdict'), baseline_score=_score(block, 'baseline'),
                candidate_score=_score(block, 'candidate'), delta=delta.get('mean') if isinstance(delta, dict) else None,
                attempt=(block.get('bindings') or {}).get('attempt'), frozen_inputs=(block.get('frozen_inputs') or {}).get('baseline'))


def _key(where, states):
    """The identity of one candidate on one baseline and harness scope; `where` holds the scope and the baseline."""
    return digest([where['scope'], where['baseline'], states])


def _known(record):
    """The carried regression of the record's own frozen candidate, else None."""
    if record['candidate'] is None: return None
    key = _key(record, record['candidate']['states'])
    return next((entry for entry in record['regressions'] if entry['key'] == key), None)


def _measured(prior):
    """The regression the previous receipt measured for the very candidate its record froze, on a cause the candidate owns, else None."""
    record, receipt = prior['record'], prior['receipt']
    seen, candidate = _historical(receipt), record['candidate']
    if (candidate is not None and seen is not None and seen['verdict'] == 'candidate_regressed' and seen['attempt'] == candidate['attempt']
            and receipt.get('reason') == 'candidate_regressed' and (receipt.get('investigation') or {}).get('cause') == 'candidate_caused'):
        return dict(key=_key(record, candidate['states']), run=prior['run'], cause='candidate_caused', historical=seen)


def carried(prior):
    """Every regression measured up to and including the previous run, to be copied into this run's record."""
    earlier, own = prior['record']['regressions'], _measured(prior)
    return (earlier + [own] if own else earlier)[-REGRESSIONS_KEPT:]


def regressed(prior):
    """True for a measured regression of the previous run's frozen candidate, False for a measured pair bound to it that did not
    regress, None when the candidate was never measured or the measurement is not about it."""
    record, seen = prior['record'], _historical(prior['receipt'])
    if record['candidate'] is None: return None
    if _measured(prior): return True
    if seen is not None and seen['attempt'] == record['candidate']['attempt'] and seen['verdict'] in ('improved', 'inconclusive'): return False
    return True if _known(record) else None


def repeats_regression(state, proposed, runtime_sha256):
    """The carried regression of an earlier run that measured this same candidate worse on this baseline, scope and runtime, else None."""
    if state is None or runtime_sha256 is None: return None
    key = _key(state, _plain(proposed))
    return next((entry for entry in state['regressions']
                 if entry['key'] == key and (entry['historical'].get('frozen_inputs') or {}).get('runtime_sha256') == runtime_sha256), None)


def block_repeat(result, state, found):
    """End a run whose candidate an earlier run already measured worse; nothing is constructed or evaluated again."""
    state['skipped'] = True
    result.update(status='blocked', reason=REPEAT_REASON, previous_run=found['run'])


def _fresh(result, historical):
    block = result.get('before_after')
    if not isinstance(block, dict) or _score(block, 'baseline') is None: return None
    from agent_harness_lab.evaluation import before_after
    fresh = dict(label='fresh_evaluation', verdict=block.get('verdict'), baseline_score=_score(block, 'baseline'), candidate_score=_score(block, 'candidate'))
    arm, prior = (block.get('frozen_inputs') or {}).get('baseline') or {}, (historical or {}).get('frozen_inputs')
    if not isinstance(prior, dict) or not arm: return dict(fresh, comparison=dict(status='unverified', reason='frozen_inputs_unrecorded'))
    comparison = before_after.compare_identities(prior, arm)
    # A difference is shown only for identical frozen inputs; another bank, grader or runtime makes the scores incomparable.
    shown = {} if comparison['status'] != 'identical' or historical['candidate_score'] is None else dict(
        fresh_baseline_minus_historical_candidate=round(fresh['baseline_score'] - historical['candidate_score'], 6),
        fresh_baseline_minus_historical_baseline=round(fresh['baseline_score'] - historical['baseline_score'], 6))
    return dict(fresh, comparison=comparison, **shown)


def attach(result, state, current):
    """Add the ``incremental`` block; a first run has none, and a failure here never changes the run result."""
    if state is None: return
    try:
        if state['problem']:
            result['incremental'] = dict(profile=PROFILE, status='unavailable', reason=state['problem'], error_type=state.get('error'), **_AUTHORITY)
            return
        prior = state['prior']
        if prior is None: return
        record, receipt, now = prior['record'], prior['receipt'], _plain(current)
        found = compare(record, now, state['scope'])
        paths = found.get('paths', {})
        def listed(kind, names=None):
            names = sorted(names if names is not None else (path for path, value in paths.items() if value == kind))
            return dict(count=len(names), paths=names[:LISTED])
        candidate_regressed, known = regressed(prior), _known(record)
        historical = _historical(receipt) or known and dict(known['historical'], run=known['run'])
        result['incremental'] = dict(
            profile=PROFILE, claim='observed_pattern', status='unknown' if found['disposition'] == 'unknown' else 'compared',
            previous=dict(run=prior['run'], status=(receipt or {}).get('status', 'unknown'), reason=(receipt or {}).get('reason'),
                          candidate_frozen=record['candidate'] is not None, candidate_regressed=candidate_regressed),
            disposition=found['disposition'],
            flags=(['regressed_candidate_applied'] if candidate_regressed and found['disposition'] in ('applied', 'partially_applied') else [])
                  + (['harness_edited_outside_candidate'] if 'edited' in paths.values() and found['disposition'] in ('applied', 'ignored', 'partially_applied') else []),
            **({'reason': found['reason']} if 'reason' in found else dict(harness=dict(
                changed_since_previous_baseline=now != record['baseline'], applied=listed('applied'), ignored=listed('ignored'), edited=listed('edited'),
                added=listed(None, [path for path in now if path not in record['baseline']]),
                removed=listed(None, [path for path in record['baseline'] if path not in now])))),
            historical=historical, fresh=_fresh(result, historical),
            stages={name: dict(action='skipped', reason='previous_regression_of_the_same_candidate') if state['skipped'] and name in ('evaluation_construction', 'trials')
                    else dict(action='rerun', reason=why) for name, why in _RERUN.items()}, **_AUTHORITY)
    except Exception as exc:
        result['incremental'] = dict(profile=PROFILE, status='unavailable', reason=type(exc).__name__, **_AUTHORITY)
