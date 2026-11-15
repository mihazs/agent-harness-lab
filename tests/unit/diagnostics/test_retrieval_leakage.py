"""Synthetic contracts for the retrieval-side leakage check on held-out probes (#14)."""
from dataclasses import replace
import json

import pytest

from agent_harness_lab.diagnostics import retrieval_leakage
from agent_harness_lab.diagnostics.retrieval import Incident, LexicalIndex

BASE = Incident('i-base', 'root-train', 'tool timeout while reading the lockfile twice', ('tool_timeout',),
                ('synthetic:e1',), 'synthetic-review', 'reviewed_annotation', 'random', 'synthetic', True)
_MISSING = object()


def incident(incident_id, root, summary, **changes):
    return replace(BASE, incident_id=incident_id, root_id=root, summary=summary, **changes)


def probe(root, text, **changes):
    value = dict(root_case_id=root, split='heldout', text=text)
    value.update(changes)
    return {key: item for key, item in value.items() if item is not _MISSING}


def index(*incidents):
    return LexicalIndex(list(incidents))


def test_same_root_incident_in_the_index_rejects_even_when_nothing_retrieves_it():
    result = retrieval_leakage.evaluate(index(incident('own', 'root-held', 'permission denied on install')),
                                        [probe('root-held', 'tool timeout while reading')])
    assert result['status'] == 'rejected'
    assert 'same_root_incident_in_index' in result['reasons']
    assert result['leaks'] == dict(same_root=1, near_duplicate_text=0, retrieved=0)
    assert [(f['incident_id'], f['reasons'], f['retrieved']) for f in result['findings']] == [('own', ['same_root'], False)]


def test_same_root_incident_the_probe_retrieves_is_marked_retrieved():
    result = retrieval_leakage.evaluate(index(incident('own', 'root-held', 'tool timeout while reading')),
                                        [probe('root-held', 'tool timeout')])
    assert result['leaks']['retrieved'] == 1
    assert result['findings'][0]['retrieved'] is True


def test_leak_below_the_retrieval_depth_is_reported_but_not_marked_retrieved():
    corpus = index(incident('closer', 'root-a', 'tool timeout tool timeout tool timeout'),
                   incident('own', 'root-held', 'timeout of the lockfile read'))
    result = retrieval_leakage.evaluate(corpus, [probe('root-held', 'tool timeout')], top_k=1)
    assert result['status'] == 'rejected'
    assert [(f['incident_id'], f['retrieved']) for f in result['findings']] == [('own', False)]
    assert result['top_k'] == 1


@pytest.mark.parametrize('text', [
    'tool timeout while reading the lockfile twice',
    '  Tool   TIMEOUT while reading the lockfile twice!  ',
], ids=['verbatim', 'case-space-and-punctuation'])
def test_copied_summary_under_another_root_rejects_as_near_duplicate_text(text):
    result = retrieval_leakage.evaluate(index(BASE), [probe('root-held', text)])
    assert result['status'] == 'rejected'
    assert 'near_duplicate_text_in_index' in result['reasons']
    finding = result['findings'][0]
    assert (finding['reasons'], finding['similarity_permille'], finding['retrieved']) == (['near_duplicate_text'], 1000, True)


@pytest.mark.parametrize(('probe_text', 'rejected'), [
    ('red green blue amber violet cyan magenta', True),
    ('red green blue amber violet cyan magenta ochre', False),
], ids=['at-the-800-permille-threshold', 'just-below-the-threshold'])
def test_near_duplicate_threshold_includes_the_boundary_and_excludes_below_it(probe_text, rejected):
    corpus = index(incident('six', 'root-train', 'red green blue amber violet cyan'))
    result = retrieval_leakage.evaluate(corpus, [probe('root-held', probe_text)])
    assert (result['status'] == 'rejected') is rejected
    assert [f['similarity_permille'] for f in result['findings']] == ([800] if rejected else [])


