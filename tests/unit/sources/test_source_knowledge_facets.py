"""Pure rules that map declared page metadata to temporal facets; synthetic pages prove the contract only."""
from datetime import date, datetime, timezone
import json
import random

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.knowledge_facets import (DECISION, MAX_PAGES, MAX_REFS, MAX_SCOPE_CHARS, MAX_SCOPES, PROFILE,
    FacetLimit, facets, ref, scope_ref, summary)
from agent_harness_lab.sources.okf import Page
from agent_harness_lab.sources.schema import digest
from agent_harness_lab.sources.temporal import DECISIONS

SOURCE = digest('synthetic-source')
SECRET = 'SYNTHETIC_PAGE_TEXT'


def page(path, **metadata):
    return Page(path, digest('bytes:'+path), metadata, SECRET)


def one(path='a.md', *, recorded_at=None, **metadata):
    result = facets([page(path, **metadata)], SOURCE, recorded_at)
    return result.facets[0] if result.facets else None, result


def placeholder(name):
    return RecordRef(SOURCE, digest(name), digest(dict(unresolved=name)))


@pytest.mark.parametrize('word, decision', [
    pytest.param(None, 'unspecified', id='absent-is-not-accepted'),
    *[pytest.param(word, decision, id=word) for word, decision in DECISION.items()],
])
def test_declared_status_maps_to_one_decision_status(word, decision):
    metadata = {} if word is None else dict(status=word)
    facet, result = one(**metadata)
    assert facet.decision_status == decision and result.findings == ()


def test_every_mapped_decision_is_a_resolver_decision_status():
    assert set(DECISION.values()) <= set(DECISIONS)


@pytest.mark.parametrize('field, reason, value', [
    pytest.param('status', 'status_unrecognized', 'Stable', id='status-wrong-case'),
    pytest.param('status', 'status_unrecognized', 'superseded by ADR-5', id='status-free-text'),
    pytest.param('status', 'status_unrecognized', '', id='status-empty'),
    pytest.param('status', 'status_unrecognized', 3, id='status-number'),
    pytest.param('status', 'status_unrecognized', ['accepted'], id='status-list'),
    pytest.param('valid_from', 'valid_from_unreadable', 20260101, id='valid-from-number'),
    pytest.param('valid_from', 'valid_from_unreadable', '', id='valid-from-empty'),
    pytest.param('valid_from', 'valid_from_unreadable', 'x'*65, id='valid-from-oversized'),
    pytest.param('valid_from', 'valid_from_unreadable', '2026-01-01\n', id='valid-from-control-character'),
    pytest.param('valid_until', 'valid_until_unreadable', {'at': '2026-01-01'}, id='valid-until-mapping'),
    pytest.param('stale_after', 'stale_after_unreadable', 20260601, id='stale-after-number'),
    pytest.param('stale_after', 'stale_after_unreadable', 'x'*65, id='stale-after-oversized'),
    pytest.param('stale_after', 'stale_after_unreadable', ['2026-06-01T00:00:00Z'], id='stale-after-list'),
    pytest.param('open_ended', 'open_ended_unreadable', 'yes', id='open-ended-text'),
    pytest.param('open_ended', 'open_ended_unreadable', None, id='open-ended-null'),
    pytest.param('open_ended', 'open_ended_unreadable', 1, id='open-ended-number'),
    pytest.param('supersedes', 'supersedes_unreadable', {'path': 'b.md'}, id='supersedes-mapping'),
    pytest.param('supersedes', 'supersedes_unreadable', ['b.md', 3], id='supersedes-non-text-item'),
    pytest.param('superseded_by', 'superseded_by_unreadable', ['b%d.md' % n for n in range(MAX_REFS+1)], id='superseded-by-oversized'),
    pytest.param('superseded_by', 'superseded_by_unreadable', 7, id='superseded-by-number'),
    pytest.param('scope', 'scope_unreadable', '', id='scope-empty'),
    pytest.param('scope', 'scope_unreadable', 3, id='scope-number'),
    pytest.param('scope', 'scope_unreadable', {'version': '2'}, id='scope-mapping'),
    pytest.param('scope', 'scope_unreadable', ['v1', 4], id='scope-non-text-item'),
    pytest.param('scope', 'scope_unreadable', ['v1', ''], id='scope-empty-item'),
    pytest.param('scope', 'scope_unreadable', 'x'*(MAX_SCOPE_CHARS+1), id='scope-oversized'),
    pytest.param('scope', 'scope_unreadable', 'v1\nv2', id='scope-control-character'),
    pytest.param('scope', 'scope_unreadable', ['v%d' % n for n in range(MAX_SCOPES+1)], id='scope-too-many'),
    pytest.param('scope', 'scope_unreadable', [], id='scope-empty-list'),
])
def test_a_field_that_cannot_be_read_withholds_the_page_instead_of_guessing(field, reason, value):
    result = facets([page('bad.md', **{field: value}), page('good.md', status='accepted')], SOURCE)
    assert [f.record for f in result.facets] == [ref(SOURCE, page('good.md'))]
    assert result.withheld == ('bad.md',) and ('bad.md', reason) in result.findings


