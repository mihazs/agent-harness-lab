"""Synthetic corpus-to-benchmark controls; no model, private traces or gold claims."""
from copy import deepcopy
import hashlib
import json
import subprocess
import sys

import pytest

from agent_harness_lab.diagnostics.corpus import CorpusStore, development_export
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.private_journal import encoded
from tools import benchmark_models as bench

if __package__:
    from . import test_benchmark_contracts as benchmark_fixtures
    from .test_diagnostic_corpus import label, sample
else:
    import test_benchmark_contracts as benchmark_fixtures
    from test_diagnostic_corpus import label, sample


def case(name, partition='development', outcome='task_failure', root=None):
    value = sample(name, partition)
    value['summary'] = f'Synthetic {name} terminal record for an offline corpus control.'
    if root is not None:
        value['root_id'] = root
    return value, label(outcome, 'unrecovered' if outcome == 'task_failure' else 'not_applicable')


def resign(manifest):
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    return dict(body, manifest_sha256=hashlib.sha256(encoded(body)).hexdigest())


PROTECTED = [pytest.param(partition, id=partition) for partition in ('validation', 'certification', 'calibration', 'threshold')]


class Corpus:
    """A private corpus store under a temporary folder, with helpers to adjudicate and export it."""

    def __init__(self, folder):
        self.folder = folder; self.store = CorpusStore(self.folder/'corpus')

    def adjudicate(self, value, annotation):
        item = self.store.enqueue(value)
        for reviewer in ('synthetic-reviewer-one', 'synthetic-reviewer-two'):
            item = self.store.update(item['case_id'], item['event_sha256'], 'claimed', reviewer=reviewer)
            item = self.store.update(item['case_id'], item['event_sha256'], 'submitted', reviewer=reviewer, label=annotation)
        return self.store.update(item['case_id'], item['event_sha256'], 'adjudicated', reviewer='synthetic-adjudicator',
                                 label=annotation, reason='Synthetic adjudication basis')

    def corpus(self):
        for name, partition, outcome, root in (('fit-failure', 'fitting', 'task_failure', None),
                ('fit-success', 'fitting', 'successful', None), ('dev-first', 'development', 'task_failure', 'shared-root'),
                ('dev-repeat', 'development', 'successful', 'shared-root'), ('dev-outage', 'development', 'external_failure', None),
                ('held-validation', 'validation', 'task_failure', None), ('held-certification', 'certification', 'successful', None)):
            self.adjudicate(*case(name, partition, outcome, root))
        return self.store.export_development(self.store.snapshot()['revision_sha256'])

    def write(self, manifest, name='export.json'):
        path = self.folder/name; path.write_bytes(encoded(manifest)); return path

    def run_cli(self, path, *extra):
        return subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(path), '--profile', 'diagnostic-corpus',
                               '--method', 'baseline', *extra], capture_output=True, text=True)


@pytest.fixture
def env(tmp_path):
    return Corpus(tmp_path)


def test_provenance_split_and_descriptive_private_report_are_preserved(env):
    path = env.write(env.corpus())
    rows, _ = bench.read_corpus_manifest(path)
    assert {r['label_provenance'] for r in rows} == {'adjudicated'}
    assert {(r['declared_partition'], r['split']) for r in rows} == {('fitting', 'train'), ('development', 'development')}
    completed = env.run_cli(path)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report['input_profile'] == 'diagnostic_corpus'
    assert report['label_provenance_counts'] == dict(adjudicated=5, single_review=0, adapter_smoke=0)
    assert (report['reviewed_annotations'], report['gold_labels']) == (5, 0)
    assert (report['qualification'], report['certification']) == ('descriptive_only', 'not_issued')
    assert report['outcome_counts']['development'] == dict(task_failure=1, successful=1, external_failure=1)
    assert report['splits']['development']['unlabeled_outcomes'] == 1
    assert not any('unknown' in metrics for metrics in (*report['splits'].values(), *report['projects'].values()))
    assert report['threshold'] is None
    assert not report['publication_allowed']; assert not report['classifier_selection_allowed']
    for forged in (report, {**report, **benchmark_fixtures.record(), 'publication_allowed': True}):
        assert not public_export_allowed([forged])


