"""The incremental block follows call order, not the entry route, so entry parity ignores it and nothing else (#806)."""
import pytest

from agent_harness_lab import entry_parity

BLOCK = dict(profile='ahl-incremental-run-v1', disposition='ignored', harness=dict(applied=dict(count=0, paths=[])))


def differences(first, second, artifact='workflow.json'):
    return entry_parity.document_differences(artifact, first, second, '/cli', '/skill')


@pytest.mark.parametrize('first, second', [
    pytest.param(dict(status='no_change'), dict(status='no_change', incremental=BLOCK), id='block_only_on_the_later_route'),
    pytest.param(dict(status='no_change', incremental=BLOCK), dict(status='no_change'), id='block_only_on_the_earlier_route'),
    pytest.param(dict(status='no_change', incremental=BLOCK), dict(status='no_change', incremental=dict(BLOCK, disposition='applied')), id='block_values_differ'),
])
def test_the_incremental_block_alone_never_breaks_receipt_parity(first, second):
    assert differences(first, second) == []


def test_the_incremental_block_in_a_recovery_record_is_ignored_the_same_way():
    assert differences(dict(receipt=dict(status='no_change')), dict(receipt=dict(status='no_change', incremental=BLOCK)), 'recovery.json') == []


@pytest.mark.parametrize('first, second, expected', [
    pytest.param(dict(status='no_change'), dict(status='blocked', incremental=BLOCK), ['status'], id='another_field_still_differs'),
    pytest.param(dict(status='x'), dict(status='x', incremental_other=1), ['incremental_other'], id='a_lookalike_key_is_not_ignored'),
    pytest.param(dict(status='x'), dict(status='x', reason='y'), ['reason'], id='a_missing_key_is_still_reported'),
])
def test_only_the_incremental_subtree_is_ignored(first, second, expected):
    assert differences(first, second) == expected


def test_a_block_is_not_ignored_in_an_artifact_that_is_not_a_receipt():
    assert differences(dict(a=1), dict(a=1, incremental=BLOCK), 'change-plan.json') != []
