"""Pure rules of the portable knowledge-bundle reader: manifest validation and unknown-preserving claim tiers."""
import json

import pytest

from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources.knowledge_bundle import (LIFECYCLES, MAX_BYTES, MAX_PAGE_BYTES, MAX_PAGES, MAX_ROOTS,
    MAX_TYPES, PROFILE, TRUST_TIERS, Bundle, Manifest, lifecycle, summary, trust_tier)
from agent_harness_lab.sources.okf import Page


def verified(*actors):
    return {'verified': [{'by': actor, 'at': '2026-06-25T09:00:00Z'} for actor in actors]}


@pytest.mark.parametrize('metadata, tier', [
    pytest.param({}, 'unverified', id='no-verified-key'),
    pytest.param({'verified': []}, 'unverified', id='empty-event-list'),
    pytest.param(verified('process:nightly', 'reference_agent/gemini-2.5-pro'), 'machine_confirmed_claim', id='machine-actors-only'),
    pytest.param(verified('process:nightly', 'human:reviewer'), 'human_reviewed_claim', id='one-human-actor'),
    pytest.param({'verified': {'by': 'human:reviewer', 'at': '2026-06-25T09:00:00Z'}}, 'human_reviewed_claim', id='bare-mapping-is-one-event'),
    pytest.param({'verified': 'human:reviewer'}, 'unknown', id='scalar-is-malformed'),
    pytest.param({'verified': None}, 'unknown', id='null-is-malformed'),
    pytest.param({'verified': ['human:reviewer']}, 'unknown', id='event-without-mapping'),
    pytest.param({'verified': [{'at': '2026-06-25T09:00:00Z'}]}, 'unknown', id='event-without-actor'),
    pytest.param({'verified': [{'by': ''}]}, 'unknown', id='empty-actor'),
    pytest.param({'verified': [{'by': 7}]}, 'unknown', id='non-text-actor'),
    pytest.param({'verified': [{'by': 'human:reviewer'}, 'junk']}, 'unknown', id='one-malformed-event-spoils-the-claim'),
])
def test_verified_claim_maps_to_a_tier_and_malformed_claims_stay_unknown(metadata, tier):
    assert trust_tier(metadata) == tier


@pytest.mark.parametrize('metadata, state', [
    pytest.param({}, 'unspecified', id='absent-is-not-stable'),
    pytest.param({'status': 'draft'}, 'draft', id='draft'),
    pytest.param({'status': 'stable'}, 'stable', id='stable'),
    pytest.param({'status': 'deprecated'}, 'deprecated', id='deprecated'),
    pytest.param({'status': 'Stable'}, 'unknown', id='unrecognized-case'),
    pytest.param({'status': None}, 'unknown', id='null'),
    pytest.param({'status': ['stable']}, 'unknown', id='list'),
    pytest.param({'status': {'x': 1}}, 'unknown', id='mapping'),
])
def test_status_claim_maps_to_a_lifecycle_and_unrecognized_values_stay_unknown(metadata, state):
    assert lifecycle(metadata) == state


def test_every_classified_value_is_a_declared_vocabulary_member():
    assert {trust_tier(m) for m in ({}, verified('human:a'), {'verified': 3})} <= set(TRUST_TIERS)
    assert {lifecycle(m) for m in ({}, {'status': 'draft'}, {'status': 3})} <= set(LIFECYCLES)


def test_manifest_accepts_files_directories_and_the_whole_bundle():
    assert Manifest(roots=('README.md', 'docs', 'docs-extra/adr')).roots == ('README.md', 'docs', 'docs-extra/adr')
    assert Manifest(roots=('.',)).roots == ('.',)


@pytest.mark.parametrize('roots', [
    pytest.param((), id='empty'),
    pytest.param(['docs'], id='list-not-tuple'),
    pytest.param('docs', id='bare-text'),
    pytest.param(('docs', 'docs'), id='duplicate'),
    pytest.param(('docs', 'docs/adr'), id='nested-overlap'),
    pytest.param(('.', 'docs'), id='whole-bundle-overlaps-everything'),
    pytest.param(('../outside',), id='parent-traversal'),
    pytest.param(('/etc',), id='absolute'),
    pytest.param(('docs/',), id='trailing-slash'),
    pytest.param(('./docs',), id='dot-prefix'),
    pytest.param(('docs\\adr',), id='backslash'),
    pytest.param(('',), id='empty-name'),
    pytest.param((3,), id='non-text'),
    pytest.param(tuple(f'd{i}' for i in range(MAX_ROOTS + 1)), id='too-many'),
])
def test_manifest_rejects_unsafe_or_ambiguous_roots(roots):
    with pytest.raises(ValueError):
        Manifest(roots=roots)


