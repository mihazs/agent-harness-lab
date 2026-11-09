from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest.mock import Mock, patch

import pytest

from agent_harness_lab.promotion.journal import Journal,file_record
from agent_harness_lab.promotion.monitor import MonitorPolicy,monitor


def write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text)


class JournalProject:
    """A project with one AGENTS.md and a private journal outside it."""

    def __init__(self,root):
        self.root=root; self.project=self.root/'project'; self.project.mkdir()
        (self.project/'AGENTS.md').write_text('before')
        self.journal=Journal(self.project,self.root/'private/journal')

    def prepare(self):
        before={'AGENTS.md':file_record(self.project/'AGENTS.md'),'CLAUDE.md':None}
        target=self.root/'target'; target.write_text('after'); target.chmod(0o640)
        after={'AGENTS.md':file_record(target),'CLAUDE.md':file_record(target)}
        return self.journal.prepare(before,after,{'certification':'synthetic-direct-contract'})


@pytest.fixture
def env(tmp_path):
    return JournalProject(tmp_path)


def test_write_ahead_idempotence_addition_and_guarded_rollback(env):
    record=env.prepare()
    assert (env.project/'AGENTS.md').read_text()=='before'
    assert record['status']=='prepared'
    final=env.journal.execute(record['deployment'])
    assert final['status']=='committed'
    assert (env.project/'CLAUDE.md').read_text()=='after'
    assert (env.project/'AGENTS.md').stat().st_mode & 0o777==0o640
    assert env.journal.execute(record['deployment'])==final
    (env.project/'unrelated').write_text('preserve')
    before_preview={p.name:p.read_bytes() for p in env.project.iterdir()}
    assert env.journal.preview_rollback(record['deployment'])['status']=='rollback_eligible'
    assert before_preview=={p.name:p.read_bytes() for p in env.project.iterdir()}
    rollback=env.journal.rollback(record['deployment'])
    assert (env.project/'AGENTS.md').read_text()=='before'
    assert not (env.project/'CLAUDE.md').exists()
    assert (env.project/'unrelated').read_text()=='preserve'
    assert env.journal.rollback(record['deployment'])['deployment']==rollback['deployment']


def test_later_edit_mode_symlink_and_newer_deployment_block_rollback(env):
    record=env.prepare(); env.journal.execute(record['deployment'])
    path=env.project/'AGENTS.md'; path.write_text('user edit')
    assert env.journal.preview_rollback(record['deployment'])['status']=='conflict'
    with pytest.raises(ValueError): env.journal.rollback(record['deployment'])
    assert path.read_text()=='user edit'
    path.write_text('after'); path.chmod(0o600)
    assert env.journal.preview_rollback(record['deployment'])['status']=='conflict'
    path.chmod(0o640); path.unlink(); path.symlink_to(env.project/'CLAUDE.md')
    assert env.journal.preview_rollback(record['deployment'])['status']=='conflict'


def test_crash_recovery_resumes_only_recognized_partial_transaction(env):
    record=env.prepare()
    import os
    original=os.replace
    failed=[False]
    def interrupt(source,target):
        if Path(target)==env.project/'CLAUDE.md' and not failed[0]:
            failed[0]=True; raise OSError('synthetic crash')
        return original(source,target)
    with patch('agent_harness_lab.promotion.journal.os.replace',side_effect=interrupt):
        with pytest.raises(OSError): env.journal.execute(record['deployment'])
    assert env.journal.load(record['deployment'])['status']=='applying'
    assert (env.project/'AGENTS.md').read_text()=='after'
    env.journal.execute(record['deployment'])
    assert (env.project/'CLAUDE.md').read_text()=='after'
    assert env.journal.active==record['deployment']


def test_transaction_scope_and_changed_baseline_are_rejected(env):
    with pytest.raises(ValueError): env.journal.prepare({'source.py':None},{'source.py':None})
    with pytest.raises(ValueError): env.journal.prepare({'AGENTS.md':None},{'AGENTS.md':None})
    record=env.prepare(); (env.project/'AGENTS.md').write_text('intervening')
    with pytest.raises(ValueError): env.journal.execute(record['deployment'])
    assert (env.project/'AGENTS.md').read_text()=='intervening'


def test_deletion_is_reversed_and_later_deployment_conflicts(env):
    before={'AGENTS.md':file_record(env.project/'AGENTS.md')}
    first=env.journal.prepare(before,{'AGENTS.md':None}); env.journal.execute(first['deployment'])
    assert not (env.project/'AGENTS.md').exists()
    env.journal.rollback(first['deployment'])
    assert (env.project/'AGENTS.md').read_text()=='before'
    assert env.journal.preview_rollback(first['deployment'])['status']=='conflict'


