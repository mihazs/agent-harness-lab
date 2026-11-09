"""Synthetic companion integration; repeated plan math is conditional, never certification."""
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from agent_harness_lab.evaluation import repeated_workflow,certification_access
from agent_harness_lab.evaluation.oracle_history import HistoryStore
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.private_journal import encoded,record
from tests.integration.evaluation import test_repeated_workflow as fixtures
from tests.integration.sandbox import test_sandbox_kernel as kernel
from tests.support.unittest_cleanups import run_class_cleanups,run_cleanups


class RepeatedAccessAdmission(fixtures.PublicFixture,unittest.TestCase):
    """Hosts PublicFixture with an explicit evaluation store for the admission cases below; it defines no tests."""

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.cgroup_root=self.root/'no-cgroup'; self.configure()
        self.selected=self.root/'selected-history'

    def arguments(self,*args):
        return super().arguments(*args)+['--evaluation-store',str(self.selected)]

    def request_args(self,selected):
        return SimpleNamespace(agent='responses',command='improve',experiment_request=self.request,
            inference_settings=self.settings,project=self.project,image_root=self.root/'image',
            evaluation_store=selected,cgroup_root=self.cgroup_root,image_sha256='a'*64,disk_bytes=64*1024*1024)


@pytest.fixture
def access():
    value=RepeatedAccessAdmission();value.setUp()
    yield value
    run_cleanups(value)


def test_request_uses_the_explicit_same_store_instead_of_a_new_output_store(access):
    output=access.root/'prepared'; output.mkdir()
    actual=repeated_workflow.Request(access.request_args(access.selected),output,time.monotonic()+30)
    assert actual.store == access.selected
    assert access.calls == []; assert access.key_reads == 0


def test_selected_store_inside_fixed_bank_rejects_before_writing_analyzed_inputs(access):
    access.selected=access.bank/'must-not-be-created'; before=digest(access.bank)
    with patch('agent_harness_lab.evaluation.sandbox.SandboxBackend',side_effect=AssertionError('no invalid-store probe')):
        with pytest.raises(SystemExit): access.invoke(access.root/'image','a'*64,access.root/'output')
    assert not access.selected.exists(), 'preflight wrote controller metadata inside the declared fixed bank'
    assert digest(access.bank) == before
    assert access.calls == []; assert access.key_reads == 0


def test_default_request_keeps_its_existing_local_history(access):
    output=access.root/'prepared';output.mkdir();args=access.request_args(None)
    assert repeated_workflow.Request(args,output,time.monotonic()+30).store == output/'private-history'


def test_changed_request_cannot_repin_selected_store_preflight(access):
    args=access.request_args(access.selected)
    _,args._evaluation_request_sha256=repeated_workflow.selected_store(args)
    access.request_value['seed']+=1;access.request.write_bytes(encoded(access.request_value))
    with pytest.raises(ValueError, match='request changed'):
        repeated_workflow.Request(args,access.root/'output',time.monotonic()+30)
    assert not access.selected.exists()


@pytest.mark.parametrize('choose', [pytest.param(lambda access, owner: owner/'history', id='bank-owner'),
                                    pytest.param(lambda access, owner: access.root/'image/history', id='runtime'),
                                    pytest.param(lambda access, owner: access.project/'history', id='project')])
def test_declared_owner_runtime_and_project_cannot_hold_selected_store(access, choose):
    source=record(access.manifest)[0]
    owner=access.root/'owned-bank'
    source['bank_authority']={'bank_path':str(owner/'revisions/one/bank.json')}
    access.manifest.write_bytes(encoded(source));access.request_value['manifest_sha256']=record(access.manifest)[1]
    access.request.write_bytes(encoded(access.request_value))
    selected=choose(access, owner)
    with pytest.raises(ValueError):
        repeated_workflow.selected_store(access.request_args(selected))
    assert not selected.exists()


def test_explicit_shared_trial_limit_does_not_rewrite_frozen_plan(access):
    output=access.root/'prepared';output.mkdir();args=access.request_args(access.selected)
    args.project_settings=access.root/'declared-config';args.trial_limit=12
    request=repeated_workflow.Request(args,output,time.monotonic()+30)
    assert request.blocked_reason == 'configured_shared_trial_budget_infeasible'
    assert (request.template.repeats,request.template.max_trials) == (2,12)
    assert (access.calls,access.key_reads) == ([],0)


kernel_only=pytest.mark.skipif(not kernel.ENABLED,reason='explicit delegated kernel profile required')


class RepeatedAccessKernel(fixtures.PublicFixture,kernel.KernelFixture):
    """Hosts PublicFixture on the KernelFixture scope with one shared recipe root; it defines no tests."""

    def setUp(self):
        super().setUp();self.configure();self.selected=self.root/'selected-history'
        # Known recipe variants share one root; successful artifacts do not supply independent roots.
        value=record(self.manifest)[0]
        for task in value['tasks']:task['root']='shared-synthetic-recipe'
        self.manifest.write_bytes(encoded(value));self.request_value.update(
            manifest_sha256=record(self.manifest)[1],bank_sha256=digest(self.bank))
        self.request.write_bytes(encoded(self.request_value))

    def arguments(self,*args):
        return super().arguments(*args)+['--evaluation-store',str(self.selected)]


@pytest.fixture(scope='module')
def kernel_scope():
    RepeatedAccessKernel.setUpClass()
    yield
    run_class_cleanups(RepeatedAccessKernel)


@pytest.fixture
def kernel_case(kernel_scope):
    value=RepeatedAccessKernel();value.setUp()
    yield value
    run_cleanups(value)


@kernel_only
def test_canonical_repeated_uses_one_store_without_independence_upgrade(kernel_case):
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    code,result=kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert code == 3, result
    assert result['recommendation'] != 'review_candidate', result
    assert result['analysis']['status'] == 'inconclusive', result
    assert result['analysis']['roots'] == 1
    assert not (kernel_case.root/'output/private-history').exists()
    checked=HistoryStore(kernel_case.selected).checked(result['controller_run_id'])
    assert len(checked[4]['trials']) == 12
    for row in checked[4]['trials']:
        assert row['grader']['outcome'] == (row['version']=='candidate')
    state=certification_access._state(HistoryStore(kernel_case.selected).access_ledger().path)
    assert len(state['constructions']) == 1
    assert state['observed_assets']
    assert not result['automatic_promotion']


@kernel_only
def test_request_changed_during_proposer_cannot_gain_a_sealed_comparison(kernel_case):
    runtime=kernel_case.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
    original=kernel_case.transport
    def changed(*args):
        reply=original(*args)
        kernel_case.request_value['seed']+=1
        kernel_case.request.write_bytes(encoded(kernel_case.request_value))
        return reply
    kernel_case.transport=changed
    with pytest.raises(SystemExit) as failure:
        kernel_case.invoke(runtime,pin,kernel_case.root/'output')
    assert failure.value.code == 2
    result=record(kernel_case.root/'output/workflow.json')[0]
    assert result['status'] == 'invalid_input', result
    assert len(kernel_case.calls) == 2
    assert 'controller_run_id' not in result
    state=certification_access._state(HistoryStore(kernel_case.selected).access_ledger().path)
    assert state['constructions']
    assert all(row['state']!='complete' for row in state['constructions'].values())
