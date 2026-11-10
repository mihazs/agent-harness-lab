"""Synthetic, scoped declarations never imply script execution."""
import json
import socket
import subprocess

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.audit import npm_test_observation


@pytest.mark.parametrize(('runtime','source','text','package','mention','declared'), [
    ('codex','AGENTS.md','Run `npm test`.','{"scripts":{"test":"SYNTHETIC_SCRIPT_SECRET"}}','present','declared'),
    ('claude-code','CLAUDE.md','Run npm run test.','{"scripts":{"build":"SYNTHETIC_SCRIPT_SECRET"}}','present','not_declared'),
    ('codex','AGENTS.md','Do not run npm testing.','{"scripts":{}}','not_observed','not_declared'),
    ('codex','AGENTS.md','Never run npm test.','{"scripts":{"test":""}}','present','declared'),
])
def test_root_observations_are_private_and_do_not_execute(
        tmp_path,monkeypatch,capsys,runtime,source,text,package,mention,declared):
    project=tmp_path/'project';project.mkdir();(project/source).write_text(text+'\nSYNTHETIC_INSTRUCTION_SECRET\n')
    (project/'package.json').write_text(package)
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**kw:pytest.fail('process started'))
    monkeypatch.setattr(socket,'socket',lambda *a,**kw:pytest.fail('network used'))
    assert main(['audit',str(project),'--runtime',runtime,'--json'])==0
    assert 'npm_test_observation' not in json.loads(capsys.readouterr().out)
    assert main(['audit',str(project),'--runtime',runtime,'--json','--npm-test-observation'])==0
    output=json.loads(capsys.readouterr().out);row=output['npm_test_observation']
    assert (row['status'],row['instruction_mention'],row['test_script'])==('observed',mention,declared)
    assert row['observed_loading']==row['runtime_execution']=='unknown'
    assert row['publication_allowed'] is False and 'SYNTHETIC_SCRIPT_SECRET' not in json.dumps(output)
    assert 'SYNTHETIC_INSTRUCTION_SECRET' not in json.dumps(output)


@pytest.mark.parametrize('package', ['{', '{"scripts":{"test":"a","test":"b"}}',
                                     '{"scripts":[]}', '{"scripts":{"test":42}}'])
def test_unsettled_package_is_unknown(tmp_path,monkeypatch,capsys,package):
    project=tmp_path/'project';project.mkdir();(project/'AGENTS.md').write_text('Run npm test.\n')
    (project/'package.json').write_text(package);monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    assert main(['audit',str(project),'--json','--npm-test-observation'])==0
    row=json.loads(capsys.readouterr().out)['npm_test_observation']
    assert row['status']==row['instruction_mention']==row['test_script']=='unknown'


def test_changed_source_and_nested_task_stay_unknown(tmp_path,monkeypatch,capsys):
    project=tmp_path/'project';project.mkdir();(project/'AGENTS.md').write_text('Run npm test.\n')
    (project/'package.json').write_text('{"scripts":{}}');(project/'nested').mkdir()
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    assert main(['audit',str(project),'--json','--task-cwd','nested','--npm-test-observation'])==0
    assert json.loads(capsys.readouterr().out)['npm_test_observation']['status']=='unknown'
    original=npm_test_observation.Folder.read
    def changed(self,name,*args,**kwargs):
        raw=original(self,name,*args,**kwargs)
        return raw+b'changed' if name=='AGENTS.md' and raw is not None else raw
    monkeypatch.setattr(npm_test_observation.Folder,'read',changed)
    assert main(['audit',str(project),'--json','--npm-test-observation'])==0
    assert json.loads(capsys.readouterr().out)['npm_test_observation']['status']=='unknown'


@pytest.mark.parametrize('kind',['missing','symlink','oversized'])
def test_unavailable_root_package_stays_unknown(tmp_path,monkeypatch,capsys,kind):
    project=tmp_path/'project';project.mkdir();(project/'AGENTS.md').write_text('Run npm test.\n')
    package=project/'package.json'
    if kind=='symlink': package.symlink_to(tmp_path/'elsewhere')
    if kind=='oversized': package.write_text('x'*(npm_test_observation.MAX_BYTES+1))
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    assert main(['audit',str(project),'--json','--npm-test-observation'])==0
    row=json.loads(capsys.readouterr().out)['npm_test_observation']
    assert row['status']==row['test_script']=='unknown'


def test_changed_package_during_observation_stays_unknown(tmp_path,monkeypatch,capsys):
    project=tmp_path/'project';project.mkdir();(project/'AGENTS.md').write_text('Run npm test.\n')
    package=project/'package.json';package.write_text('{"scripts":{}}')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    class ChangedFolder(npm_test_observation.Folder):
        def verify(self):
            package.write_text('{"scripts":{"test":"changed"}}')
            return super().verify()
    monkeypatch.setattr(npm_test_observation,'Folder',ChangedFolder)
    assert main(['audit',str(project),'--json','--npm-test-observation'])==0
    assert json.loads(capsys.readouterr().out)['npm_test_observation']['status']=='unknown'
