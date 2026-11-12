"""Read-only overview of explicitly selected evaluation HistoryStores, by reference.

Nothing is copied into a second authority: every call re-runs the HistoryStore's own validators under its own
lock, so an oracle quarantine or a bank revocation changes the next listing and an old result stays visible only
as labeled historical evidence. Missing provenance stays unknown (null); nothing is written to any store."""
import hashlib
import os
from pathlib import Path

from agent_harness_lab.evaluation.oracle_history import MAX_CASES, HistoryStore

# Sandbox roles (worker, proposer, evaluator author, grader) run with this set; it is the existing role boundary.
ROLE_VARIABLE = 'AHL_SANDBOX_ROLE'


class RoleDenied(PermissionError):
    pass


def store_ref(root):
    """Stable, path-free reference to a history store: the digest of its canonical location."""
    return hashlib.sha256(os.fsencode(root)).hexdigest()


def _row(store, ref, run_id):
    row = dict(authority_store_ref=ref, original_run_id=run_id, state=None, current_validity='unreadable',
               comparison_eligibility='unknown', reasons=[], oracle_state=None, bank_authority=None,
               spec_sha256=None, bank_sha256=None, bank_revision_sha256=None, oracle_sha256s=None,
               baseline_sha256=None, candidate_sha256=None, grader_sha256=None, runtime_sha256=None,
               event_sha256=None, publication_allowed=False)
    try:
        plan = store._historical_plan(run_id)  # public historical_plan takes the lock this listing already holds
        bundles = sorted({v['digest'] for v in plan['manifest']['grader_bundles'].values()})
        states = {store.oracle_events(b)[-1][0]['state'] for b in bundles}
    except (ValueError, OSError) as error:
        row['reasons'] = ['historical_plan_unavailable: ' + str(error)[:200]]
        return row
    spec, manifest = plan['plan'], plan['manifest']
    bound = plan['bank_tasks'] is not None  # a manifest's bank digest is only a claim until the run is bank-bound
    row.update(state=plan['state'], event_sha256=plan['event_sha256'], spec_sha256=plan['plan_sha256'],
               bank_sha256=manifest.get('bank_digest') if bound else None,
               bank_revision_sha256=manifest.get('bank_revision_sha256') if bound else None,
               oracle_sha256s=bundles, baseline_sha256=spec.get('baseline_hash'), candidate_sha256=spec.get('candidate_hash'),
               grader_sha256=spec.get('grader_hash'), runtime_sha256=spec.get('runtime_hash'),
               oracle_state='quarantined' if 'quarantined' in states else 'active')
    try:
        live = store.summary(run_id, store.checked(run_id))
        row['bank_authority'] = live.get('bank_authority')
    except (ValueError, OSError):
        live = None
    if plan['state'] != 'sealed':
        row['current_validity'] = 'incomplete'
    elif row['oracle_state'] == 'quarantined':
        row['current_validity'] = 'invalidated_by_oracle_quarantine'
    elif live is None:
        row['current_validity'] = 'authority_not_current'
    else:
        row['current_validity'] = 'current'
    legacy = [name for name, value in (('bank_binding_missing', plan['bank_tasks']), ('snapshot_missing', row['baseline_sha256'] and row['candidate_sha256']),
                                       ('grader_provenance_missing', row['grader_sha256']), ('runtime_missing', row['runtime_sha256'])) if not value]
    row['reasons'] = legacy + ([] if row['current_validity'] == 'current' else [row['current_validity']])
    row['comparison_eligibility'] = 'legacy_unverified' if legacy else 'eligible' if not row['reasons'] else 'not_comparable'
    return row


def list_histories(history_stores):
    """Rows for every run of every explicitly named HistoryStore; refuses isolated sandbox roles."""
    if os.environ.get(ROLE_VARIABLE):
        raise RoleDenied('evaluation history is not available to isolated roles')
    roots = []
    for path in history_stores:
        path = Path(path)
        if not path.is_absolute() or '..' in path.parts:
            raise ValueError('history store path must be absolute and canonical')
        if path in roots:
            raise ValueError('history store selected twice')
        roots.append(path)
    rows = []
    for root in roots:
        store = HistoryStore(root)
        ref = store_ref(store.root)
        with store.locked(read_only=True):
            for case_id in store.case_ids(MAX_CASES):
                try:
                    started = store.history(store.case(case_id))[0][0]['kind'] == 'run_started'
                except (ValueError, OSError):
                    started = True  # an unreadable case is listed as unreadable, never skipped
                if started:  # oracle registrations are cases too
                    rows.append(_row(store, ref, case_id))
    return dict(profile='ahl-history-index-v1', histories=rows, authority='live_revalidated',
                writes='none', publication_allowed=False)