def test_delayed_outcomes_use_task_start_version_and_fixed_window():
    policy=MonitorPolicy('v1',0,10,1)
    events=[dict(event_id='late',task_id='old',deployment='A',task_started=1,outcome=False,
                 provenance='executable_grader',critical=True),
            dict(event_id='current',task_id='new',deployment='B',task_started=2,outcome=True,provenance='executable_grader')]
    versions={'old':'A','new':'B'}
    result=monitor('B',versions,events,policy,20)
    assert result['state']=='stable'; assert result['verified_exposure']==1
    assert not result['automatic_rollback']
    assert monitor('A',versions,events,policy,20)['state']=='paused'
    assert monitor('B',versions,events,policy,5)['state']=='observing'
    assert monitor('B',versions,events+[events[1]],policy,20)['verified_exposure']==1
    assert monitor('B',versions,events+[dict(events[1],outcome=False)],policy,20)['state']=='conflict'
    with pytest.raises(ValueError): monitor('B',versions,events,replace(policy,end=0),20)


def test_privileged_modes_and_conflicting_task_outcomes_are_rejected(tmp_path):
    path=tmp_path/'AGENTS.md'; path.write_text('fixture'); path.chmod(0o4644)
    with pytest.raises(ValueError): file_record(path)
    policy=MonitorPolicy('v1',0,10,1)
    event=dict(event_id='one',task_id='t',deployment='A',task_started=1,outcome=True,provenance='executable_grader')
    result=monitor('A',{'t':'A'},[event,dict(event,event_id='two',outcome=False)],policy,20)
    assert result['state']=='conflict'


def test_forged_privileged_snapshot_is_rejected_before_mutation(tmp_path):
    root=tmp_path; (root/'project').mkdir(); path=root/'project/AGENTS.md'; path.write_text('before')
    journal=Journal(root/'project',root/'private')
    before=file_record(path); after=dict(before,mode=0o4644)
    with pytest.raises(ValueError): journal.prepare({'AGENTS.md':before},{'AGENTS.md':after})
    assert path.read_text()=='before'
    with pytest.raises(ValueError): Journal(root/'project',root/'project/private')


def test_adopt_snapshot_race_and_manual_cli_rollback(tmp_path):
    from agent_harness_lab.workflow import adopt,snapshot,candidate_states
    from agent_harness_lab.cli import main
    import agent_harness_lab.promotion.journal as module
    root=tmp_path; project=root/'project'; project.mkdir(); (project/'AGENTS.md').write_text('before')
    original=snapshot(project,root/'baseline'); shutil.copytree(root/'baseline',root/'candidate')
    (root/'candidate/AGENTS.md').write_text('after'); proposed=candidate_states(root/'candidate',original)
    original_reader=module.file_record
    def mutate_then_read(path):
        if path==root/'candidate/AGENTS.md': path.write_text('unevaluated')
        return original_reader(path)
    with patch('agent_harness_lab.workflow.state_root',return_value=root/'state'):
        with patch.object(module,'file_record',side_effect=mutate_then_read):
            with pytest.raises(ValueError,match='while journaling'):
                adopt(project,root/'baseline',root/'candidate',original,proposed)
        assert (project/'AGENTS.md').read_text()=='before'
        (root/'candidate/AGENTS.md').write_text('after')
        assert adopt(project,root/'baseline',root/'candidate',original,proposed)==['AGENTS.md']
        active=next((root/'state/promotions').glob('*/active.json'))
        deployment=json.loads(active.read_text())['deployment']
        with patch('builtins.print'):
            assert main(['promotion',str(project),'--deployment',deployment])==0
            assert (project/'AGENTS.md').read_text()=='after'
            assert main(['promotion',str(project),'--deployment',deployment,'--action','rollback'])==0
        assert (project/'AGENTS.md').read_text()=='before'


# Nested instructions and rules adopt through the one journal; every other surface is still refused.
NAMES=('AGENTS.md','app/CLAUDE.md','.claude/rules/x.md')


