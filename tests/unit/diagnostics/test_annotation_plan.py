"""Class-wise annotation uncertainty plan on synthetic counts."""
import pytest

from agent_harness_lab.diagnostics.annotation_plan import half_width, plan, roots_needed

SPEC = dict(schema_version=1, classes={'task_failure': dict(half_width=0.1, min_roots=5), 'rare_outage': dict(half_width=0.1, min_roots=5)})


def test_roots_needed_is_the_smallest_count_meeting_the_target():
    n = roots_needed(0.1)
    assert half_width(n) <= 0.1 < half_width(n - 1)


def test_sparse_class_stays_inconclusive_even_when_the_corpus_is_large():
    report = plan(SPEC, dict(task_failure=dict(precision=500, recall=500), rare_outage=dict(precision=3, recall=0)))
    assert report['status'] == 'inconclusive'
    assert report['classes']['task_failure']['recall']['status'] == 'sufficient'
    rare = report['classes']['rare_outage']
    assert rare['recall'] == dict(roots=0, worst_case_half_width=None, required_roots=roots_needed(0.1), additional_roots=roots_needed(0.1), status='inconclusive')
    assert rare['precision']['additional_roots'] == roots_needed(0.1) - 3


def test_missing_counts_are_unknown_not_zero_evidence_or_success():
    report = plan(SPEC, {})
    assert report['classes']['task_failure']['precision']['roots'] is None
    assert report['status'] == 'inconclusive'


def test_all_classes_meeting_targets_is_sufficient_but_never_authority():
    full = dict(precision=200, recall=200)
    report = plan(SPEC, dict(task_failure=full, rare_outage=full))
    assert report['status'] == 'sufficient'
    assert (report['certification'], report['classifier_selection_allowed'], report['publication_allowed']) == ('not_issued', False, False)


def test_minimum_roots_floor_applies_when_the_interval_target_is_loose():
    report = plan(dict(schema_version=1, classes={'a': dict(half_width=0.5, min_roots=50)}), dict(a=dict(precision=10, recall=10)))
    assert report['classes']['a']['recall']['required_roots'] == 50 and report['classes']['a']['recall']['additional_roots'] == 40


@pytest.mark.parametrize('spec,observed', [
    ({}, {}), (dict(schema_version=1, classes={}), {}),
    (dict(schema_version=1, classes={'a': dict(half_width=0, min_roots=1)}), {}),
    (dict(schema_version=1, classes={'a': dict(half_width=0.1)}), {}),
    (SPEC, dict(unknown=dict(recall=1))), (SPEC, dict(task_failure=dict(recall=-1))),
    (SPEC, dict(task_failure=dict(recall=True))), (SPEC, dict(task_failure=dict(f1=1)))])
def test_invalid_plans_fail_closed(spec, observed):
    with pytest.raises(ValueError):
        plan(spec, observed)


def test_spec_digest_changes_with_the_preregistered_targets():
    other = dict(schema_version=1, classes={**SPEC['classes'], 'task_failure': dict(half_width=0.05, min_roots=5)})
    assert plan(SPEC, {})['spec_sha256'] != plan(other, {})['spec_sha256']


def test_cli_prints_the_plan_and_rejects_invalid_input(tmp_path, capsys):
    import json
    from agent_harness_lab.cli import main
    (tmp_path/'spec.json').write_text(json.dumps(SPEC)); (tmp_path/'seen.json').write_text('{"task_failure":{"recall":9}}')
    assert main(['annotation-plan', str(tmp_path/'spec.json'), str(tmp_path/'seen.json')]) == 0
    assert json.loads(capsys.readouterr().out)['classes']['task_failure']['recall']['roots'] == 9
    (tmp_path/'seen.json').write_text('{"other":{}}')
    assert main(['annotation-plan', str(tmp_path/'spec.json'), str(tmp_path/'seen.json')]) == 2
