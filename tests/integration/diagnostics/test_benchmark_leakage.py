import json
import subprocess
import sys


from tools import benchmark_models as bench


def line(root, split, text, label=None, **extra):
    return dict(root_case_id=root, split=split, text=text, label=label, **extra)


def write(tmp_path, rows):
    source = tmp_path / 'examples.jsonl'
    source.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
    return source


def run(source, *args):
    return subprocess.run([sys.executable, '-m', 'tools.benchmark_models', str(source), '--method', 'baseline', *args],
                          capture_output=True, text=True)


def test_shared_project_and_copied_text_are_disclosed_and_optionally_rejected(tmp_path):
    source = write(tmp_path, [line('a', 'train', 'Tool  failed twice', True, project='p'), line('b', 'train', 'two', False, project='q'),
                              line('c', 'heldout', 'tool failed TWICE', True, project='p')])
    assert bench.leakage(bench.read_examples(source)) == dict(shared_train_heldout_projects=['p'], texts_in_multiple_splits=1)
    assert json.loads(run(source).stdout)['leakage']['shared_train_heldout_projects'] == ['p']
    rejected = run(source, '--reject-leakage')
    assert rejected.returncode == 2 and 'project or text crosses splits' in rejected.stderr


def test_disjoint_projects_and_texts_pass_the_leakage_gate(tmp_path):
    source = write(tmp_path, [line('a', 'train', 'one', True, project='p'), line('b', 'heldout', 'two', True, project='q')])
    assert run(source, '--reject-leakage').returncode == 0


def test_heldout_labels_cannot_change_predictions(tmp_path):
    train = [line('a', 'train', 'one', True), line('b', 'train', 'two', False)]
    flipped = [json.loads(run(write(tmp_path, train + [line('c', 'heldout', 'three', label)])).stdout)['predictions']
               for label in (True, False)]
    assert [p['probability'] for p in flipped[0]] == [p['probability'] for p in flipped[1]]


def test_rare_positive_misses_are_counted_beside_high_accuracy():
    rows = [dict(root_case_id=str(i), label=i >= 98, split='heldout', text='x') for i in range(100)]
    metrics = bench.measure(rows, [.01] * 100, .5)
    assert metrics['accepted_label_accuracy'] == .98
    assert (metrics['positives'], metrics['positive_misses'], metrics['recall']) == (2, 2, 0)


def test_no_calibration_roots_means_every_prediction_abstains(tmp_path):
    source = write(tmp_path, [line('a', 'train', 'one', True), line('b', 'heldout', 'three', True)])
    report = json.loads(run(source).stdout)
    assert report['threshold'] is None and all(p['abstain'] for p in report['predictions'])
    assert report['splits']['heldout']['accepted'] == 0 and report['splits']['heldout']['coverage'] == 0