class NestedProject:
    """A project whose root, nested and rule instructions all read 'before'."""

    def __init__(self,root):
        self.root=root; self.project=self.root/'project'
        for name in NAMES: write(self.project/name,'before')

    def stage(self):
        from agent_harness_lab.workflow import snapshot,candidate_states
        original=snapshot(self.project,self.root/'baseline',allowed_paths=list(NAMES))
        shutil.copytree(self.root/'baseline',self.root/'candidate')
        for name in NAMES[1:]: (self.root/'candidate'/name).write_text('after')
        return original,candidate_states(self.root/'candidate',original)

    def adopt(self,original,proposed):
        from agent_harness_lab.workflow import adopt
        return adopt(self.project,self.root/'baseline',self.root/'candidate',original,proposed)

    def stage_files(self,files,modes={}):
        """Stage candidate bytes over a baseline of NAMES; modes sets st_mode bits on staged files."""
        from agent_harness_lab.workflow import snapshot,candidate_states
        original=snapshot(self.project,self.root/'baseline',allowed_paths=list(NAMES))
        shutil.copytree(self.root/'baseline',self.root/'candidate')
        for name,text in files.items():
            write(self.root/'candidate'/name,text); (self.root/'candidate'/name).chmod(modes.get(name,0o644))
        return original,lambda:candidate_states(self.root/'candidate',original)


@pytest.fixture
def nested(tmp_path,monkeypatch):
    value=NestedProject(tmp_path)
    monkeypatch.setattr('agent_harness_lab.workflow.state_root',Mock(return_value=tmp_path/'state'))
    # Hermetic: a git work tree enclosing the host's TMPDIR must not lend this project its ignore rules.
    monkeypatch.setattr('agent_harness_lab.promotion.journal.ignored',Mock(return_value=False))
    return value


def test_nested_instruction_and_rule_adopt_and_roll_back(nested):
    assert nested.adopt(*nested.stage())==['.claude/rules/x.md','app/CLAUDE.md']
    assert [(nested.project/n).read_text() for n in NAMES]==['before','after','after']
    journal=Journal(nested.project,next((nested.root/'state/promotions').iterdir()))
    assert journal.load(journal.active)['metadata']=={'source':'directly_authorized_adopt_api'}
    journal.rollback(journal.active)
    assert [(nested.project/n).read_text() for n in NAMES]==['before']*3


def test_stale_baseline_or_concurrent_edit_invalidates_adoption(nested):
    original,proposed=nested.stage()
    (nested.project/'app/CLAUDE.md').write_text('user edit')
    with pytest.raises(ValueError,match='changed before adoption'): nested.adopt(original,proposed)
    assert (nested.project/'.claude/rules/x.md').read_text()=='before'
    journal=Journal(nested.project,nested.root/'journal'); target=nested.root/'target'; target.write_text('after')
    name='.claude/rules/x.md'
    record=journal.prepare({name:file_record(nested.project/name)},{name:file_record(target)})
    (nested.project/name).write_text('concurrent')
    with pytest.raises(ValueError,match='intervening'): journal.execute(record['deployment'])
    assert [(nested.project/n).read_text() for n in NAMES[1:]]==['user edit','concurrent']


REFUSED_SURFACES=('CLAUDE.local.md','.claude/settings.json','.claude/hooks/x.sh','.claude/workflows/x.js','.mcp.json',
                  '.codex/config.toml','.codex/rules/default.rules','.claude/rules/linked.md',
                  'plugins/demo/.claude/rules/x.md','plugins/demo/CLAUDE.md')


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in REFUSED_SURFACES])
def test_settings_hooks_workflows_policy_links_and_plugin_checkouts_stay_refused(nested,name):
    from agent_harness_lab.surfaces import classify
    from agent_harness_lab.workflow import candidate_states
    outside=nested.root/'outside.md'; outside.write_text('foreign')
    (nested.project/'.claude/rules/linked.md').symlink_to(outside)
    write(nested.project/'plugins/demo/.git/HEAD','ref: refs/heads/main')  # a nested plugin checkout
    target=nested.root/'target'; target.write_text('after')
    journal=Journal(nested.project,nested.root/'journals'/name.replace('/','_'))
    with pytest.raises(ValueError): journal.prepare({name:None},{name:file_record(target)})
    assert not list(journal.folder.iterdir())
    if name.startswith('plugins/'):
        # A staged copy holds no .git, so only the journal can see the nested checkout.
        assert classify(name,origin='external_repository',in_checkout=True).writability=='recommendation_only'
    else:
        folder=Path(tempfile.mkdtemp(dir=nested.root))
        if name.endswith('linked.md'): write(folder/'.claude/rules/real.md','x'); (folder/name).symlink_to(outside)
        else: write(folder/name,'candidate')
        with pytest.raises(ValueError,match='outside project harness scope|symlink'): candidate_states(folder,{})
    assert not (nested.project/'plugins/demo/CLAUDE.md').exists()


