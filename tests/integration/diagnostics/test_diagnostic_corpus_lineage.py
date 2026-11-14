"""Synthetic reviewed lineage groupings across corpus partitions; no real incident text or native proof."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore, _digest, development_export
from agent_harness_lab.sources.execution import RecordRef
from agent_harness_lab.sources.lineage import LineageClaim, LineageReview, LineageStore
from agent_harness_lab.sources.schema import digest
from tests.integration.diagnostics.test_diagnostic_corpus import label, sample
from tests.integration.sources import test_source_store as fixtures

SUMMARIES = {
    'a': 'The synthetic build job stopped after the cache step reported a missing artifact in the staging area.',
    'b': 'A wholly different synthetic outage hit the network layer during deployment of the gateway service.',
    'c': 'The synthetic linter rejected a configuration key that the schema review had renamed last week.'}


class Lineage:
    """Three synthetic source records with a real reviewed-link store over their frozen cache."""
    def __init__(self, tmp_path):
        self.fixture = f = fixtures.StoreFixture(); f.build(tmp_path)
        self.records = [replace(f.record, native_id_sha256=digest(n), revision_sha256=digest('revision-'+n)) for n in 'abc']
        f.store.ingest(f.source, self.records)
        self.refs = [RecordRef(r.source_sha256, r.native_id_sha256, r.revision_sha256) for r in self.records]
        self.store = LineageStore(f.root/'lineage', project_sha256=f.source.project_sha256, scope_sha256=f.source.scope_sha256)

    def link(self, left, right, relation='same_observation'):
        f = self.fixture; claim = LineageClaim(self.refs[left], self.refs[right], relation, 'synthetic-author', (self.refs[left], self.refs[right]))
        (f.root/'claim.json').write_text(json.dumps(claim.to_dict()))
        proposed = self.store.propose(f.store.path, f.root/'claim.json')
        review = LineageReview(proposed['case_id'], proposed['event_sha256'], proposed['cache_sha256'], 'reviewed',
            'synthetic-reviewer', 'synthetic operator provenance', 'independent_metadata_review',
            'Explicit review of the frozen synthetic metadata.', (self.refs[left], self.refs[right]), 'Synthetic decision.')
        (f.root/'review.json').write_text(json.dumps(review.to_dict()))
        self.store.review(proposed['case_id'], f.store.path, f.root/'review.json')

    def grouping(self): return self.store.query(self.fixture.store.path)

    def incident(self, index, partition, root=None):
        name = 'abc'[index]; value = sample('lineage-'+name, partition); value['summary'] = SUMMARIES[name]
        value['evidence'] = [dict(id='terminal', content=name+' evidence', sha256=sha256((name+' evidence').encode()).hexdigest())]
        value['source_revision_sha256'] = self.records[index].revision_sha256
        if root: value['root_id'] = root
        return value


def reviewed(store, value):
    item = store.enqueue(value)
    for reviewer in ('synthetic-reviewer-one', 'synthetic-reviewer-two'):
        item = store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=reviewer)
        item = store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=reviewer, label=label())
    return store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer='synthetic-adjudicator',
                        label=label(), reason='Synthetic adjudication')


def export(store): return store.export_development(store.snapshot()['revision_sha256'])


def resign(manifest):
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    return dict(body, manifest_sha256=_digest(body))


@pytest.fixture
def lineage(tmp_path): return Lineage(tmp_path)


@pytest.fixture
def store(tmp_path): return CorpusStore(tmp_path/'corpus')


def test_a_later_incident_joining_a_bound_group_in_another_partition_blocks_export_and_protected_inputs(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting'))
    store.bind_lineage(lineage.grouping())
    assert len(export(store)['records']) == 1  # the control: nothing blocks before the linked incident arrives
    store.enqueue(lineage.incident(1, 'validation'))
    with pytest.raises(ValueError, match='lineage group'):
        export(store)
    with pytest.raises(ValueError, match='lineage group'):
        store.protected_inputs()


def test_binding_refuses_a_grouping_that_already_crosses_partitions_and_records_nothing(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting')); store.enqueue(lineage.incident(1, 'validation'))
    before = store.snapshot()['revision_sha256']
    with pytest.raises(ValueError, match='lineage group'):
        store.bind_lineage(lineage.grouping())
    assert store.snapshot()['revision_sha256'] == before
    assert len(export(store)['records']) == 1


def test_transitive_links_hold_the_whole_component_in_one_partition(lineage, store):
    lineage.link(0, 1); lineage.link(1, 2)
    reviewed(store, lineage.incident(0, 'fitting'))
    store.enqueue(lineage.incident(2, 'development'))  # a and c share no direct link, only b
    with pytest.raises(ValueError, match='lineage group'):
        store.bind_lineage(lineage.grouping())


def test_exposure_retirement_does_not_let_a_protected_member_join_a_development_group(lineage, store):
    lineage.link(0, 1)
    held = reviewed(store, lineage.incident(1, 'validation'))
    store.bind_lineage(lineage.grouping())
    retired = store.update(held['case_id'], held['event_sha256'], 'exposed', reason='Synthetic exposure')
    assert retired['partition'] == 'development'
    reviewed(store, lineage.incident(0, 'fitting'))  # development (retired) and fitting members of one group
    with pytest.raises(ValueError, match='lineage group'):
        export(store)


def test_a_group_inside_one_partition_exports_with_its_receipt_and_stays_unverified(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting')); reviewed(store, lineage.incident(1, 'fitting', root='lineage-b-other-root'))
    before = store.snapshot()['revision_sha256']
    result = store.bind_lineage(lineage.grouping())
    assert (result['matched_cases'], result['components'], result['multi_root_components']) == (2, 1, 1)
    assert result['review_authentication'] == 'operator_attested'; assert result['root_independence'] == 'unverified'
    assert not result['publication_allowed']
    assert store.snapshot()['revision_sha256'] != before
    manifest = export(store); development_export(manifest)
    group, = manifest['lineage_groupings']
    assert group['grouping_revision_sha256'] == lineage.grouping()['grouping_revision_sha256']
    assert len(group['components']) == 1 and len(group['components'][0]['revisions']) == 2
    assert manifest['root_independence'] == 'unverified'


def test_export_omits_group_members_that_are_not_exported(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting')); store.enqueue(lineage.incident(1, 'fitting'))  # b is unreviewed
    store.bind_lineage(lineage.grouping())
    assert export(store)['lineage_groupings'] == []  # a lone exported row needs no group check and discloses no unexported revision


def test_forged_manifest_cannot_add_a_group_that_crosses_exported_partitions(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting')); reviewed(store, lineage.incident(1, 'development'))
    manifest = export(store); development_export(manifest)
    assert manifest['lineage_groupings'] == []
    revisions = sorted(r.revision_sha256 for r in lineage.records[:2])
    forged = dict(manifest, lineage_groupings=[dict(grouping_revision_sha256='f'*64, components=[dict(component_sha256='e'*64, revisions=revisions)])])
    with pytest.raises(ValueError, match='lineage group'):
        development_export(resign(forged))


def test_unlinked_incidents_in_different_partitions_stay_separate(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting')); reviewed(store, lineage.incident(2, 'development'))
    result = store.bind_lineage(lineage.grouping())
    assert result['matched_cases'] == 1  # c belongs to no reviewed component: unlinked is not independent, only unbound
    assert len(export(store)['records']) == 2


def test_a_grouping_matching_no_incident_binds_and_reports_zero_matches(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(2, 'fitting'))
    assert store.bind_lineage(lineage.grouping())['matched_cases'] == 0


def test_the_same_grouping_cannot_be_bound_twice(lineage, store):
    lineage.link(0, 1); store.bind_lineage(lineage.grouping())
    with pytest.raises(ValueError, match='already recorded'):
        store.bind_lineage(lineage.grouping())


@pytest.mark.parametrize('change', [
    pytest.param(lambda g: g.update(conflicts=[dict(case_id='x', reason='derivation_cycle')]), id='conflicts'),
    pytest.param(lambda g: g.update(stale_cases=['x'] ), id='stale-cases'),
    pytest.param(lambda g: g.update(independent_roots=3), id='claimed-independent-roots'),
    pytest.param(lambda g: g.update(publication_allowed=True), id='publication-allowed'),
    pytest.param(lambda g: g.update(task_outcomes_verified=True), id='verified-outcomes'),
    pytest.param(lambda g: g.update(native_freshness='verified'), id='claimed-freshness'),
    pytest.param(lambda g: g.update(applicability='anything'), id='applicability'),
    pytest.param(lambda g: g.update(schema_version=2), id='schema-version'),
    pytest.param(lambda g: g.update(grouping_revision_sha256='short'), id='revision-format'),
    pytest.param(lambda g: g['components'][0].update(component_sha256='0'*64), id='tampered-component-digest'),
    pytest.param(lambda g: g.update(components=g['components']*2), id='duplicate-component'),
    pytest.param(lambda g: g.pop('components'), id='missing-components'),
    pytest.param(lambda g: g.update(components='none'), id='components-not-a-list'),
    pytest.param(lambda g: g['components'][0]['members'].append(dict(g['components'][0]['members'][0], bogus=1)), id='extra-member-field'),
])
def test_invalid_or_unresolved_groupings_are_refused_and_record_nothing(lineage, store, change):
    lineage.link(0, 1); grouping = deepcopy(lineage.grouping()); change(grouping)
    reviewed(store, lineage.incident(2, 'fitting'))
    before = store.snapshot()['revision_sha256']
    with pytest.raises(ValueError):
        store.bind_lineage(grouping)
    assert store.snapshot()['revision_sha256'] == before


def test_an_oversized_grouping_is_refused(lineage, store):
    lineage.link(0, 1); grouping = deepcopy(lineage.grouping())
    member = grouping['components'][0]['members'][0]
    grouping['components'][0]['members'] = [dict(member, revision_sha256=sha256(str(i).encode()).hexdigest()) for i in range(4097)]
    grouping['components'][0]['component_sha256'] = digest(grouping['components'][0]['members'])
    with pytest.raises(ValueError, match='bound'):
        store.bind_lineage(grouping)


def test_a_grouping_bound_before_any_incident_still_holds_a_later_pair_apart(lineage, store):
    lineage.link(0, 1)
    store.bind_lineage(lineage.grouping())
    reviewed(store, lineage.incident(0, 'fitting')); reviewed(store, lineage.incident(1, 'development'))
    with pytest.raises(ValueError, match='lineage group'):
        export(store)


def test_many_components_cannot_exceed_the_total_revision_bound(lineage, store):
    lineage.link(0, 1); grouping = deepcopy(lineage.grouping())
    member = grouping['components'][0]['members'][0]
    grouping['components'] = [dict(component_sha256=digest(rows), members=rows) for rows in
        ([dict(member, revision_sha256=sha256(f'{part}-{i}'.encode()).hexdigest()) for i in range(3000)] for part in 'xy')]
    with pytest.raises(ValueError, match='bound'):
        store.bind_lineage(grouping)


def test_a_changed_recorded_grouping_changes_the_corpus_revision(lineage, store):
    lineage.link(0, 1)
    reviewed(store, lineage.incident(0, 'fitting'))
    store.bind_lineage(lineage.grouping())
    before = store.snapshot()['revision_sha256']
    path, = (store.root/'lineage_groupings').iterdir()
    value = json.loads(path.read_text()); value['components'][0]['revisions'].pop()
    path.write_text(json.dumps(value))
    assert store.snapshot()['revision_sha256'] != before
