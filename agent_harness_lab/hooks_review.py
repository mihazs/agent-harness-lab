"""hook_review_v1: the review packet of one hook deployment unit. Pure data from the contract, its journal snapshots and a
hook_validation_v1 document; nothing is executed, applied or authorized.

The packet separates what is known (the semantic hook-entry delta, the script delta, the static capability receipt, the
canary disposition and the canary run's wall time) from what is not: task effect and the instruction-only comparison need
graded matched tasks, the canary run's wall time is not a per-hook overhead, and a post-apply registration check does
not exist yet, so applied files are never operational validation."""
import hashlib
import json
from collections import Counter

from .hooks_contract import _raw, read_hooks, unit_patch, CONFIGS, PROFILE
from . import surfaces

REVIEW_PROFILE = 'hook_review_v1'


def _entries(raw, form):
    """[(event, matcher, handler-json)] of configuration bytes, in file order; [] when absent or not a hook shape."""
    try: hooks = read_hooks(raw, form) if raw is not None else None
    except ValueError: return []
    rows = []
    for event, groups in (hooks.items() if isinstance(hooks, dict) else ()):
        for group in groups if isinstance(groups, list) else ():
            handlers = group.get('hooks') if isinstance(group, dict) else None
            for handler in handlers if isinstance(handlers, list) else ():
                rows.append((event, group.get('matcher'), json.dumps(handler, sort_keys=True, default=str)))
    return rows


def _split(rows, common):
    """(kept, rest) of rows against a Counter of shared rows, consuming one match per kept row so duplicates count."""
    left, kept, rest = Counter(common), [], []
    for row in rows:
        key = repr(row)
        if left[key] > 0: left[key] -= 1; kept.append(row)
        else: rest.append(row)
    return kept, rest


def _script_rows(contract, before, after):
    rows = []
    for path, _mode, origin, sha in contract.scripts:
        old = (before.get(path) or {}).get('sha256')
        change = 'unchanged' if origin == 'checkout' or old == sha else 'added' if old is None else 'modified'
        rows.append(dict(path=path, origin=origin, sha256=sha, change=change))
    rows.extend(dict(path=name, origin='removed', sha256=None, change='removed')
                for name in sorted(before.keys() - after.keys()) if name != contract.config)
    return rows


def review_packet(contract, before, after, document=None, *, expected_effect=None):
    """The reviewer-facing packet of one hook unit. document is a hooks_validation.validate() result, or None when the
    unit was never validated; expected_effect is the proposer's own claim, never inferred."""
    if contract.profile != PROFILE: raise ValueError('review_packet() reviews hook units only')
    form = CONFIGS.get(contract.config, (None, None))[1]
    old, new = (_entries(_raw(side.get(contract.config)), form) for side in (before, after))
    common = Counter(map(repr, old)) & Counter(map(repr, new))
    (kept_old, removed), (kept_new, added) = _split(old, common), _split(new, common)
    candidate = (document or {}).get('candidate') or {}
    run = (document or {}).get('run') or {}
    status = candidate.get('status', 'not_validated')
    describe = lambda rows: [dict(event=e, matcher=m, handler=json.loads(h)) for e, m, h in rows]
    return dict(
        profile=REVIEW_PROFILE, unit_sha256=contract.sha256, config=contract.config,
        config_delta=dict(added=describe(added), removed=describe(removed), order_preserved=kept_old == kept_new,
                          other_settings='byte_identical_by_contract' if contract.status == 'passed' else 'unverified'),
        scripts=_script_rows(contract, before, after), patch=unit_patch(before, after),
        expected_effect=expected_effect if isinstance(expected_effect, str) and expected_effect.strip() else 'not_stated',
        capability_receipt=dict(runtime=contract.runtime, pinned_version=surfaces.PINS.get(contract.runtime),
                                contract_status=contract.status, trust_review=contract.trust_review,
                                matcher_check=contract.matcher_check,
                                findings=[list(row[:2]) for row in contract.findings]),
        evaluation=dict(disposition=status, effective=bool(candidate.get('effective')),
                        improvement_eligible=bool(candidate.get('improvement_eligible')),
                        disqualifiers=list(candidate.get('disqualifiers', [])),
                        runtime_kind=(document or {}).get('runtime_kind', 'none'),
                        task_effect='not_evaluated', instruction_only_alternative='not_compared',
                        inherited_hooks=(document or {}).get('inherited_hooks', 'unknown')),
        overhead=dict(canary_run_seconds=run.get('elapsed_seconds'), scope='whole_canary_run_not_per_hook'),
        apply=dict(verification='required_not_performed', outcome_if_applied='applied_unverified'),
        evidence_sha256=None if document is None else hashlib.sha256(json.dumps(
            document, sort_keys=True, default=str).encode()).hexdigest(),
        adoption_authority=False, publication_allowed=False)