def test_same_named_files_in_unrelated_scopes_never_become_writable_by_discovery(nested):
    from agent_harness_lab.workflow import harness_files,snapshot,candidate_states
    # A nested repository under a project folder name; a vendor/ copy is refused lexically before staging.
    for name in ('nested/.git/HEAD','nested/AGENTS.md','nested/.claude/rules/x.md','plugin/skills/demo/SKILL.md',
                 'my-skills/demo/CLAUDE.local.md'):
        write(nested.project/name,'foreign')
    assert [str(p.relative_to(nested.project)) for p in harness_files(nested.project)]==['AGENTS.md']
    # Even an explicit selection of a nested repository's file stages but never adopts.
    names=['nested/AGENTS.md','nested/.claude/rules/x.md']
    original=snapshot(nested.project,nested.root/'baseline',allowed_paths=names)
    shutil.copytree(nested.root/'baseline',nested.root/'candidate')
    for name in names: (nested.root/'candidate'/name).write_text('after')
    with pytest.raises(ValueError,match='outside owned harness surface'):
        nested.adopt(original,candidate_states(nested.root/'candidate',original))
    assert [(nested.project/n).read_text() for n in names]==['foreign','foreign']


@pytest.mark.parametrize('name',[pytest.param(name,id=name)
                                 for name in ('.claude/rules/x.md','pkg/.claude/skills/demo/scripts/deploy','app/CLAUDE.md')])
def test_executable_files_on_gated_rows_are_never_staged_or_journaled(nested,name):
    script='#!/bin/sh\necho synthetic\n'
    _,states=nested.stage_files({name:script},{name:0o755})
    with pytest.raises(ValueError,match='needs a hook contract'): states()
    journal=Journal(nested.project,nested.root/'journals'/name.replace('/','_'))
    before=file_record(nested.project/name) if (nested.project/name).exists() else None
    with pytest.raises(ValueError,match='outside owned harness surface'):
        journal.prepare({name:before},{name:file_record(nested.root/'candidate'/name)})
    assert not (nested.project/'pkg').exists()
    assert [(nested.project/n).stat().st_mode&0o777 for n in NAMES]==[0o644]*3


def test_an_unchanged_executable_file_is_never_written_so_staging_accepts_it(nested):
    (nested.project/'.claude/rules/x.md').chmod(0o755)
    original,states=nested.stage_files({'app/CLAUDE.md':'after'})
    assert states()['.claude/rules/x.md']==original['.claude/rules/x.md']


HOOKED_AGENTS={'.claude/agents/r.md':'---\nname: r\ndescription: d\nhooks:\n  PreToolUse:\n    - hooks:\n'
               '        - type: command\n          command: synthetic-hook\n---\nBody\n',
               '.claude/agents/q.md':'---\n{name: q, description: d, "\\x68ooks": {}}\n---\n',
               '.codex/agents/r.toml':'name = "r"\ndeveloper_instructions = "x"\n[[hooks.PreToolUse]]\ncommand = "x"\n',
               '.codex/agents/bad.toml':'name = = "broken"\n'}


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in HOOKED_AGENTS])
def test_agent_definitions_that_register_hooks_are_never_adopted(nested,name):
    _,states=nested.stage_files({name:HOOKED_AGENTS[name]})
    with pytest.raises(ValueError,match='needs a hook contract'): states()
    journal=Journal(nested.project,nested.root/'journals'/name.replace('/','_'))
    with pytest.raises(ValueError,match='outside owned harness surface'):
        journal.prepare({name:None},{name:file_record(nested.root/'candidate'/name)})
    assert not (nested.project/'.claude/agents').exists(); assert not (nested.project/'.codex').exists()


def test_plain_agent_definitions_adopt(nested):
    plain={'.claude/agents/ok.md':'---\nname: ok\ndescription: reviews synthetic diffs\n---\nRead only.\n',
           '.codex/agents/ok.toml':'name = "ok"\ndescription = "d"\ndeveloper_instructions = "Review synthetic diffs."\n'}
    original,states=nested.stage_files(plain)
    assert nested.adopt(original,states())==sorted(plain)