def test_held_out_items_are_absent_and_cannot_be_reintroduced(env):
    manifest = env.corpus()
    rows, _ = bench.read_corpus_manifest(env.write(manifest))
    assert not ({r['declared_partition'] for r in rows} & {'validation', 'certification'})
    assert not ({r['split'] for r in rows} & {'calibration', 'heldout'})
    policy = env.write(dict(schema_version=1, threshold=.9, min_precision=.9, min_recall=.9, min_coverage=.9), 'policy.json')
    refused = env.run_cli(env.write(manifest), '--triage-policy', str(policy))
    assert refused.returncode == 2; assert 'no held-out roots' in refused.stderr


@pytest.mark.parametrize('partition', PROTECTED)
def test_held_out_partition_cannot_be_reintroduced(env, partition):
    forged = deepcopy(env.corpus()); forged['records'][0]['partition'] = partition
    forged['records'][0]['record']['partition'] = partition
    forged['records'][0]['case_id'] = hashlib.sha256(encoded(forged['records'][0]['record'])).hexdigest()
    with pytest.raises(ValueError, match='protected'):
        development_export(resign(forged))


@pytest.mark.parametrize('partition', PROTECTED)
def test_declared_held_out_rows_need_retirement_evidence(env, partition):
    forged = deepcopy(env.corpus()); row = deepcopy(forged['records'][2])
    row['record'].update(partition=partition, incident_id='forged-'+partition, root_id='forged-root-'+partition,
        summary='Forged synthetic held-out text '+partition, source_revision_sha256=hashlib.sha256(partition.encode()).hexdigest())
    row['case_id'] = hashlib.sha256(encoded(row['record'])).hexdigest(); forged['records'].append(row)
    with pytest.raises(ValueError, match='retirement'):
        development_export(resign(forged))


def test_declared_held_out_rows_need_retirement_evidence_and_are_disclosed(env):
    env.corpus()
    retired = env.adjudicate(*case('retired-certification', 'certification'))
    env.store.update(retired['case_id'], retired['event_sha256'], 'exposed', reason='Synthetic development exposure')
    export = env.store.export_development(env.store.snapshot()['revision_sha256'])
    row = next(r for r in export['records'] if r['case_id'] == retired['case_id'])
    assert (row['partition'], row['record']['partition']) == ('development', 'certification')
    assert row['retirement']['event_sha256'] == row['review_event_sha256']
    assert all(r['retirement'] is None for r in export['records'] if r is not row)
    stripped = deepcopy(export); next(r for r in stripped['records'] if r['case_id'] == retired['case_id'])['retirement'] = None
    with pytest.raises(ValueError, match='retirement'): development_export(resign(stripped))
    completed = env.run_cli(env.write(export))
    assert completed.returncode == 0, completed.stderr
    disclosed = json.loads(completed.stdout)['partitions']
    assert disclosed['development'] == dict(declared=dict(development=3, certification=1), retired=1, retired_from_protected=1)
    assert disclosed['train'] == dict(declared=dict(fitting=2), retired=0, retired_from_protected=0)


def test_requeued_retirement_cites_the_exposed_revision(env):
    value, annotation = case('lineage', 'validation')
    old = env.store.enqueue(value)
    old = env.store.update(old['case_id'], old['event_sha256'], 'exposed', reason='Synthetic exposure before review')
    changed = deepcopy(value); changed.update(rubric_sha256='b'*64, supersedes=old['case_id'])
    env.adjudicate(changed, annotation)
    export = env.store.export_development(env.store.snapshot()['revision_sha256'])
    row, = export['records']
    assert (row['partition'], row['retirement']['case_id']) == ('development', old['case_id'])
    assert development_export(export) == export
    orphan = deepcopy(export); orphan['records'][0]['record']['supersedes'] = None
    orphan['records'][0]['case_id'] = hashlib.sha256(encoded(orphan['records'][0]['record'])).hexdigest()
    with pytest.raises(ValueError, match='retirement'): development_export(resign(orphan))


def test_adapter_smoke_labels_never_count_as_reviewed(env):
    smoke = env.folder/'smoke.jsonl'
    smoke.write_text('\n'.join(json.dumps(dict(root_case_id=f'r{i}', split=s, text='synthetic', label=bool(i % 2)))
                               for i, s in enumerate(('train', 'train', 'heldout', 'heldout'))))
    completed = subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(smoke), '--method', 'baseline'],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report['input_profile'] == 'adapter_smoke'
    assert report['label_provenance_counts'] == dict(adjudicated=0, single_review=0, adapter_smoke=4)
    assert (report['reviewed_annotations'], report['gold_labels']) == (0, 0)
    assert {p['label_provenance'] for p in report['predictions']} == {'adapter_smoke'}
    assert report['splits']['heldout']['unknown'] == 0; assert 'unlabeled_outcomes' not in report['splits']['heldout']


