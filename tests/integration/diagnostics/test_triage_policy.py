import json
import subprocess
import sys

import pytest

from tools import benchmark_models as bench


def policy(**changes):
    return dict(schema_version=1, threshold=.5, min_precision=.8,
                min_recall=.8, min_coverage=.5, **changes)


def row(root, label):
    return dict(root_case_id=root, label=label, split='heldout', text='synthetic evidence')


def test_majority_accuracy_cannot_hide_zero_incident_recall():
    rows = [row(str(i), i >= 990) for i in range(1000)]
    scores = [.01] * len(rows)
    assert bench.choose_threshold(rows, scores) == .5
    result = bench.evaluate_triage_policy(rows, scores, policy())
    assert result['status'] == 'rejected'
    assert result['metrics']['recall'] == 0
    assert result['metrics']['fn'] == 10
    assert result['metrics']['precision'] is None
    assert 'recall_below_minimum' in result['reasons']


def test_good_empirical_scores_do_not_establish_independent_validation():
    result = bench.evaluate_triage_policy([row('p', True), row('n', False)], [.99, .01], policy())
    assert result['status'] == 'insufficient_evidence'
    assert result['metrics']['coverage'] == 1
    assert result['root_independence'] == 'unverified'
    assert not result['automatic_promotion']


def test_repeated_root_counts_once_and_conflicting_decisions_abstain():
    rows = [row('p', True)] * 20 + [row('n', False)] * 20
    first = bench.evaluate_triage_policy(rows, [.99] * 20 + [.01] * 20, policy())
    assert first['metrics']['labeled_roots'] == 2
    scores = [.99] * 19 + [.01] + [.01] * 20
    result = bench.evaluate_triage_policy(rows, scores, policy())
    assert result['metrics']['abstained_positive'] == 1
    assert result['metrics']['recall'] == 0


def test_abstention_and_missing_class_cannot_qualify():
    abstained = bench.evaluate_triage_policy([row('p', True), row('n', False)], [.6, .4],
                                             dict(policy(), threshold=.9))
    assert abstained['status'] == 'rejected'
    assert abstained['metrics']['coverage'] == 0
    missing = bench.evaluate_triage_policy([row('n', False)], [.01], policy())
    assert missing['metrics']['recall'] is None
    assert missing['status'] != 'accepted_for_shadow'


@pytest.mark.parametrize('changes', (
    pytest.param(dict(threshold=float('nan')), id='nan-threshold'),
    pytest.param(dict(min_recall=True), id='boolean-minimum'),
    pytest.param(dict(extra=1), id='extra-key'),
    pytest.param(dict(schema_version=2), id='schema-version')))
def test_invalid_policy_rejects(changes):
    with pytest.raises(ValueError):
        bench.evaluate_triage_policy([row('p', True)], [.99], dict(policy(), **changes))


@pytest.mark.parametrize('rows, scores', (
    pytest.param([dict(row('p', True), split='calibration')], [.99], id='calibration-split'),
    pytest.param([row('p', True), row('p', False)], [.99, .01], id='conflicting-labels'),
    pytest.param([row('p', True)], [float('nan')], id='nan-score')))
def test_invalid_validation_input_rejects(rows, scores):
    with pytest.raises(ValueError):
        bench.evaluate_triage_policy(rows, scores, policy())


def test_public_cli_uses_frozen_policy_on_heldout_only(tmp_path):
    root = tmp_path
    source, configuration = root/'examples.jsonl', root/'policy.json'
    source.write_text('\n'.join(json.dumps(r) for r in
        [dict(row('train', False), split='train', text='x'*16385), row('p', True), row('n', False)]))
    configuration.write_text(json.dumps(policy()))
    command = [sys.executable, '-m', 'tools.benchmark_models', str(source),
               '--method', 'baseline', '--triage-policy', str(configuration)]
    process = subprocess.run(command, capture_output=True, text=True)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result['triage']['metrics']['labeled_roots'] == 2
    assert result['triage']['status'] == 'rejected'
    assert result['threshold_authority'] == 'descriptive_smoke_only'
    assert all(p['abstain'] for p in result['predictions'])
    assert result['triage']['decision_threshold_source'] == 'frozen_policy'
    assert all(p['value'] is False for p in result['triage']['decisions'])
    limited = subprocess.run([*command, '--max-examples', '2'], capture_output=True, text=True)
    assert limited.returncode == 2
    assert 'example limit exceeded' in limited.stderr