HOOK='hooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n          command: ./synthetic.sh\n'
GRANTED={'.claude/commands/deploy.md':'---\ndescription: x\n'+HOOK+'---\nBody\n',
         'pkg/.claude/skills/s/SKILL.md':'---\nname: s\ndescription: d\n'+HOOK+'---\nBody\n',
         '.claude/skills/s/SKILL.md':'---\nname: s\ndescription: d\n'+HOOK+'---\nBody\n',
         '.claude/rules/r.md':'---\npaths: ["*.py"]\n'+HOOK+'---\nBody\n',
         '.claude/commands/run.md':'---\ndescription: d\nallowed-tools: Bash(*)\n---\nBody\n',
         '.claude/agents/p.md':'---\nname: p\ndescription: d\npermissionMode: bypassPermissions\n---\nBody\n',
         '.claude/agents/m.md':'---\nname: m\ndescription: d\nmcpServers:\n  synthetic:\n'
         '    command: /bin/sh\n    args: ["-c", "true"]\n---\nBody\n'}


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in GRANTED])
def test_frontmatter_that_registers_hooks_or_grants_execution_is_never_adopted(nested,name):
    _,states=nested.stage_files({name:GRANTED[name]})
    with pytest.raises(ValueError,match='needs a hook contract'): states()
    journal=Journal(nested.project,nested.root/'journals'/name.replace('/','_'))
    with pytest.raises(ValueError,match='outside owned harness surface'):
        journal.prepare({name:None},{name:file_record(nested.root/'candidate'/name)})
    assert not (nested.project/'pkg').exists(); assert not (nested.project/'.claude/commands').exists()


def test_plain_frontmatter_adopts(nested):
    plain={'.claude/commands/ok.md':'---\ndescription: summarize synthetic diffs\n---\nBody\n',
           'pkg/.claude/skills/ok/SKILL.md':'---\nname: ok\ndescription: d\n---\nBody\n'}
    original,states=nested.stage_files(plain)
    assert nested.adopt(original,states())==sorted(plain)


def test_rolling_back_a_rollback_probes_containment_like_a_promotion(nested):
    original,proposed=nested.stage_files({'app/CLAUDE.md':'after'})
    assert nested.adopt(original,proposed())==['app/CLAUDE.md']
    (nested.project/'app/.git').mkdir()
    journal=Journal(nested.project,next((nested.root/'state/promotions').iterdir()))
    rollback=journal.rollback(journal.active)
    assert (nested.project/'app/CLAUDE.md').read_text()=='before'
    # Reverting the rollback re-applies the promotion's content into what is now a nested repository.
    with pytest.raises(ValueError,match='outside owned harness surface'): journal.rollback(rollback['deployment'])
    assert (nested.project/'app/CLAUDE.md').read_text()=='before'
    # An external caller cannot claim the rollback exemption either.
    write(nested.project/'vendor/AGENTS.md','before'); (nested.project/'vendor/.git').mkdir()
    target=nested.root/'target'; target.write_text('after')
    with pytest.raises(ValueError,match='outside owned harness surface'):
        journal.prepare({'vendor/AGENTS.md':file_record(nested.project/'vendor/AGENTS.md')},
                        {'vendor/AGENTS.md':file_record(target)},None,'rollback')
    assert (nested.project/'vendor/AGENTS.md').read_text()=='before'


HOOKED_AGENT='---\nname: a\ndescription: d\nhooks:\n  Stop: []\n---\nBody\n'
RESTORING_ROLLBACKS=[
    pytest.param('app/CLAUDE.md','after',lambda project:(project/'app/.git').mkdir(),'before',id='app/CLAUDE.md'),
    pytest.param('.claude/agents/a.md','---\nname: a\ndescription: d\n---\nBody\n',lambda project:None,HOOKED_AGENT,
                 id='.claude/agents/a.md'),
]


@pytest.mark.parametrize('name,after,later,before',RESTORING_ROLLBACKS)
def test_every_rollback_that_restores_original_content_survives_nesting_and_hooks(nested,name,after,later,before):
    write(nested.project/'.claude/agents/a.md',HOOKED_AGENT)
    journal=Journal(nested.project,nested.root/'journals'/name.replace('/','_'))
    target=nested.root/'target'; target.write_text(after)
    promotion=journal.prepare({name:file_record(nested.project/name)},{name:file_record(target)})
    journal.execute(promotion['deployment'])
    first=journal.rollback(promotion['deployment'])
    second=journal.rollback(first['deployment'])
    assert (nested.project/name).read_text()==after
    later(nested.project)
    # The third rollback restores the original content again, so it is never stranded or content-checked.
    third=journal.rollback(second['deployment'])
    assert (nested.project/name).read_text()==before
    # Reverting it would re-apply the promotion's content, which is admitted (and refused) again.
    if name=='app/CLAUDE.md':
        with pytest.raises(ValueError,match='outside owned harness surface'):
            journal.rollback(third['deployment'])
        assert (nested.project/name).read_text()==before


