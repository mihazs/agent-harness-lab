"""Read-only analysis of a saved protected or derived history writes nothing to its store (synthetic data)."""
from contextlib import ExitStack
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation import test_certification_access as protected
from tests.integration.evaluation import test_derivation as derivation
from tests.integration.evaluation.test_saved_bank_bound_history import state


class SavedProtected(protected.TestCertificationAccess):
    """The protected-access fixture, minus the tests it inherits."""


@pytest.fixture
def saved():
    with ExitStack() as cleanups:
        harness = SavedProtected()
        harness.f = protected.fixtures.OracleHistoryTests(cleanups.callback)
        run, _ = harness.run_protected(harness.reserve())
        yield harness.f, run['run_id']


def analyze_rejected(history, run_id):
    with pytest.raises(SystemExit) as stopped:
        history.analyze_saved(run_id)
    return stopped.value.code


def test_protected_saved_history_is_analyzed_without_writing_to_its_store(saved):
    history, run_id = saved
    before = state(history.store_root)
    code, receipt = history.analyze_saved(run_id)
    assert code == 0
    method = receipt['analysis']['algorithms'][-1]
    assert (method['id'], method['execution'], method['authority']) == ('paired_history_analysis', 'completed', 'conditional_analysis_only')
    result = method['result']
    assert result['protected_access']['qualification'] == 'unverified'
    assert result['protected_access']['scope'] == 'managed_controller_access_only'
    assert result['independence'] == 'unverified' and result['oracle_review'] == 'unverified'
    assert result['automatic_promotion'] is False and result['publication_allowed'] is False
    assert state(history.store_root) == before


def test_protected_saved_history_without_its_dependency_lock_names_the_lock_and_creates_none(saved):
    history, run_id = saved
    lock = history.store_root/'protected-access.lock'; lock.unlink()
    before = state(history.store_root)
    with pytest.raises(ValueError, match='existing protected-access dependency lock'):
        history.store.analyze(run_id, read_only=True)
    assert not lock.exists() and state(history.store_root) == before


def test_protected_saved_history_with_a_missing_ledger_is_rejected_and_none_is_created(saved):
    history, run_id = saved
    ledger = history.store_root/'protected-access.json'; ledger.unlink()
    before = state(history.store_root)
    assert analyze_rejected(history, run_id) == 2
    assert not ledger.exists() and state(history.store_root) == before


@pytest.mark.parametrize('change', [
    pytest.param(lambda value: value['reservations'].clear(), id='reservation-removed'),
    pytest.param(lambda value: next(iter(value['reservations'].values())).update(state='reserved'), id='reservation-reopened'),
])
def test_protected_saved_history_whose_ledger_differs_from_its_receipt_is_rejected_without_writing(saved, change):
    history, run_id = saved
    ledger = history.store_root/'protected-access.json'
    value = json.loads(ledger.read_text()); change(value); ledger.write_text(json.dumps(value))
    before = state(history.store_root)
    assert analyze_rejected(history, run_id) == 2
    assert state(history.store_root) == before


@pytest.fixture
def derived():
    harness = derivation.DerivationHarness(); harness.setUp()
    try:
        history = harness.f
        harness.derive(source_run=harness.parent())
        manifest = harness.install_child_plan()
        with patch.object(SandboxBackend, 'capability', return_value={'status': 'available'}), \
                patch.object(SandboxBackend, 'run', side_effect=history.execute):
            ordinary = history.store.run(manifest, history.args.baseline, history.args.candidate, **harness.resources())
        assert 'derivation_binding_sha256' in history.store.opened(ordinary['run_id'])[2]
        yield history, ordinary['run_id']
    finally:
        harness.cleanups.close()


def test_derived_saved_history_is_analyzed_without_writing_to_its_store(derived):
    history, run_id = derived
    before = state(history.store_root)
    code, receipt = history.analyze_saved(run_id)
    assert code == 0
    result = receipt['analysis']['algorithms'][-1]['result']
    assert result['independence'] == 'unverified' and result['automatic_promotion'] is False
    assert state(history.store_root) == before


def test_derived_saved_history_without_its_dependency_lock_names_the_lock_and_creates_none(derived):
    history, run_id = derived
    lock = history.store_root/'protected-access.lock'; lock.unlink()
    before = state(history.store_root)
    with pytest.raises(ValueError, match='existing protected-access dependency lock'):
        history.store.analyze(run_id, read_only=True)
    assert not lock.exists() and state(history.store_root) == before


def test_derived_saved_history_with_a_missing_ledger_is_rejected_and_none_is_created(derived):
    history, run_id = derived
    ledger = history.store_root/'protected-access.json'; ledger.unlink()
    before = state(history.store_root)
    assert analyze_rejected(history, run_id) == 2
    assert not ledger.exists() and state(history.store_root) == before


@pytest.mark.parametrize('change', [
    pytest.param(lambda value: value['derivations'].clear(), id='derivation-removed'),
    pytest.param(lambda value: next(iter(value['derivations'].values())).update(state='pending'), id='derivation-reopened'),
])
def test_derived_saved_history_whose_derivation_link_changed_is_rejected_without_writing(derived, change):
    history, run_id = derived
    ledger = history.store_root/'protected-access.json'
    value = json.loads(ledger.read_text()); change(value); ledger.write_text(json.dumps(value))
    before = state(history.store_root)
    assert analyze_rejected(history, run_id) == 2
    assert state(history.store_root) == before
