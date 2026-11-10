"""Synthetic boundary observations never claim runtime loading."""
import json

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.audit import cli as audit_cli


@pytest.mark.parametrize(('parent','limit','expected'), [
    (' '*8,1,[('nested/AGENTS.md','instruction_chain_truncated_at_declared_project_doc_bytes',1,1)]),
    ('\u00a0'*4,1,[('AGENTS.md','instruction_chain_truncated_at_declared_project_doc_bytes',1,7),
                    ('nested/AGENTS.md','instruction_chain_skipped_at_declared_project_doc_bytes',0,2)]),
    ('\u00a0'*4,2,[]),
    ('   x',1,[('AGENTS.md','instruction_chain_truncated_at_declared_project_doc_bytes',0,4),
               ('nested/AGENTS.md','instruction_chain_truncated_at_declared_project_doc_bytes',1,1)]),
])
def test_boundary_observation_resolves_only_checked_prefixes(tmp_path,monkeypatch,capsys,parent,limit,expected):
    project=tmp_path/'project';(project/'.git').mkdir(parents=True);(project/'nested').mkdir()
    (project/'AGENTS.md').write_text(parent);(project/'nested/AGENTS.md').write_text('xx')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    args=['audit',str(project),'--json','--task-cwd','nested','--codex-project-doc-max-bytes',str(limit)]
    assert main(args)==0
    original=json.loads(capsys.readouterr().out)
    assert 'codex_budget_boundary' not in original
    assert main(args+['--codex-budget-boundary'])==0
    report=json.loads(capsys.readouterr().out);boundary=report['codex_budget_boundary']
    assert {key:value for key,value in report.items() if key!='codex_budget_boundary'}==original
    rows=[(row['path'],row['reason'],row['measures']['loaded_bytes'],row['measures']['dropped_bytes'])
          for row in boundary['additional_findings']]
    assert rows==expected and boundary['status']=='modeled'
    assert boundary['observed_loading']=='unknown' and boundary['publication_allowed'] is False
    assert 'xx' not in json.dumps(boundary)


def test_changed_resolver_input_never_returns_partial_boundary_results(tmp_path,monkeypatch,capsys):
    project=tmp_path/'project';(project/'.git').mkdir(parents=True);(project/'nested').mkdir()
    (project/'AGENTS.md').write_text(' '*8);(project/'nested/AGENTS.md').write_text('xx')
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    class ChangedFolder(audit_cli.Folder):
        def read(self,name,*args,**kwargs):
            raw=super().read(name,*args,**kwargs)
            return raw+b'changed' if name.endswith('nested/AGENTS.md') and raw is not None else raw
    monkeypatch.setattr(audit_cli,'Folder',ChangedFolder)
    assert main(['audit',str(project),'--json','--task-cwd','nested',
                 '--codex-project-doc-max-bytes','1','--codex-budget-boundary'])==0
    boundary=json.loads(capsys.readouterr().out)['codex_budget_boundary']
    assert boundary['status']=='unknown' and boundary['additional_findings']==[]


def test_unsettled_project_root_does_not_produce_boundary_result(tmp_path,monkeypatch,capsys):
    project=tmp_path/'project';project.mkdir();(project/'AGENTS.md').write_text(' '*8)
    monkeypatch.setenv('XDG_STATE_HOME',str(tmp_path/'state'))
    assert main(['audit',str(project),'--json','--codex-project-doc-max-bytes','1','--codex-budget-boundary'])==0
    boundary=json.loads(capsys.readouterr().out)['codex_budget_boundary']
    assert boundary['status']=='unknown' and boundary['additional_findings']==[]
    assert boundary['evidence_authentication']=='none'