@pytest.mark.parametrize('declared, names', [
    pytest.param('version-2', ['version-2'], id='one-text'),
    pytest.param(['version-1', 'version-2'], ['version-1', 'version-2'], id='list-keeps-the-declared-order'),
    pytest.param(['version-1', 'version-1'], ['version-1'], id='duplicates-collapse'),
    pytest.param('x'*MAX_SCOPE_CHARS, ['x'*MAX_SCOPE_CHARS], id='at-the-length-bound'),
    pytest.param(['v%d' % n for n in range(MAX_SCOPES)], ['v%d' % n for n in range(MAX_SCOPES)], id='at-the-count-bound'),
])
def test_a_declared_scope_maps_to_fingerprints_and_never_keeps_its_text(declared, names):
    facet, result = one(scope=declared)
    assert facet.scopes == tuple(scope_ref(n) for n in names) and result.findings == ()
    assert all(n not in json.dumps(facet.to_dict()) for n in names)


def test_a_page_without_a_scope_declares_none():
    facet, _ = one(project='synthetic-project', workspace='synthetic-workspace')
    assert facet.scopes == ()


def test_the_scope_fingerprint_is_the_one_a_caller_queries_with():
    assert scope_ref('version-2') == digest(dict(scope='version-2')) != scope_ref('Version-2')


def test_one_page_with_several_unreadable_fields_reports_each_reason():
    result = facets([page('bad.md', status='Stable', valid_from=3)], SOURCE)
    assert result.findings == (('bad.md', 'status_unrecognized'), ('bad.md', 'valid_from_unreadable'))


@pytest.mark.parametrize('declared, text', [
    pytest.param('2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', id='text-is-kept-verbatim'),
    pytest.param(datetime(2026, 1, 1, tzinfo=timezone.utc), '2026-01-01T00:00:00+00:00', id='yaml-timestamp-with-offset'),
    pytest.param(datetime(2026, 1, 1), '2026-01-01T00:00:00', id='yaml-timestamp-without-offset-stays-naive'),
    pytest.param(date(2026, 1, 1), '2026-01-01', id='yaml-date-has-no-offset'),
    pytest.param(None, None, id='absent-stays-absent'),
    pytest.param('last spring', 'last spring', id='unparseable-text-is-left-for-the-resolver'),
])
def test_declared_times_keep_their_offset_state_for_the_resolver(declared, text):
    facet, _ = one(valid_from=declared, valid_until=declared, stale_after=declared)
    assert (facet.valid_from, facet.valid_until, facet.stale_after) == (text, text, text)


def test_knowledge_time_is_the_callers_and_never_a_time_the_page_declares_about_itself():
    declared = page('a.md', generated=dict(by='process:x', at='2025-01-01T00:00:00Z'), recorded_at='2025-01-01T00:00:00Z',
                    collected_at='2025-01-01T00:00:00Z')
    assert facets([declared], SOURCE).facets[0].recorded_at is None
    assert facets([declared], SOURCE, '2026-02-01T00:00:00Z').facets[0].recorded_at == '2026-02-01T00:00:00Z'


def test_a_mapping_of_knowledge_times_leaves_an_unlisted_page_without_one():
    result = facets([page('a.md'), page('b.md')], SOURCE, {'a.md': '2026-02-01T00:00:00Z'})
    assert [f.recorded_at for f in result.facets] == ['2026-02-01T00:00:00Z', None]


def test_an_unreadable_caller_knowledge_time_is_rejected_not_hidden():
    with pytest.raises(ValueError):
        facets([page('a.md')], SOURCE, 20260201)


@pytest.mark.parametrize('declared, resolved', [
    pytest.param('b.md', 'dir/b.md', id='same-directory'),
    pytest.param(['b.md', './b.md'], 'dir/b.md', id='duplicates-collapse'),
    pytest.param('/b.md', 'b.md', id='root-relative'),
    pytest.param('/dir/b.md', 'dir/b.md', id='root-relative-into-a-directory'),
    pytest.param('../b.md', 'b.md', id='parent-within-the-bundle'),
    pytest.param(' b.md ', 'dir/b.md', id='surrounding-blanks'),
])
def test_a_page_reference_resolves_to_the_exact_bundle_page(declared, resolved):
    result = facets([page('dir/a.md', superseded_by=declared), page('b.md'), page('dir/b.md')], SOURCE)
    facet = next(f for f in result.facets if f.record == ref(SOURCE, page('dir/a.md')))
    assert facet.superseded_by == (ref(SOURCE, page(resolved)),) and result.findings == ()


