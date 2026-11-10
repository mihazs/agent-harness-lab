"""Synthetic cross-field corpus aliases; no real incident text."""
from hashlib import sha256

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample


def fixture(name,partition,summary,fragment):
    value = sample(name,partition); value['summary'] = summary
    value['evidence'] = [dict(id='terminal',content=fragment,sha256=sha256(fragment.encode()).hexdigest())]
    return value


def reviewed(store,value):
    item = store.enqueue(value)
    for reviewer in ('synthetic-reviewer-one','synthetic-reviewer-two'):
        item = store.update(item['case_id'],item['event_sha256'],'claimed',reviewer=reviewer)
        item = store.update(item['case_id'],item['event_sha256'],'submitted',reviewer=reviewer,label=label())
    return store.update(item['case_id'],item['event_sha256'],'adjudicated',reviewer='synthetic-adjudicator',
                        label=label(),reason='Synthetic adjudication')


@pytest.mark.parametrize('reverse',[False,True])
def test_training_summary_aliased_by_heldout_fragment_is_blocked(tmp_path,reverse):
    store = CorpusStore(tmp_path/'corpus')
    fit = fixture('fit','fitting','Unique synthetic target sentence.','Fitting evidence of another observation.')
    held = fixture('held','validation','Different held-out summary.','  unique SYNTHETIC target sentence.  ')
    for value in ((held,fit) if reverse else (fit,held)):
        reviewed(store,value) if value is fit else store.enqueue(value)
    with pytest.raises(ValueError,match='text aliases'):
        store.export_development(store.snapshot()['revision_sha256'])


def test_shared_boilerplate_fragments_alone_do_not_trigger_alias_block(tmp_path):
    store = CorpusStore(tmp_path/'corpus')
    for name,partition in [('fit','fitting'),('held','validation')]:
        value = fixture(name,partition,f'Unique synthetic summary {name}.','Shared generic evidence boilerplate.')
        reviewed(store,value) if partition == 'fitting' else store.enqueue(value)
    exported = store.export_development(store.snapshot()['revision_sha256'])
    assert len(exported['records']) == 1 and exported['classifier_selection_allowed'] is False


def test_exported_fitting_fragment_aliased_by_heldout_summary_is_blocked(tmp_path):
    store = CorpusStore(tmp_path/'corpus')
    fit = fixture('fit','fitting','Fitting summary about another observation.','Shared exact held-out text.')
    held = fixture('held','certification','SHARED EXACT HELD-OUT TEXT.','Unrelated protected fragment.')
    reviewed(store,fit); store.enqueue(held)
    with pytest.raises(ValueError,match='text aliases'):
        store.export_development(store.snapshot()['revision_sha256'])


def test_cross_field_alias_within_one_partition_remains_allowed(tmp_path):
    store = CorpusStore(tmp_path/'corpus')
    first = fixture('one','fitting','Identical synthetic sentence.','Other observation.')
    second = fixture('two','fitting','Different synthetic summary.','IDENTICAL SYNTHETIC SENTENCE.')
    reviewed(store,first); store.enqueue(second)
    assert len(store.export_development(store.snapshot()['revision_sha256'])['records']) == 1


@pytest.mark.parametrize('reverse',[False,True])
def test_cross_field_guard_is_order_independent(reverse):
    from agent_harness_lab.diagnostics.corpus import _separate
    fit = fixture('fit','fitting','Unique synthetic target sentence.','Another fitting observation.')
    held = fixture('held','validation','Different held-out summary.','Unique synthetic target sentence.')
    groups,texts = {},{}
    first,second = (held,fit) if reverse else (fit,held)
    _separate(groups,texts,first,first['partition'])
    with pytest.raises(ValueError,match='text aliases'):
        _separate(groups,texts,second,second['partition'])