def test_rollback_survives_a_repository_created_below_the_project_after_adoption(nested):
    original,proposed=nested.stage_files({'AGENTS.md':'after','app/CLAUDE.md':'after'})
    assert nested.adopt(original,proposed())==['AGENTS.md','app/CLAUDE.md']
    (nested.project/'app/.git').mkdir()  # a later git init, submodule or worktree
    journal=Journal(nested.project,next((nested.root/'state/promotions').iterdir()))
    assert journal.preview_rollback(journal.active)['status']=='rollback_eligible'
    journal.rollback(journal.active)
    assert [(nested.project/n).read_text() for n in ('AGENTS.md','app/CLAUDE.md')]==['before','before']
    # A new promotion still probes containment, so the nested repository's file is no longer adoptable.
    target=nested.root/'target'; target.write_text('again')
    with pytest.raises(ValueError,match='outside owned harness surface'):
        journal.prepare({'app/CLAUDE.md':file_record(nested.project/'app/CLAUDE.md')},{'app/CLAUDE.md':file_record(target)})


# node_modules, .venv and gitignored folders hold no .git of their own, yet the journal never owns them.
VENDORED=('node_modules/pkg/CLAUDE.md','node_modules/pkg/.claude/rules/x.md','.venv/lib/AGENTS.md',
          '.venv/.claude/rules/x.md','generated/CLAUDE.md','generated/.claude/rules/x.md',
          'generated/.claude/skills/demo/SKILL.md')


class VendoredProject:
    """A Git project that ignores generated/ and holds vendored copies of harness files."""

    def __init__(self,root):
        self.root=root; self.project=self.root/'project'; self.project.mkdir()
        subprocess.run(['git','init','-q'],cwd=self.project,check=True)
        (self.project/'.gitignore').write_text('generated/\n')
        for name in (*VENDORED,'app/CLAUDE.md','.claude/rules/x.md'):
            write(self.project/name,'before')
        self.target=self.root/'target'; self.target.write_text('after')

    def prepare(self,name):
        journal=Journal(self.project,self.root/'journals'/name.replace('/','_'))
        return journal,lambda:journal.prepare({name:file_record(self.project/name)},{name:file_record(self.target)})

    def git(self,*args):
        subprocess.run(['git',*args],cwd=self.project,check=True,stdout=subprocess.DEVNULL)


@pytest.fixture
def vendored(tmp_path):
    if shutil.which('git') is None: pytest.skip('git is required for the ignore-rule probe')
    return VendoredProject(tmp_path)


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in VENDORED])
def test_vendored_and_gitignored_trees_are_never_journaled(vendored,name):
    journal,prepare=vendored.prepare(name)
    with pytest.raises(ValueError,match='outside owned harness surface'): prepare()
    assert not list(journal.folder.iterdir())
    assert (vendored.project/name).read_text()=='before'


# Project folders in the same repository still adopt: the probe refuses only what the ignore rules exclude.
@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in ('app/CLAUDE.md','.claude/rules/x.md')])
def test_project_folders_beside_vendored_trees_still_adopt(vendored,name):
    journal,prepare=vendored.prepare(name)
    assert journal.execute(prepare()['deployment'])['status']=='committed'
    assert (vendored.project/name).read_text()=='after'


def test_explicitly_selected_vendored_files_stage_but_never_adopt(vendored):
    from agent_harness_lab.workflow import adopt,snapshot,candidate_states
    names=['generated/CLAUDE.md','generated/.claude/rules/x.md']
    original=snapshot(vendored.project,vendored.root/'baseline',allowed_paths=names)
    shutil.copytree(vendored.root/'baseline',vendored.root/'candidate')
    for name in names: (vendored.root/'candidate'/name).write_text('after')
    with patch('agent_harness_lab.workflow.state_root',return_value=vendored.root/'state'):
        with pytest.raises(ValueError,match='outside owned harness surface'):
            adopt(vendored.project,vendored.root/'baseline',vendored.root/'candidate',original,
                  candidate_states(vendored.root/'candidate',original))
    assert [(vendored.project/n).read_text() for n in names]==['before','before']


