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
    original=os.open
    def prevent_read(path,flags,*args,**kwargs):
        assert os.fspath(path) not in ('test_feature.py','package.json','Taskfile.yml','lefthook.yml')
        return original(path,flags,*args,**kwargs)
    monkeypatch.setattr(os,'open',prevent_read)
    result=feedback_sensors.capture(tmp_path);rows=result['candidates']
    assert {(row['path'],row['category'],row['stage']) for row in rows} >= {
        ('tests','tests','local'),('tests/test_feature.py','tests','local'),('package.json','task_runner','local'),
        ('Taskfile.yml','task_runner','local'),('lefthook.yml','git_hook','pre_commit')}
    assert all(row['content']=='not_read' and row['command']=='unknown' and row['execution']=='unknown' for row in rows)
    assert 'SYNTHETIC_PRIVATE_CONTENT' not in repr(result)


def test_refused_sensor_link_keeps_coverage_unknown(tmp_path):
    (tmp_path/'package.json').symlink_to(tmp_path/'missing')
    result=feedback_sensors.capture(tmp_path)
    assert result['coverage']=='unknown' and result['candidates']==[]


def test_hosted_ci_paths_are_not_sensor_candidates(tmp_path):
    workflow=tmp_path/'.github'/'workflows';workflow.mkdir(parents=True)
    (workflow/'ci.yml').write_text('SYNTHETIC_CONTENT')
    (workflow/'test_job.py').write_text('SYNTHETIC_CONTENT')
    (workflow/'package.json').write_text('SYNTHETIC_CONTENT')
    (workflow/'tests').mkdir()
    for name in ('.circleci','.buildkite'):
        directory=tmp_path/name;directory.mkdir()
        (directory/'test_job.py').write_text('SYNTHETIC_CONTENT')
        (directory/'package.json').write_text('SYNTHETIC_CONTENT')
        (directory/'tests').mkdir()
    (tmp_path/'.gitlab-ci.yml').write_text('SYNTHETIC_CONTENT')
    assert feedback_sensors.capture(tmp_path)['candidates']==[]


def test_a_git_ignored_directory_is_not_walked_while_coverage_stays_walked(tmp_path):
    # docs/static-audit.md states this omission: the coverage value does not record it, so the documentation must.
    (tmp_path/'.git').mkdir();(tmp_path/'.gitignore').write_text('vendor/\n')
    for name in ('vendor/tests/test_dep.py','vendor/package.json','tests/test_app.py'):
        (tmp_path/name).parent.mkdir(parents=True,exist_ok=True);(tmp_path/name).write_text('')
    result=feedback_sensors.capture(tmp_path)
    assert {row['path'] for row in result['candidates']}=={'tests','tests/test_app.py'}
    assert result['coverage']=='walked_checkout_metadata_only'
