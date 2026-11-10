"""Synthetic action display contains bounded metadata, never payload text."""
from copy import deepcopy

from agent_harness_lab.evaluation.history_report import MAX_ACTION_ROWS, action_timeline, render


def report():
    return dict(schema_version=1,privacy='private',role='executor',trial_id='synthetic-trial',
                trial_id_visibility='observed',scope_id='scope-1',
                coverage=dict(records_complete=True,omitted_records=0,omitted_updates=0),
                actions=[dict(scope_id='scope-1',kind='tool_request',observed_ordinal=2,request_ordinal=1,
                              decision='allowed',worker_outcome='observed',stdout={'text':'SYNTHETIC_SECRET'},
                              argv=['SYNTHETIC_SECRET']),
                         dict(scope_id='scope-1',kind='model_request',observed_ordinal=1,request_ordinal=1)])


def test_scoped_metadata_excludes_raw_content_and_keeps_order():
    timeline=action_timeline(report(),'synthetic-trial')
    assert timeline['coverage']=='recorded_complete'
    assert [row['ordinal'] for row in timeline['events']]==[1,2]
    assert 'SYNTHETIC_SECRET' not in repr(timeline)
    assert action_timeline(report(),'another-trial')['coverage']=='unknown'


def test_omissions_duplicate_order_and_bounded_output_are_visible():
    value=report();value['coverage']['omitted_updates']=1
    assert action_timeline(value,'synthetic-trial')['coverage']=='partial_or_unknown'
    value=report();value['actions'][1]['observed_ordinal']=2
    assert action_timeline(value,'synthetic-trial')['coverage']=='unknown'
    value=report();row=value['actions'][0]
    value['actions']=[dict(row,observed_ordinal=n+1) for n in range(MAX_ACTION_ROWS+1)]
    timeline=action_timeline(value,'synthetic-trial')
    assert timeline['truncated'] and len(timeline['events'])==MAX_ACTION_ROWS
    assert timeline['coverage']=='partial_or_unknown'
    changed=deepcopy(value);changed['actions'][0]['scope_id']='other'
    assert action_timeline(changed,'synthetic-trial')['coverage']=='unknown'


def test_offline_render_shows_projected_metadata_without_secret():
    timeline=action_timeline(report(),'synthetic-trial')
    side=dict(trial_id='synthetic-trial',outcome='passed',timeline=timeline)
    pair=dict(task='synthetic',root='synthetic-root',family='synthetic-family',repetition=0,
              baseline=side,candidate=side,status='unchanged')
    data=dict(report_kind='controller_history',publication_allowed=False,comparisons=[dict(
        receipt_sha256='b'*64,receipt=dict(run_id='a'*64,plan_sha256='c'*64),
        analysis=dict(status='inconclusive'),task_pairs=[pair])])
    markdown,document=render(data)
    assert 'tool_request' in markdown and 'tool_request' in document
    assert 'SYNTHETIC_SECRET' not in markdown and 'SYNTHETIC_SECRET' not in document
    assert 'not authenticated action custody' in document
