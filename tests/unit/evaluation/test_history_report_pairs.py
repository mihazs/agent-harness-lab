"""Synthetic pairing is descriptive and never changes backend analysis."""
from copy import deepcopy

from agent_harness_lab.evaluation.history_report import task_pairs
from agent_harness_lab.evaluation.plan import ExperimentSpec, schedule
from agent_harness_lab.evaluation.statistics import TrialRecord


def test_pairing_retains_unknown_and_refuses_runtime_or_grader_mismatch():
    spec=ExperimentSpec('synthetic',(('a','root-a','family'),('b','root-b','family')),1,1,4,2,.05,.1,.05,
                        (('family',1.),),'baseline','candidate','grader','runtime')
    rows=[]
    for planned in schedule(spec):
        passed=planned['version']=='candidate'
        row=dict(planned,status='passed' if passed else 'failed',
                 grader=dict(status='passed' if passed else 'failed',outcome=passed,
                             grader_identity=dict(grader_id='synthetic',grader_version='v1')),
                 execution=dict(status='completed',attestation=dict(runtime_image=dict(
                     sha256='a'*64,verified_before=True,verified_after=True))))
        rows.append(row)
    records=[TrialRecord(row['trial_id'],row['grader']['outcome'],'graded','executable_grader',
                         'baseline','candidate','grader','runtime',False) for row in rows]
    assert [row['status'] for row in task_pairs(spec,dict(trials=rows),records)]==['improved','improved']
    changed=deepcopy(rows); changed[0]['execution']['attestation']['runtime_image']['sha256']='b'*64
    assert 'not comparable' in [row['status'] for row in task_pairs(spec,dict(trials=changed),records)]
    changed=deepcopy(rows); changed[0]['grader']['grader_identity']['grader_version']='different'
    assert 'not comparable' in [row['status'] for row in task_pairs(spec,dict(trials=changed),records)]
    unknown=deepcopy(records); unknown[0]=TrialRecord(rows[0]['trial_id'],None,'graded','executable_grader',
                                                    'baseline','candidate','grader','runtime',False)
    assert 'unknown' in [row['status'] for row in task_pairs(spec,dict(trials=rows),unknown)]
    assert 'unknown' in [row['status'] for row in task_pairs(spec,dict(trials=rows[1:]),records)]
    assert 'unknown' in [row['status'] for row in task_pairs(spec,dict(trials=rows+[rows[0]]),records)]
