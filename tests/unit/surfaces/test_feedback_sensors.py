"""Synthetic filename-only sensor candidates; no command execution or contents."""
import os

from agent_harness_lab.audit import feedback_sensors


def test_empty_checkout_keeps_consistency_unknown(tmp_path):
    result=feedback_sensors.capture(tmp_path)
    assert result['candidates']==[] and result['status']=='completed'
    assert result['consistency']=='not_checked' and result['runtime_execution']=='unknown'
    assert not result['publication_allowed']


def test_filename_candidates_never_open_content(tmp_path,monkeypatch):
    (tmp_path/'tests').mkdir();(tmp_path/'tests'/'test_feature.py').write_text('SYNTHETIC_PRIVATE_CONTENT')
    (tmp_path/'package.json').write_text('SYNTHETIC_PRIVATE_CONTENT')
    (tmp_path/'Taskfile.yml').write_text('SYNTHETIC_PRIVATE_CONTENT')
    (tmp_path/'lefthook.yml').write_text('SYNTHETIC_PRIVATE_CONTENT')
    workflow=tmp_path/'.github'/'workflows';workflow.mkdir(parents=True)
    (workflow/'ci.yml').write_text('SYNTHETIC_PRIVATE_CONTENT')
    original=os.open
    def prevent_read(path,flags,*args,**kwargs):
        assert os.fspath(path) not in ('test_feature.py','package.json','Taskfile.yml','lefthook.yml','ci.yml')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',prevent_read)
    result=feedback_sensors.capture(tmp_path);rows=result['candidates']
    assert {(row['path'],row['category'],row['stage']) for row in rows} >= {
        ('tests','tests','local'),('tests/test_feature.py','tests','local'),('package.json','task_runner','local'),
        ('Taskfile.yml','task_runner','local'),('lefthook.yml','git_hook','pre_commit'),('.github/workflows/ci.yml','ci','ci')}
    assert all(row['content']=='not_read' and row['command']=='unknown' and row['execution']=='unknown' for row in rows)
    assert 'SYNTHETIC_PRIVATE_CONTENT' not in repr(result)


def test_refused_sensor_link_keeps_coverage_unknown(tmp_path):
    (tmp_path/'package.json').symlink_to(tmp_path/'missing')
    result=feedback_sensors.capture(tmp_path)
    assert result['coverage']=='unknown' and result['candidates']==[]