@pytest.mark.parametrize('value',[10**1000,-10**1000])
def test_extreme_binary_scores_and_constraints_reject_without_overflow(value):
    with pytest.raises(ValueError,match='invalid probability'):
        bench.evaluate_triage_policy([row('synthetic',True)],[value],policy())
    with pytest.raises(ValueError,match='invalid triage policy constraint'):
        bench.evaluate_triage_policy([row('synthetic',True)],[.9],dict(policy(),threshold=value))
    with pytest.raises(ValueError,match='invalid probability'):
        bench.measure([row('synthetic',True)],[value],.5)


def test_cli_extreme_policy_rejects_without_traceback(tmp_path):
    source,configuration=tmp_path/'examples.jsonl',tmp_path/'policy.json'
    source.write_text('\n'.join(json.dumps(value) for value in [
        dict(row('train-positive',True),split='train'),dict(row('train-negative',False),split='train'),
        row('synthetic',True)])+'\n')
    configuration.write_text(json.dumps(dict(policy(),threshold=10**1000)))
    process=subprocess.run([sys.executable,'-m','tools.benchmark_models',str(source),
        '--method','baseline','--triage-policy',str(configuration)],capture_output=True,text=True)
    assert process.returncode==2
    assert 'invalid triage policy constraint' in process.stderr and 'Traceback' not in process.stderr


def test_binary_all_root_coverage_includes_unlabeled_abstentions():
    rows=[row('positive',True),row('negative',False)]+[row(f'unknown-{n}',None) for n in range(98)]
    scores=[.9,.1]+[.5]*98
    configured=dict(policy(),threshold=.8,min_coverage=.8)
    old=bench.evaluate_triage_policy(rows,scores,configured)
    result=bench.evaluate_triage_policy(rows+[rows[-1]],scores+[scores[-1]],configured,report_version=2)
    assert old['metrics']['coverage']==1 and 'coverage_below_minimum' not in old['reasons']
    assert result['metrics']['coverage']==.02 and result['metrics']['declared_roots']==100
    assert result['metrics']['decided_roots']==2 and result['metrics']['unknown_roots']==98
    assert result['status']=='rejected' and 'coverage_below_minimum' in result['reasons']
    assert result['coverage_scope']=='all_declared_roots' and result['publication_allowed'] is False
    assert result['root_independence']=='unverified' and result['label_authority']=='unverified'
    assert not result['automatic_promotion']


def test_binary_all_root_coverage_never_invents_labels_or_empty_coverage():
    result=bench.evaluate_triage_policy([row('unknown',None)],[.9],policy(),report_version=2)
    assert result['metrics']['coverage']==1 and result['metrics']['precision'] is None
    assert result['metrics']['recall'] is None and result['status']=='insufficient_evidence'
    abstained=bench.evaluate_triage_policy([row('unknown',None)],[.5],dict(policy(),threshold=.8),report_version=2)
    assert abstained['metrics']['coverage']==0 and abstained['status']=='rejected'
    assert 'coverage_below_minimum' in abstained['reasons']
    empty=bench.evaluate_triage_policy([],[],policy(),report_version=2)
    assert empty['metrics']['coverage'] is None and 'coverage_undefined' in empty['reasons']
    for version in (True,0,3):
        with pytest.raises(ValueError,match='report version'):
            bench.evaluate_triage_policy([],[],policy(),report_version=version)


def test_binary_report_version_cli_requires_policy_and_is_private(tmp_path):
    from agent_harness_lab.evaluation.benchmark import public_export_allowed
    from tests.unit.evaluation.test_benchmark_contracts import record

    source,configuration=tmp_path/'examples.jsonl',tmp_path/'policy.json'
    source.write_text('\n'.join(json.dumps(value) for value in [
        dict(row('train-positive',True),split='train'),dict(row('train-negative',False),split='train'),
        row('positive',True),row('negative',False),row('unknown',None)])+'\n')
    configuration.write_text(json.dumps(dict(policy(),threshold=.8)))
    command=[sys.executable,'-m','agent_harness_lab','benchmark',str(source),'--method','baseline']
    missing=subprocess.run([*command,'--triage-report-version','2'],capture_output=True,text=True)
    assert missing.returncode==2 and 'requires --triage-policy' in missing.stderr
    process=subprocess.run([*command,'--triage-policy',str(configuration),'--triage-report-version','2'],capture_output=True,text=True)
    assert process.returncode==0,process.stderr
    result=json.loads(process.stdout)['triage']
    assert result['profile']=='diagnostic-binary-triage-evaluation-v2'
    assert result['metrics']['declared_roots']==3 and result['coverage_scope']=='all_declared_roots'
    assert not public_export_allowed([{**result,**record(),'publication_allowed':True}])