@pytest.mark.parametrize('declared, name', [
    pytest.param('gone.md', 'dir/gone.md', id='absent-page'),
    pytest.param('B.md', 'dir/B.md', id='case-only-difference'),
    pytest.param('../../b.md', '../../b.md', id='climbs-above-the-bundle'),
    pytest.param('/../b.md', '/../b.md', id='root-relative-climb'),
    pytest.param('..\\b.md', '..\\b.md', id='backslash'),
    pytest.param('https://example.invalid/b.md', 'dir/https:/example.invalid/b.md', id='remote-reference-stays-opaque'),
    pytest.param('', '', id='empty'),
    pytest.param('x'*2049, 'x'*2049, id='oversized'),
])
def test_a_reference_to_no_bundle_page_stays_visible_as_an_unresolved_placeholder(declared, name):
    result = facets([page('dir/a.md', superseded_by=declared), page('dir/b.md')], SOURCE)
    facet = next(f for f in result.facets if f.record == ref(SOURCE, page('dir/a.md')))
    assert facet.superseded_by == (placeholder(name),)
    assert result.findings == (('dir/a.md', 'superseded_by_unresolved'),)


def test_a_supersedes_reference_to_an_absent_page_is_reported_and_keeps_the_page():
    facet, result = one('new.md', supersedes=['old.md'])
    assert facet.supersedes == (placeholder('old.md'),)
    assert result.findings == (('new.md', 'supersedes_unresolved'),) and result.withheld == ()


def test_a_page_never_names_itself_as_a_resolved_edge_unless_it_declares_it():
    facet, _ = one('a.md', supersedes='a.md')
    assert facet.supersedes == (ref(SOURCE, page('a.md')),)  # the resolver reports the cycle; nothing hides it


def test_the_record_reference_binds_the_path_and_the_exact_bytes():
    record = ref(SOURCE, page('a/b.md'))
    assert record.key == (SOURCE, digest('a/b.md'), digest('bytes:a/b.md'))
    assert ref(SOURCE, Page('a/b.md', digest('other'), {}, '')).revision_sha256 != record.revision_sha256


def test_unknown_metadata_keys_and_execution_fields_are_ignored_and_never_run(monkeypatch):
    monkeypatch.setattr('subprocess.run', lambda *a, **k: pytest.fail('a page field must never execute'))
    facet, result = one(type='Attested Computation', executor='touch /tmp/never', attester='human:x', custom=['kept'])
    assert facet.decision_status == 'unspecified' and result.findings == ()


def test_the_result_is_identical_across_input_order():
    pages = [page(f'{n}.md', status='accepted', supersedes=['3.md', 'gone.md']) for n in range(6)]
    baseline = facets(pages, SOURCE, '2026-01-01T00:00:00Z')
    for seed in range(10):
        shuffled = pages[:]; random.Random(seed).shuffle(shuffled)
        assert facets(shuffled, SOURCE, '2026-01-01T00:00:00Z') == baseline


def test_duplicate_page_paths_are_refused():
    with pytest.raises(ValueError, match='distinct'):
        facets([page('a.md'), page('a.md')], SOURCE)


def test_a_bundle_over_the_page_bound_raises_instead_of_truncating():
    with pytest.raises(FacetLimit):
        facets((page(f'{n}.md') for n in range(MAX_PAGES+1)), SOURCE)


def test_an_empty_bundle_has_no_facets_and_no_findings():
    assert facets([], SOURCE) == facets((), SOURCE) and facets([], SOURCE).facets == ()


def test_summary_counts_only_and_is_never_publishable():
    result = facets([page('a.md', status='accepted', supersedes='gone.md'), page('b.md', status='Stable'),
                     page('c.md', status='deprecated')], SOURCE)
    report = summary(result)
    assert report['pages'] == 3 and report['facets'] == 2 and report['withheld_pages'] == 1
    assert report['findings'] == {'status_unrecognized': 1, 'supersedes_unresolved': 1}
    assert report['decision_statuses'] == {'accepted': 1, 'deprecated': 1}
    text = json.dumps(report)
    assert all(word not in text for word in ('a.md', 'gone.md', SECRET))
    assert report['profile'] == PROFILE and report['truth_established'] is False and report['authority'] == 'none'
    assert not public_export_allowed(dict(report))
