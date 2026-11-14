"""Read-only index of existing evaluation histories: live authority, unknowns and role denial (synthetic data)."""
from contextlib import ExitStack
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.sandbox import ROLES
from agent_harness_lab.memory.history_index import RoleDenied, list_histories
from tests.integration.evaluation.test_oracle_bank_authority import OracleBankAuthority
from tests.integration.evaluation.test_oracle_history import OracleHistoryTests


@pytest.fixture
def bound():
    with ExitStack() as cleanups:
        yield OracleBankAuthority(cleanups.callback)


@pytest.fixture
def legacy():
    with ExitStack() as cleanups:
        yield OracleHistoryTests(cleanups.callback)


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob('*')) if p.is_file()}


def rows(*stores):
    return list_histories([s.root for s in stores])['histories']


def test_bound_run_is_listed_by_reference_with_digests_and_leaves_the_store_unchanged(bound):
    result, _ = bound.run_history()
    before = snapshot(bound.store.root)
    [row] = rows(bound.store)
    assert snapshot(bound.store.root) == before
    assert row['original_run_id'] == result['run_id'] and len(row['authority_store_ref']) == 64
    assert str(bound.store.root) not in json.dumps(row)
    assert row['current_validity'] == 'current' and row['comparison_eligibility'] == 'eligible'
    assert row['bank_authority'] == 'current_eligible' and row['reasons'] == []
    for key in ('spec_sha256', 'bank_sha256', 'bank_revision_sha256', 'baseline_sha256', 'candidate_sha256',
                'grader_sha256', 'runtime_sha256'):
        assert len(row[key]) == 64
    assert row['oracle_sha256s'] and row['publication_allowed'] is False


def test_oracle_quarantine_changes_the_next_read_without_touching_stored_receipts(legacy):
    result, _ = legacy.run_history()
    legacy.store.show(result['run_id'])
    assert rows(legacy.store)[0]['current_validity'] == 'current'
    path, _ = legacy.quarantine_record()
    legacy.store.quarantine(path)
    run_files = snapshot(legacy.store.case(result['run_id']))
    [row] = rows(legacy.store)
    assert snapshot(legacy.store.case(result['run_id'])) == run_files
    assert row['current_validity'] == 'invalidated_by_oracle_quarantine' and row['oracle_state'] == 'quarantined'
    assert row['comparison_eligibility'] != 'eligible' and 'invalidated_by_oracle_quarantine' in row['reasons']
    assert row['spec_sha256'] and row['state'] == 'sealed'  # still viewable as historical evidence


def test_bank_revocation_makes_a_bound_result_historical_only(bound):
    bound.run_history()
    bound.revoke()
    [row] = rows(bound.store)
    assert row['current_validity'] == 'authority_not_current' and row['comparison_eligibility'] == 'not_comparable'
    assert row['bank_sha256'] and row['bank_authority'] is None


def test_unbound_legacy_run_keeps_unknown_bank_binding_unknown(legacy):
    legacy.run_history()
    [row] = rows(legacy.store)
    assert row['comparison_eligibility'] == 'legacy_unverified' and 'bank_binding_missing' in row['reasons']
    assert row['bank_sha256'] is None and row['bank_revision_sha256'] is None and row['bank_authority'] is None


def test_tampered_run_is_listed_unreadable_not_skipped(legacy):
    result, _ = legacy.run_history()
    (legacy.store.case(result['run_id']) / 'evidence.json').write_text('{}')
    [row] = rows(legacy.store)
    assert row['current_validity'] == 'unreadable' and row['comparison_eligibility'] == 'unknown'
    assert row['spec_sha256'] is None


def test_selection_is_explicit_and_unique(legacy, tmp_path):
    legacy.run_history()
    with pytest.raises(ValueError):
        list_histories([legacy.store.root, legacy.store.root])
    with pytest.raises(ValueError):
        list_histories(['relative/history'])


@pytest.mark.parametrize('role', sorted(ROLES))
def test_every_isolated_role_is_denied(role, legacy, monkeypatch, capsys):
    legacy.run_history()
    monkeypatch.setenv('AHL_SANDBOX_ROLE', role)
    with pytest.raises(RoleDenied):
        list_histories([legacy.store.root])
    assert main(['memory', 'history', '--history-store', str(legacy.store.root)]) == 3
    assert capsys.readouterr().out == ''


def test_cli_lists_json_and_validates_flags(legacy, capsys):
    legacy.run_history()
    before = snapshot(legacy.store.root)
    assert main(['memory', 'history', '--history-store', str(legacy.store.root)]) == 0
    assert json.loads(capsys.readouterr().out)['histories'][0]['comparison_eligibility'] == 'legacy_unverified'
    assert snapshot(legacy.store.root) == before
    for argv in (['memory', 'history'], ['memory', 'status', '--history-store', str(legacy.store.root)],
                 ['memory', 'history', '--history-store', str(legacy.store.root), '--apply']):
        with pytest.raises(SystemExit) as stop:
            main(argv)
        assert stop.value.code == 2
