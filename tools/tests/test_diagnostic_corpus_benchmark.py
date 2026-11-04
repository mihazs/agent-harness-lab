"""Synthetic corpus-to-benchmark controls; no model, private traces or gold claims."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

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


class CorpusBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name); self.store = CorpusStore(self.folder/'corpus')

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

    def test_provenance_split_and_descriptive_private_report_are_preserved(self):
        path = self.write(self.corpus())
        rows, _ = bench.read_corpus_manifest(path)
        self.assertEqual({r['label_provenance'] for r in rows}, {'adjudicated'})
        self.assertEqual({(r['declared_partition'], r['split']) for r in rows}, {('fitting', 'train'), ('development', 'development')})
        completed = self.run_cli(path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report['input_profile'], 'diagnostic_corpus')
        self.assertEqual(report['label_provenance_counts'], dict(adjudicated=5, single_review=0, adapter_smoke=0))
        self.assertEqual((report['reviewed_annotations'], report['gold_labels']), (5, 0))
        self.assertEqual((report['qualification'], report['certification']), ('descriptive_only', 'not_issued'))
        self.assertEqual(report['outcome_counts']['development'], dict(task_failure=1, successful=1, external_failure=1))
        self.assertEqual(report['splits']['development']['unlabeled_outcomes'], 1)
        self.assertFalse(any('unknown' in metrics for metrics in (*report['splits'].values(), *report['projects'].values())))
        self.assertIsNone(report['threshold'])
        self.assertFalse(report['publication_allowed']); self.assertFalse(report['classifier_selection_allowed'])
        for forged in (report, {**report, **benchmark_fixtures.record(), 'publication_allowed': True}):
            self.assertFalse(public_export_allowed([forged]))

    def test_held_out_items_are_absent_and_cannot_be_reintroduced(self):
        manifest = self.corpus()
        rows, _ = bench.read_corpus_manifest(self.write(manifest))
        self.assertFalse({r['declared_partition'] for r in rows} & {'validation', 'certification'})
        self.assertFalse({r['split'] for r in rows} & {'calibration', 'heldout'})
        for partition in ('validation', 'certification', 'calibration', 'threshold'):
            forged = deepcopy(manifest); forged['records'][0]['partition'] = partition
            forged['records'][0]['record']['partition'] = partition
            forged['records'][0]['case_id'] = hashlib.sha256(encoded(forged['records'][0]['record'])).hexdigest()
            with self.subTest(partition=partition), self.assertRaisesRegex(ValueError, 'protected'):
                development_export(resign(forged))
        policy = self.write(dict(schema_version=1, threshold=.9, min_precision=.9, min_recall=.9, min_coverage=.9), 'policy.json')
        refused = self.run_cli(self.write(manifest), '--triage-policy', str(policy))
        self.assertEqual(refused.returncode, 2); self.assertIn('no held-out roots', refused.stderr)

    def test_declared_held_out_rows_need_retirement_evidence_and_are_disclosed(self):
        manifest = self.corpus()
        for partition in ('validation', 'certification', 'calibration', 'threshold'):
            forged = deepcopy(manifest); row = deepcopy(forged['records'][2])
            row['record'].update(partition=partition, incident_id='forged-'+partition, root_id='forged-root-'+partition,
                summary='Forged synthetic held-out text '+partition, source_revision_sha256=hashlib.sha256(partition.encode()).hexdigest())
            row['case_id'] = hashlib.sha256(encoded(row['record'])).hexdigest(); forged['records'].append(row)
            with self.subTest(partition=partition), self.assertRaisesRegex(ValueError, 'retirement'):
                development_export(resign(forged))
        retired = self.adjudicate(*case('retired-certification', 'certification'))
        self.store.update(retired['case_id'], retired['event_sha256'], 'exposed', reason='Synthetic development exposure')
        export = self.store.export_development(self.store.snapshot()['revision_sha256'])
        row = next(r for r in export['records'] if r['case_id'] == retired['case_id'])
        self.assertEqual((row['partition'], row['record']['partition']), ('development', 'certification'))
        self.assertEqual(row['retirement']['event_sha256'], row['review_event_sha256'])
        self.assertTrue(all(r['retirement'] is None for r in export['records'] if r is not row))
        stripped = deepcopy(export); next(r for r in stripped['records'] if r['case_id'] == retired['case_id'])['retirement'] = None
        with self.assertRaisesRegex(ValueError, 'retirement'): development_export(resign(stripped))
        completed = self.run_cli(self.write(export))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        disclosed = json.loads(completed.stdout)['partitions']
        self.assertEqual(disclosed['development'], dict(declared=dict(development=3, certification=1), retired=1, retired_from_protected=1))
        self.assertEqual(disclosed['train'], dict(declared=dict(fitting=2), retired=0, retired_from_protected=0))

    def test_requeued_retirement_cites_the_exposed_revision(self):
        value, annotation = case('lineage', 'validation')
        old = self.store.enqueue(value)
        old = self.store.update(old['case_id'], old['event_sha256'], 'exposed', reason='Synthetic exposure before review')
        changed = deepcopy(value); changed.update(rubric_sha256='b'*64, supersedes=old['case_id'])
        self.adjudicate(changed, annotation)
        export = self.store.export_development(self.store.snapshot()['revision_sha256'])
        row, = export['records']
        self.assertEqual((row['partition'], row['retirement']['case_id']), ('development', old['case_id']))
        self.assertEqual(development_export(export), export)
        orphan = deepcopy(export); orphan['records'][0]['record']['supersedes'] = None
        orphan['records'][0]['case_id'] = hashlib.sha256(encoded(orphan['records'][0]['record'])).hexdigest()
        with self.assertRaisesRegex(ValueError, 'retirement'): development_export(resign(orphan))

    def test_adapter_smoke_labels_never_count_as_reviewed(self):
        smoke = self.folder/'smoke.jsonl'
        smoke.write_text('\n'.join(json.dumps(dict(root_case_id=f'r{i}', split=s, text='synthetic', label=bool(i % 2)))
                                   for i, s in enumerate(('train', 'train', 'heldout', 'heldout'))))
        completed = subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(smoke), '--method', 'baseline'],
                                   capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report['input_profile'], 'adapter_smoke')
        self.assertEqual(report['label_provenance_counts'], dict(adjudicated=0, single_review=0, adapter_smoke=4))
        self.assertEqual((report['reviewed_annotations'], report['gold_labels']), (0, 0))
        self.assertEqual({p['label_provenance'] for p in report['predictions']}, {'adapter_smoke'})
        self.assertEqual(report['splits']['heldout']['unknown'], 0); self.assertNotIn('unlabeled_outcomes', report['splits']['heldout'])
        for claimed in ('adjudicated', 'single_review', 'gold'):
            smoke.write_text(json.dumps(dict(root_case_id='r', split='train', text='synthetic', label=True, label_provenance=claimed)))
            with self.subTest(claimed=claimed), self.assertRaises(ValueError): bench.read_examples(smoke)
        manifest = self.corpus()
        for key, value in (('label_provenance', 'adapter_smoke'), ('gold_label', True)):
            forged = deepcopy(manifest); forged['records'][0][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'relabelled'): development_export(resign(forged))

    def test_repeated_roots_are_grouped_not_independent(self):
        rows, _ = bench.read_corpus_manifest(self.write(self.corpus()))
        groups = bench.group_diagnostics(rows)['development']
        self.assertEqual((groups['rows'], groups['roots'], groups['repeated_root_rows'], groups['max_rows_per_root']), (3, 2, 1, 2))
        self.assertEqual(groups['conflicting_label_roots'], 1)
        development = [r for r in rows if r['split'] == 'development']
        self.assertEqual(bench.measure(development, [.99]*len(development), .5)['accepted_roots'], 1)

    def test_root_weighted_brier_counts_each_root_once_and_reports_conflicts(self):
        rows = [dict(root_case_id=root, label=label) for root, label in (('a', True), ('a', True), ('b', False), ('c', True), ('c', False), ('d', None))]
        result = bench.root_weighted(rows, [.9, .7, .2, .5, .5, .1])
        self.assertAlmostEqual(result['brier'], ((.01+.09)/2+.04)/2)
        self.assertEqual((result['roots'], result['conflicting_label_roots_excluded'], result['unlabeled_roots']), (2, 1, 1))
        self.assertIsNone(bench.root_weighted([dict(root_case_id='c', label=True), dict(root_case_id='c', label=False)], [.5, .5])['brier'])
        report = json.loads(self.run_cli(self.write(self.corpus())).stdout)
        self.assertEqual(report['metric_weighting'], dict(splits='row', root_weighted='declared_root'))
        self.assertEqual(report['root_weighted']['development']['conflicting_label_roots_excluded'], 1)
        self.assertEqual(report['root_weighted']['train']['roots'], 2)

    def test_tampered_or_legacy_manifests_are_rejected(self):
        manifest = self.corpus(); path = self.write(manifest)
        self.assertEqual(development_export(manifest), manifest)
        changed = deepcopy(manifest); changed['records'][0]['annotation']['outcome'] = 'successful'
        summary = deepcopy(manifest); summary['records'][0]['record']['summary'] = 'Replaced synthetic summary text.'
        authority = deepcopy(manifest); authority['classifier_selection_allowed'] = True
        legacy = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
        duplicate = deepcopy(manifest); duplicate['records'].append(deepcopy(manifest['records'][0]))
        for name, forged in (('digest', changed), ('identity', resign(summary)), ('authority', resign(authority)),
                             ('fields', legacy), ('identity', resign(duplicate))):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, name if name != 'fields' else 'schema'):
                development_export(forged)
        path.write_bytes(path.read_bytes().replace(b'"schema_version": 1', b'"schema_version": 1, "schema_version": 1', 1))
        rejected = self.run_cli(path)
        self.assertEqual(rejected.returncode, 2); self.assertEqual(rejected.stdout, '')
        self.write(changed)
        self.assertIn('digest differs', self.run_cli(path).stderr)


if __name__ == '__main__':
    unittest.main()
