import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('benchmark_models', Path(__file__).resolve().parents[3] / 'tools/benchmark_models.py')
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


def row(root, label, split='calibration'):
    return dict(root_case_id=root, label=label, text='redacted evidence', split=split)


def test_abstention_hurts_recall_unknowns_are_not_negative_labels():
    result = bench.measure([row('a', True), row('b', True), row('c', False), row('d', None)], [.99, .6, .01, .99], .9)
    assert result['precision'] == 1
    assert result['recall'] == .5
    assert result['accepted'] == 2
    assert result['unknown'] == 1
    # unittest's assertAlmostEqual: equal after rounding the difference to seven places
    assert round(abs(result['brier'] - (.0001+.16+.0001)/3), 7) == 0


def test_no_labels_and_small_calibration_abstain():
    assert bench.choose_threshold([row('a', True)], [.99]) is None
    assert bench.measure([row('a', None)], [.99], .9)['brier'] is None
    assert bench.measure([row('a', True)], [.99], None)['coverage'] == 0


def test_repeated_events_do_not_inflate_independent_calibration():
    rows = [row('one-root', True) for _ in range(100)]
    assert bench.choose_threshold(rows, [.99]*100) is None
    rows = [row(str(i), True) for i in range(40)]
    assert bench.choose_threshold(rows, [.99]*40) == .5


def test_probability_validation():
    for values in ([float('nan')], [-.1], [1.1], []):
        with pytest.raises(ValueError):
            bench.measure([row('a', True)], values, .9)


def test_split_leakage_and_nonboolean_labels_fail(tmp_path):
    for index, rows in enumerate(([row('same', True, 'train'), row('same', False, 'heldout')],
                                  [row('a', 1)], [{'root_case_id': 'a', 'text': '', 'split': 'train'}])):
        path = tmp_path/str(index)/'cases.jsonl'; path.parent.mkdir()
        path.write_text('\n'.join(json.dumps(r) for r in rows))
        with pytest.raises(ValueError):
            bench.read_examples(path)


def test_limit_and_valid_missing_label(tmp_path):
    path = tmp_path/'cases.jsonl'
    path.write_text(json.dumps(row('a', None))+'\n'+json.dumps(row('b', True)))
    assert len(bench.read_examples(path)) == 2
    with pytest.raises(ValueError):
        bench.read_examples(path, limit=1)


def test_example_fixture_has_separate_roots_and_both_labels():
    fixture_spec = importlib.util.spec_from_file_location('fixture', Path(__file__).parents[3]/'examples/classifier_fixture.py')
    fixture = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture)
    rows = list(fixture.examples())
    assert len(rows) == len({r['root_case_id'] for r in rows})
    for split in bench.SPLITS:
        assert {r['label'] for r in rows if r['split'] == split} == {False, True}


def test_default_reader_accepts_more_than_previous_row_and_text_caps(tmp_path):
    path = tmp_path/'examples.jsonl'
    long = dict(row('long', True), text='x' * 16385)
    path.write_text(json.dumps(long)+'\n'+(json.dumps(row('same', None))+'\n')*50000)
    result = bench.read_examples(path)
    assert len(result) == 50001
    assert result[0]['text'] == long['text']


@pytest.fixture
def two_examples(tmp_path):
    path = tmp_path/'examples.jsonl'
    path.write_text(json.dumps(row('one', None))+'\n'+json.dumps(row('two', True)))
    return path


def test_explicit_row_limit_remains_optional(two_examples):
    assert len(bench.read_examples(two_examples, limit=2)) == 2


@pytest.mark.parametrize('limit', [
    pytest.param(1, id='below-count'), pytest.param(0, id='zero'), pytest.param(-1, id='negative'),
    pytest.param(True, id='bool'), pytest.param(1.5, id='fractional')])
def test_explicit_row_limit_remains_optional_and_validated(two_examples, limit):
    with pytest.raises(ValueError):
        bench.read_examples(two_examples, limit=limit)


@pytest.mark.parametrize('text', [
    pytest.param('', id='empty'), pytest.param(None, id='none'), pytest.param([], id='list'),
    pytest.param(5, id='number')])
def test_unbounded_text_still_requires_a_nonempty_string(tmp_path, text):
    path = tmp_path/'examples.jsonl'
    path.write_text(json.dumps(dict(row('one', True), text=text)))
    with pytest.raises(ValueError):
        bench.read_examples(path)


def test_public_cli_supports_only_user_requested_row_limit(tmp_path):
    path = tmp_path/'examples.jsonl'
    path.write_text(json.dumps(dict(row('one', True, 'train'), text='x'*16385))+'\n'+
                    json.dumps(row('two', False, 'heldout')))
    command = [sys.executable, '-m', 'tools.benchmark_models', str(path), '--method', 'baseline']
    accepted = subprocess.run(command, capture_output=True, text=True)
    assert accepted.returncode == 0, accepted.stderr
    assert ('TF-IDF retains all extracted training-vocabulary features; unseen terms remain out of vocabulary.'
            in json.loads(accepted.stdout)['limitations'])
    rejected = subprocess.run([*command, '--max-examples', '1'], capture_output=True, text=True)
    assert rejected.returncode == 2
    assert 'example limit exceeded' in rejected.stderr
