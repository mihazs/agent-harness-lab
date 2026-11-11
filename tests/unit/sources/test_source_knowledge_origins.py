"""Synthetic page metadata: pages citing one source are one correlated origin, and execution fields stay inert."""
import json
import sys

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.knowledge_origins import MAX_PAGES, OriginLimit, clusters, summary


def cite(*resources, **extra):
    return dict(type='Session Summary', sources=[dict(resource=r) for r in resources], **extra)


def test_pages_citing_one_resource_are_one_cluster():
    found = clusters([('a.md', cite('ai-memory://session/s1')), ('b.md', cite('ai-memory://session/s1')),
                      ('c.md', cite('ai-memory://session/s2'))])
    assert [(c['paths'], c['basis']) for c in found] == [(['a.md', 'b.md'], 'shared_cited_source'), (['c.md'], 'single_page')]
    assert summary(found)['clusters'] == 2 and summary(found)['pages_collapsed'] == 1


def test_a_chain_of_shared_sources_is_transitive_and_order_independent():
    pages = [('a.md', cite('x')), ('b.md', cite('x', 'y')), ('c.md', cite('y'))]
    assert clusters(pages) == clusters(reversed(pages))
    assert [c['paths'] for c in clusters(pages)] == [['a.md', 'b.md', 'c.md']]


def test_uncited_and_malformed_sources_stay_unknown_not_independent():
    found = clusters([('a.md', dict(type='Note')), ('b.md', dict(type='Note')), ('c.md', dict(type='Note', sources='x')),
                      ('d.md', dict(type='Note', sources=[{'resource': 3}, 'ok']))])
    by_path = {c['paths'][0]: c for c in found}
    assert by_path['a.md']['basis'] == by_path['b.md']['basis'] == 'no_cited_source' and len(found) == 4
    assert by_path['c.md']['flags'] == {'malformed_sources': 1}
    assert by_path['d.md']['resources'] == ['ok'] and by_path['d.md']['flags'] == {'malformed_sources': 1}
    report = summary(found)
    assert report['pages_with_unknown_origin'] == 3 and report['independent_roots'] is None


def test_attested_computation_is_inert(tmp_path):
    marker = tmp_path / 'ran'
    command = f'{sys.executable} -c "open({str(marker)!r}, \'w\')"'
    page = dict(type='Attested Computation', executor=command, attester=command, sources=[dict(resource='r')])
    found = clusters([('p.md', page), ('q.md', cite('r'))])
    assert not marker.exists()
    report = summary(found)
    assert report['inert_execution_claims'] == 1 and report['executed'] is False
    assert command not in json.dumps(report) and [c['paths'] for c in found] == [['p.md', 'q.md']]


def test_report_is_counts_only_and_never_publishable():
    report = summary(clusters([('secret/path.md', cite('SYNTHETIC_RESOURCE'))]))
    assert 'SYNTHETIC_RESOURCE' not in json.dumps(report) and 'secret' not in json.dumps(report)
    assert not public_export_allowed([report])
    approved = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
    assert not public_export_allowed([dict(report, publication_allowed=True, provenance=approved)])


def test_bounds_and_bad_input_fail_closed():
    with pytest.raises(OriginLimit):
        clusters((f'{i}.md', {}) for i in range(MAX_PAGES + 1))
    with pytest.raises(ValueError):
        clusters([('a.md', {}), ('a.md', {})])
    with pytest.raises(ValueError):
        clusters([('a.md', 'not a mapping')])
    assert clusters([('a.md', dict(sources=[dict(resource='x')] * 1001))])[0]['flags'] == {'malformed_sources': 1}