def test_independent_probe_is_never_reported_clean_or_independent():
    result = retrieval_leakage.evaluate(index(BASE), [probe('root-held', 'permission denied on package install')])
    assert result['status'] == 'insufficient_evidence'
    assert result['findings'] == []
    assert (result['root_independence'], result['promotion_authority'], result['publication_allowed']) == \
        ('unverified', False, False)
    assert {'root_independence_unverified', 'paraphrase_leakage_not_detected',
            'project_and_time_lineage_not_checked'} <= set(result['reasons'])


def test_probe_with_unknown_root_is_counted_and_cannot_clear_the_index():
    result = retrieval_leakage.evaluate(index(BASE), [probe(None, 'permission denied on package install')])
    assert result['status'] == 'insufficient_evidence'
    assert (result['probes'], result['unknown_root_probes'], result['probe_roots']) == (1, 1, 0)
    assert 'unknown_root_probes' in result['reasons']


def test_probe_with_unknown_root_still_rejects_a_copied_summary():
    result = retrieval_leakage.evaluate(index(BASE), [probe(None, BASE.summary)])
    assert result['status'] == 'rejected'
    assert result['findings'][0]['probe_root_sha256'] is None


def test_empty_index_has_nothing_to_leak_and_proves_nothing():
    result = retrieval_leakage.evaluate(index(), [probe('root-held', 'tool timeout')])
    assert result['status'] == 'insufficient_evidence'
    assert 'empty_index' in result['reasons']
    assert result['index_records'] == 0


def test_report_counts_distinct_probe_roots_and_orders_findings_by_probe_then_incident():
    corpus = index(incident('b', 'root-x', 'alpha beta gamma delta'), incident('a', 'root-x', 'epsilon zeta eta theta'))
    result = retrieval_leakage.evaluate(corpus, [probe('root-x', 'other words entirely'), probe('root-x', 'more other words')])
    assert (result['probes'], result['probe_roots'], result['index_roots']) == (2, 1, 1)
    assert [f['incident_id'] for f in result['findings']] == ['a', 'b', 'a', 'b']


def test_report_withholds_raw_roots_probe_text_and_private_summaries():
    private = incident('p', 'root-held', 'secret-canary-1234 tool timeout', source_kind='private')
    result = retrieval_leakage.evaluate(index(private), [probe('root-held', 'probe-canary-9876 tool timeout')])
    text = json.dumps(result)
    assert result['status'] == 'rejected'
    assert result['private'] is True
    assert not any(word in text for word in ('secret-canary-1234', 'probe-canary-9876', 'root-held'))


@pytest.mark.parametrize('rows', [
    [],
    [probe(_MISSING, 'tool timeout')],
    [probe('', 'tool timeout')],
    [probe(7, 'tool timeout')],
    [probe('r' * 257, 'tool timeout')],
    [probe('root-held', 'tool timeout', split='train')],
    [probe('root-held', 'tool timeout', split=_MISSING)],
    [probe('root-held', '')],
    [probe('root-held', '   ')],
    [probe('root-held', None)],
    ['not a row'],
], ids=['no-probes', 'missing-root-key', 'empty-root', 'non-string-root', 'oversized-root', 'train-split',
        'missing-split', 'empty-text', 'blank-text', 'non-string-text', 'non-dict-row'])
def test_invalid_probe_input_is_refused_instead_of_becoming_a_clean_result(rows):
    with pytest.raises(ValueError):
        retrieval_leakage.evaluate(index(BASE), rows)


@pytest.mark.parametrize('top_k', [0, -1, True, 1.5, '3'], ids=['zero', 'negative', 'bool', 'float', 'string'])
def test_invalid_retrieval_depth_is_refused(top_k):
    with pytest.raises(ValueError):
        retrieval_leakage.evaluate(index(BASE), [probe('root-held', 'tool timeout')], top_k=top_k)


def test_non_index_is_refused():
    with pytest.raises(ValueError):
        retrieval_leakage.evaluate([BASE], [probe('root-held', 'tool timeout')])


def test_probe_rows_may_be_a_generator():
    result = retrieval_leakage.evaluate(index(BASE), (row for row in [probe('root-held', 'permission denied')]))
    assert result['probes'] == 1