@pytest.mark.parametrize('doc_types', [
    pytest.param((), id='empty'),
    pytest.param(['Note'], id='list-not-tuple'),
    pytest.param('Note', id='bare-text'),
    pytest.param(('',), id='empty-name'),
    pytest.param(('  ',), id='blank-name'),
    pytest.param((None,), id='non-text'),
    pytest.param(tuple(f't{i}' for i in range(MAX_TYPES + 1)), id='too-many'),
])
def test_manifest_rejects_a_malformed_type_allowlist(doc_types):
    with pytest.raises(ValueError):
        Manifest(roots=('.',), doc_types=doc_types)


@pytest.mark.parametrize('field, value', [
    pytest.param('max_pages', 0, id='zero-pages'),
    pytest.param('max_pages', MAX_PAGES + 1, id='pages-above-ceiling'),
    pytest.param('max_pages', True, id='bool-pages'),
    pytest.param('max_pages', '10', id='text-pages'),
    pytest.param('max_page_bytes', -1, id='negative-page-bytes'),
    pytest.param('max_page_bytes', MAX_PAGE_BYTES + 1, id='page-bytes-above-ceiling'),
    pytest.param('max_bytes', 0, id='zero-bytes'),
    pytest.param('max_bytes', MAX_BYTES + 1, id='bytes-above-ceiling'),
    pytest.param('max_bytes', 1.5, id='float-bytes'),
    pytest.param('format', 'okf-0.3', id='unknown-format'),
    pytest.param('format', None, id='null-format'),
])
def test_manifest_rejects_an_out_of_range_bound_or_unknown_format(field, value):
    with pytest.raises(ValueError):
        Manifest(roots=('.',), **{field: value})


def test_manifest_may_lower_bounds_to_the_ceilings():
    manifest = Manifest(roots=('.',), max_pages=MAX_PAGES, max_page_bytes=1, max_bytes=MAX_BYTES, format='okf-0.2')
    assert (manifest.max_pages, manifest.max_page_bytes, manifest.max_bytes) == (MAX_PAGES, 1, MAX_BYTES)


def build(pages, findings=()):
    return Bundle(tuple(pages), tuple(findings), 'not_applicable', 'f' * 64)


def test_summary_counts_claims_without_content_or_paths():
    pages = [Page('secret/SYNTHETIC_PATH.md', 'a' * 64, dict(type='Note', status='deprecated', **verified('human:SYNTHETIC_ACTOR')), 'SYNTHETIC_BODY'),
             Page('b.md', 'b' * 64, dict(type='Note'), '')]
    report = summary(build(pages, [dict(path='secret/SYNTHETIC_PATH.md', reason='type_missing')]))
    text = json.dumps(report)
    assert all(word not in text for word in ('SYNTHETIC_PATH', 'SYNTHETIC_BODY', 'SYNTHETIC_ACTOR'))
    assert report['pages'] == 2 and report['finding_reasons'] == {'type_missing': 1}
    assert report['trust_claims'] == {'unverified': 1, 'machine_confirmed_claim': 0, 'human_reviewed_claim': 1, 'unknown': 0}
    assert report['lifecycle_claims']['deprecated'] == 1 and report['lifecycle_claims']['unspecified'] == 1


def test_summary_never_claims_execution_truth_or_independent_validation():
    report = summary(build([]))
    assert report['profile'] == PROFILE and report['authority'] == 'none'
    assert not any(report[name] for name in ('executed', 'remote_fetched', 'archive_opened', 'archive_extracted', 'content_included',
        'independently_verified', 'truth_established', 'publication_allowed'))


def test_summary_is_never_publishable_even_with_approved_provenance():
    report = summary(build([]))
    assert not public_export_allowed([report])
    approved = dict(source_kind='synthetic', approved_publication=True, source_ref='fixture', derived_from_private=False)
    assert not public_export_allowed([dict(report, publication_allowed=True, provenance=approved)])