def test_ignore_probe_is_read_only_and_fails_closed(vendored):
    from agent_harness_lab.promotion import journal as module
    assert module.ignored(vendored.project,'generated/.claude/rules/x.md')
    assert not module.ignored(vendored.project,'app/CLAUDE.md')
    assert not module.ignored(vendored.project,'AGENTS.md')  # a root file has no enclosing folder to ignore
    assert not (vendored.project/'.git/index').exists()  # --no-index never opens or creates the index
    with patch.object(module.subprocess,'run',return_value=subprocess.CompletedProcess([],128)):
        assert module.ignored(vendored.project,'app/CLAUDE.md')
    # An inherited GIT_DIR (as inside a git hook) never redirects the probe to another repository.
    other=vendored.root/'other'; other.mkdir(); subprocess.run(['git','init','-q'],cwd=other,check=True)
    (other/'.git/info').mkdir(exist_ok=True); (other/'.git/info/exclude').write_text('app/\n')
    with patch.dict(module.os.environ,{'GIT_DIR':str(other/'.git')}):
        assert not module.ignored(vendored.project,'app/CLAUDE.md')
        assert module.ignored(vendored.project,'generated/CLAUDE.md')
    # A project outside any repository has no ignore rules, so git is never started for it. The host's own
    # ancestors are hidden, so a git work tree enclosing TMPDIR cannot decide this control.
    plain=vendored.root/'plain'; plain.mkdir(); lexists=module.os.path.lexists
    with patch.object(module.os.path,'lexists',lambda path:Path(path).is_relative_to(vendored.root) and lexists(path)), \
         patch.object(module.subprocess,'run',side_effect=AssertionError('git must not run')):
        assert not module.ignored(plain,'app/CLAUDE.md')


@pytest.mark.parametrize('failure',[pytest.param(OSError('no git'),id='OSError'),
                                    pytest.param(subprocess.TimeoutExpired('git',10),id='TimeoutExpired')])
def test_ignore_probe_fails_closed_when_git_cannot_answer(vendored,failure):
    from agent_harness_lab.promotion import journal as module
    with patch.object(module.subprocess,'run',side_effect=failure):
        assert module.ignored(vendored.project,'app/CLAUDE.md')


@pytest.mark.parametrize('name',[pytest.param(name,id=name)
                                 for name in ('.claude/rules/x.md','.claude/skills/demo/SKILL.md','generated/CLAUDE.md')])
def test_ignored_root_harness_folders_and_tracked_files_still_adopt(vendored,name):
    # Keeping .claude/ local through ignore rules does not make the root harness folders foreign.
    (vendored.project/'.gitignore').write_text('generated/\n.claude/\n')
    write(vendored.project/'.claude/skills/demo/SKILL.md','before')
    # A force-added file is tracked, so the ignore rules no longer apply to it.
    vendored.git('add','-f','--','generated/CLAUDE.md')
    journal,prepare=vendored.prepare(name)
    assert journal.execute(prepare()['deployment'])['status']=='committed'
    assert (vendored.project/name).read_text()=='after'


def ignore_instruction_file_names(vendored):
    # Keeping instruction files local with a file pattern leaves nested and root files equally adoptable.
    (vendored.project/'.gitignore').write_text('generated/\nCLAUDE.md\nAGENTS.md\n')
    for name in ('CLAUDE.md','app/AGENTS.md'): write(vendored.project/name,'before')


@pytest.mark.parametrize('name',[pytest.param(name,id=name) for name in ('CLAUDE.md','app/CLAUDE.md','app/AGENTS.md')])
def test_file_name_ignore_patterns_never_make_a_folder_foreign(vendored,name):
    ignore_instruction_file_names(vendored)
    journal,prepare=vendored.prepare(name)
    assert journal.execute(prepare()['deployment'])['status']=='committed'
    assert (vendored.project/name).read_text()=='after'


def test_a_directory_rule_still_refuses_ignored_file_names_inside_an_ignored_folder(vendored):
    ignore_instruction_file_names(vendored)
    with pytest.raises(ValueError,match='outside owned harness surface'): vendored.prepare('generated/CLAUDE.md')[1]()


def test_a_personal_global_excludes_file_never_decides_ownership(vendored):
    home=vendored.root/'home'; home.mkdir(); (home/'ignore').write_text('app/\n')
    (home/'.gitconfig').write_text(f'[core]\n\texcludesFile = {home/"ignore"}\n')
    with patch.dict('os.environ',{'HOME':str(home),'XDG_CONFIG_HOME':str(home/'xdg')}):
        journal,prepare=vendored.prepare('app/CLAUDE.md')
        assert journal.execute(prepare()['deployment'])['status']=='committed'
    assert (vendored.project/'app/CLAUDE.md').read_text()=='after'