@pytest.mark.parametrize('claimed', [pytest.param(claimed, id=claimed) for claimed in ('adjudicated', 'single_review', 'gold')])
def test_adapter_smoke_labels_cannot_claim_review_provenance(env, claimed):
    smoke = env.folder/'smoke.jsonl'
    smoke.write_text(json.dumps(dict(root_case_id='r', split='train', text='synthetic', label=True, label_provenance=claimed)))
    with pytest.raises(ValueError): bench.read_examples(smoke)


@pytest.mark.parametrize('key,value', [
    pytest.param('label_provenance', 'adapter_smoke', id='label_provenance'),
    pytest.param('gold_label', True, id='gold_label'),
])
def test_adjudicated_rows_cannot_be_relabelled(env, key, value):
    forged = deepcopy(env.corpus()); forged['records'][0][key] = value
    with pytest.raises(ValueError, match='relabelled'): development_export(resign(forged))


def test_repeated_roots_are_grouped_not_independent(env):
    rows, _ = bench.read_corpus_manifest(env.write(env.corpus()))
    groups = bench.group_diagnostics(rows)['development']
    assert (groups['rows'], groups['roots'], groups['repeated_root_rows'], groups['max_rows_per_root']) == (3, 2, 1, 2)
    assert groups['conflicting_label_roots'] == 1
    development = [r for r in rows if r['split'] == 'development']
    assert bench.measure(development, [.99]*len(development), .5)['accepted_roots'] == 1


def test_root_weighted_brier_counts_each_root_once_and_reports_conflicts(env):
    rows = [dict(root_case_id=root, label=label) for root, label in (('a', True), ('a', True), ('b', False), ('c', True), ('c', False), ('d', None))]
    result = bench.root_weighted(rows, [.9, .7, .2, .5, .5, .1])
    # unittest's assertAlmostEqual: equal after rounding the difference to seven places
    assert round(abs(result['brier'] - ((.01+.09)/2+.04)/2), 7) == 0
    assert (result['roots'], result['conflicting_label_roots_excluded'], result['unlabeled_roots']) == (2, 1, 1)
    assert bench.root_weighted([dict(root_case_id='c', label=True), dict(root_case_id='c', label=False)], [.5, .5])['brier'] is None
    report = json.loads(env.run_cli(env.write(env.corpus())).stdout)
    assert report['metric_weighting'] == dict(splits='row', root_weighted='declared_root')
    assert report['root_weighted']['development']['conflicting_label_roots_excluded'] == 1
    assert report['root_weighted']['train']['roots'] == 2


def _changed(manifest):
    changed = deepcopy(manifest); changed['records'][0]['annotation']['outcome'] = 'successful'; return changed


def _summary(manifest):
    summary = deepcopy(manifest); summary['records'][0]['record']['summary'] = 'Replaced synthetic summary text.'
    return resign(summary)


def _authority(manifest):
    authority = deepcopy(manifest); authority['classifier_selection_allowed'] = True; return resign(authority)


def _legacy(manifest):
    return {k: v for k, v in manifest.items() if k != 'manifest_sha256'}


def _duplicate(manifest):
    duplicate = deepcopy(manifest); duplicate['records'].append(deepcopy(manifest['records'][0])); return resign(duplicate)


@pytest.mark.parametrize('forge,match', [
    pytest.param(_changed, 'digest', id='digest'),
    pytest.param(_summary, 'identity', id='identity-summary'),
    pytest.param(_authority, 'authority', id='authority'),
    pytest.param(_legacy, 'schema', id='fields'),
    pytest.param(_duplicate, 'identity', id='identity-duplicate'),
])
def test_tampered_or_legacy_manifest_is_rejected(env, forge, match):
    with pytest.raises(ValueError, match=match):
        development_export(forge(env.corpus()))


def test_tampered_or_legacy_manifests_are_rejected(env):
    manifest = env.corpus(); path = env.write(manifest)
    assert development_export(manifest) == manifest
    path.write_bytes(path.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1', 1))
    rejected = env.run_cli(path)
    assert rejected.returncode == 2; assert rejected.stdout == ''
    env.write(_changed(manifest))
    assert 'digest differs' in env.run_cli(path).stderr