def test_rollback_survives_ignore_rules_written_after_adoption(vendored):
    journal,prepare=vendored.prepare('app/CLAUDE.md')
    deployment=prepare()['deployment']
    assert journal.execute(deployment)['status']=='committed'
    (vendored.project/'.gitignore').write_text('generated/\napp/\n')
    journal=Journal(vendored.project,journal.folder)
    assert journal.load(deployment)['status']=='committed'
    assert journal.preview_rollback(deployment)['status']=='rollback_eligible'
    journal.rollback(deployment)
    assert (vendored.project/'app/CLAUDE.md').read_text()=='before'
    # A new promotion probes the ignore rules again, so the now-ignored file is no longer adoptable.
    with pytest.raises(ValueError,match='outside owned harness surface'): vendored.prepare('app/CLAUDE.md')[1]()


# key_edit rewrites only the hooks key; every other member keeps its bytes or the edit is refused.
HOOKS_VALUE={'Stop':[{'hooks':[{'type':'command','command':'./.claude/hooks/x.sh','timeout':5}]}]}


@pytest.mark.parametrize('raw',[
    pytest.param(b'{"permissions":{"allow":["Bash(ls)"]},"hooks":{},"env":{"A":"1"}}',id='compact-middle'),
    pytest.param(b'{\n    "hooks": {"Stop": []},\n    "env": {"A": "1"}\n}\n',id='indented-first'),
    pytest.param(b'{\n  "env": {"A": "1"},\n  "hooks": {}\n}\n',id='indented-last'),
    pytest.param(b'{"hooks": {}}',id='only-hooks'),pytest.param(b'{}',id='empty-object'),
    pytest.param(b'  ',id='whitespace'),pytest.param(None,id='missing')])
def test_json_members_keep_their_bytes_in_every_position(raw):
    from agent_harness_lab.promotion.journal import key_edit
    updated=key_edit(raw,'json',HOOKS_VALUE)
    assert json.loads(updated)['hooks']==HOOKS_VALUE
    for key in (b'"permissions":{"allow":["Bash(ls)"]}',b'"env": {"A": "1"}',b'"env":{"A":"1"}'):
        if raw and key in raw: assert key in updated
    removed=key_edit(updated,'json',None)
    assert 'hooks' not in json.loads(removed)
    if raw and b'"hooks"' not in raw and raw.strip(): assert removed==raw


def test_toml_keeps_other_tables_comments_and_trust_state():
    from agent_harness_lab.promotion.journal import key_edit
    import tomllib
    raw=(b'model = "m"\n[hooks]\n[[hooks.Stop]]\n# inner note\nmatcher = "x"\n\n# next table\n[mcp_servers.a]\n'
         b'command = "srv"\n[hooks.state."k 1"]\ntrusted_hash = "h"\n')
    updated=key_edit(raw,'toml',HOOKS_VALUE)
    document=tomllib.loads(updated.decode())
    assert {k:v for k,v in document['hooks'].items() if k!='state'}==HOOKS_VALUE
    for kept in (b'model = "m"\n',b'# next table\n[mcp_servers.a]\ncommand = "srv"\n',b'[hooks.state."k 1"]\ntrusted_hash = "h"\n'):
        assert kept in updated
    assert b'inner note' not in updated
    assert tomllib.loads(key_edit(updated,'toml',None).decode())['hooks']=={'state':{'k 1':{'trusted_hash':'h'}}}


@pytest.mark.parametrize('raw,form',[
    pytest.param(b'{"hooks": {}, "hooks": {}}','json',id='json-duplicate-hooks'),
    pytest.param(b'[1]','json',id='json-array'),
    pytest.param(b'{"a": 1} trailing','json',id='json-trailing'),
    pytest.param(b'hooks.Stop = []\n','toml',id='toml-dotted-key'),
    pytest.param(b'hooks = {}\n','toml',id='toml-inline-table'),
    pytest.param(b'"hooks".Stop = []\n','toml',id='toml-quoted-dotted-key'),
    pytest.param(b'[profiles]\nx = """\n[hooks]\n"""\n','toml',id='toml-header-in-string'),
    pytest.param(b'x = = 1\n','toml',id='toml-invalid')])
def test_ambiguous_or_unsupported_layouts_fail_closed(raw,form):
    from agent_harness_lab.promotion.journal import key_edit
    with pytest.raises(ValueError): key_edit(raw,form,HOOKS_VALUE)


def test_unsupported_values_and_formats_fail_closed():
    from agent_harness_lab.promotion.journal import key_edit
    with pytest.raises(ValueError): key_edit(b'{}','json',[])
    with pytest.raises(ValueError): key_edit(b'','toml',{'Stop':[{'hooks':[{'timeout':1.5}]}]})
    with pytest.raises(ValueError): key_edit(b'{}','yaml',HOOKS_VALUE)
