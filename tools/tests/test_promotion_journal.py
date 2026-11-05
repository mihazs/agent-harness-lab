from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.promotion.journal import Journal,file_record
from agent_harness_lab.promotion.monitor import MonitorPolicy,monitor


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.project=self.root/'project'; self.project.mkdir()
        (self.project/'AGENTS.md').write_text('before')
        self.journal=Journal(self.project,self.root/'private/journal')

    def prepare(self):
        before={'AGENTS.md':file_record(self.project/'AGENTS.md'),'CLAUDE.md':None}
        target=self.root/'target'; target.write_text('after'); target.chmod(0o640)
        after={'AGENTS.md':file_record(target),'CLAUDE.md':file_record(target)}
        return self.journal.prepare(before,after,{'certification':'synthetic-direct-contract'})

    def test_write_ahead_idempotence_addition_and_guarded_rollback(self):
        record=self.prepare()
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertEqual(record['status'],'prepared')
        final=self.journal.execute(record['deployment'])
        self.assertEqual(final['status'],'committed')
        self.assertEqual((self.project/'CLAUDE.md').read_text(),'after')
        self.assertEqual((self.project/'AGENTS.md').stat().st_mode & 0o777,0o640)
        self.assertEqual(self.journal.execute(record['deployment']),final)
        (self.project/'unrelated').write_text('preserve')
        before_preview={p.name:p.read_bytes() for p in self.project.iterdir()}
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'rollback_eligible')
        self.assertEqual(before_preview,{p.name:p.read_bytes() for p in self.project.iterdir()})
        rollback=self.journal.rollback(record['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertFalse((self.project/'CLAUDE.md').exists())
        self.assertEqual((self.project/'unrelated').read_text(),'preserve')
        self.assertEqual(self.journal.rollback(record['deployment'])['deployment'],rollback['deployment'])

    def test_later_edit_mode_symlink_and_newer_deployment_block_rollback(self):
        record=self.prepare(); self.journal.execute(record['deployment'])
        path=self.project/'AGENTS.md'; path.write_text('user edit')
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')
        with self.assertRaises(ValueError): self.journal.rollback(record['deployment'])
        self.assertEqual(path.read_text(),'user edit')
        path.write_text('after'); path.chmod(0o600)
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')
        path.chmod(0o640); path.unlink(); path.symlink_to(self.project/'CLAUDE.md')
        self.assertEqual(self.journal.preview_rollback(record['deployment'])['status'],'conflict')

    def test_crash_recovery_resumes_only_recognized_partial_transaction(self):
        record=self.prepare()
        import os
        original=os.replace
        failed=[False]
        def interrupt(source,target):
            if Path(target)==self.project/'CLAUDE.md' and not failed[0]:
                failed[0]=True; raise OSError('synthetic crash')
            return original(source,target)
        with patch('agent_harness_lab.promotion.journal.os.replace',side_effect=interrupt):
            with self.assertRaises(OSError): self.journal.execute(record['deployment'])
        self.assertEqual(self.journal.load(record['deployment'])['status'],'applying')
        self.assertEqual((self.project/'AGENTS.md').read_text(),'after')
        self.journal.execute(record['deployment'])
        self.assertEqual((self.project/'CLAUDE.md').read_text(),'after')
        self.assertEqual(self.journal.active,record['deployment'])

    def test_transaction_scope_and_changed_baseline_are_rejected(self):
        with self.assertRaises(ValueError): self.journal.prepare({'source.py':None},{'source.py':None})
        with self.assertRaises(ValueError): self.journal.prepare({'AGENTS.md':None},{'AGENTS.md':None})
        record=self.prepare(); (self.project/'AGENTS.md').write_text('intervening')
        with self.assertRaises(ValueError): self.journal.execute(record['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'intervening')

    def test_deletion_is_reversed_and_later_deployment_conflicts(self):
        before={'AGENTS.md':file_record(self.project/'AGENTS.md')}
        first=self.journal.prepare(before,{'AGENTS.md':None}); self.journal.execute(first['deployment'])
        self.assertFalse((self.project/'AGENTS.md').exists())
        self.journal.rollback(first['deployment'])
        self.assertEqual((self.project/'AGENTS.md').read_text(),'before')
        self.assertEqual(self.journal.preview_rollback(first['deployment'])['status'],'conflict')


class MonitorTests(unittest.TestCase):
    def test_delayed_outcomes_use_task_start_version_and_fixed_window(self):
        policy=MonitorPolicy('v1',0,10,1)
        events=[dict(event_id='late',task_id='old',deployment='A',task_started=1,outcome=False,
                     provenance='executable_grader',critical=True),
                dict(event_id='current',task_id='new',deployment='B',task_started=2,outcome=True,provenance='executable_grader')]
        versions={'old':'A','new':'B'}
        result=monitor('B',versions,events,policy,20)
        self.assertEqual(result['state'],'stable'); self.assertEqual(result['verified_exposure'],1)
        self.assertFalse(result['automatic_rollback'])
        self.assertEqual(monitor('A',versions,events,policy,20)['state'],'paused')
        self.assertEqual(monitor('B',versions,events,policy,5)['state'],'observing')
        self.assertEqual(monitor('B',versions,events+[events[1]],policy,20)['verified_exposure'],1)
        self.assertEqual(monitor('B',versions,events+[dict(events[1],outcome=False)],policy,20)['state'],'conflict')
        with self.assertRaises(ValueError): monitor('B',versions,events,replace(policy,end=0),20)

class ReviewJournalTests(unittest.TestCase):
    def test_privileged_modes_and_conflicting_task_outcomes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'AGENTS.md'; path.write_text('fixture'); path.chmod(0o4644)
            with self.assertRaises(ValueError): file_record(path)
        policy=MonitorPolicy('v1',0,10,1)
        event=dict(event_id='one',task_id='t',deployment='A',task_started=1,outcome=True,provenance='executable_grader')
        result=monitor('A',{'t':'A'},[event,dict(event,event_id='two',outcome=False)],policy,20)
        self.assertEqual(result['state'],'conflict')

    def test_forged_privileged_snapshot_is_rejected_before_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'project').mkdir(); path=root/'project/AGENTS.md'; path.write_text('before')
            journal=Journal(root/'project',root/'private')
            before=file_record(path); after=dict(before,mode=0o4644)
            with self.assertRaises(ValueError): journal.prepare({'AGENTS.md':before},{'AGENTS.md':after})
            self.assertEqual(path.read_text(),'before')
            with self.assertRaises(ValueError): Journal(root/'project',root/'project/private')

    def test_adopt_snapshot_race_and_manual_cli_rollback(self):
        import shutil
        from agent_harness_lab.workflow import adopt,snapshot,candidate_states
        from agent_harness_lab.cli import main
        import agent_harness_lab.promotion.journal as module
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); project=root/'project'; project.mkdir(); (project/'AGENTS.md').write_text('before')
            original=snapshot(project,root/'baseline'); shutil.copytree(root/'baseline',root/'candidate')
            (root/'candidate/AGENTS.md').write_text('after'); proposed=candidate_states(root/'candidate',original)
            original_reader=module.file_record
            def mutate_then_read(path):
                if path==root/'candidate/AGENTS.md': path.write_text('unevaluated')
                return original_reader(path)
            with patch('agent_harness_lab.workflow.state_root',return_value=root/'state'):
                with patch.object(module,'file_record',side_effect=mutate_then_read):
                    with self.assertRaisesRegex(ValueError,'while journaling'):
                        adopt(project,root/'baseline',root/'candidate',original,proposed)
                self.assertEqual((project/'AGENTS.md').read_text(),'before')
                (root/'candidate/AGENTS.md').write_text('after')
                self.assertEqual(adopt(project,root/'baseline',root/'candidate',original,proposed),['AGENTS.md'])
                active=next((root/'state/promotions').glob('*/active.json'))
                deployment=json.loads(active.read_text())['deployment']
                with patch('builtins.print'):
                    self.assertEqual(main(['promotion',str(project),'--deployment',deployment]),0)
                    self.assertEqual((project/'AGENTS.md').read_text(),'after')
                    self.assertEqual(main(['promotion',str(project),'--deployment',deployment,'--action','rollback']),0)
                self.assertEqual((project/'AGENTS.md').read_text(),'before')


class NestedSurfaceAdoptionTests(unittest.TestCase):
    """Nested instructions and rules adopt through the one journal; every other surface is still refused."""
    NAMES=('AGENTS.md','app/CLAUDE.md','.claude/rules/x.md')

    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root=Path(temp.name); self.project=self.root/'project'
        for name in self.NAMES: self.write(self.project/name,'before')
        self.state=patch('agent_harness_lab.workflow.state_root',return_value=self.root/'state')
        self.state.start(); self.addCleanup(self.state.stop)

    @staticmethod
    def write(path,text):
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text(text)

    def stage(self):
        import shutil
        from agent_harness_lab.workflow import snapshot,candidate_states
        original=snapshot(self.project,self.root/'baseline',allowed_paths=list(self.NAMES))
        shutil.copytree(self.root/'baseline',self.root/'candidate')
        for name in self.NAMES[1:]: (self.root/'candidate'/name).write_text('after')
        return original,candidate_states(self.root/'candidate',original)

    def adopt(self,original,proposed):
        from agent_harness_lab.workflow import adopt
        return adopt(self.project,self.root/'baseline',self.root/'candidate',original,proposed)

    def test_nested_instruction_and_rule_adopt_and_roll_back(self):
        self.assertEqual(self.adopt(*self.stage()),['.claude/rules/x.md','app/CLAUDE.md'])
        self.assertEqual([(self.project/n).read_text() for n in self.NAMES],['before','after','after'])
        journal=Journal(self.project,next((self.root/'state/promotions').iterdir()))
        self.assertEqual(journal.load(journal.active)['metadata'],{'source':'directly_authorized_adopt_api'})
        journal.rollback(journal.active)
        self.assertEqual([(self.project/n).read_text() for n in self.NAMES],['before']*3)

    def test_stale_baseline_or_concurrent_edit_invalidates_adoption(self):
        original,proposed=self.stage()
        (self.project/'app/CLAUDE.md').write_text('user edit')
        with self.assertRaisesRegex(ValueError,'changed before adoption'): self.adopt(original,proposed)
        self.assertEqual((self.project/'.claude/rules/x.md').read_text(),'before')
        journal=Journal(self.project,self.root/'journal'); target=self.root/'target'; target.write_text('after')
        name='.claude/rules/x.md'
        record=journal.prepare({name:file_record(self.project/name)},{name:file_record(target)})
        (self.project/name).write_text('concurrent')
        with self.assertRaisesRegex(ValueError,'intervening'): journal.execute(record['deployment'])
        self.assertEqual([(self.project/n).read_text() for n in self.NAMES[1:]],['user edit','concurrent'])

    def test_settings_hooks_workflows_policy_links_and_plugin_checkouts_stay_refused(self):
        from agent_harness_lab.surfaces import classify
        from agent_harness_lab.workflow import candidate_states
        outside=self.root/'outside.md'; outside.write_text('foreign')
        (self.project/'.claude/rules/linked.md').symlink_to(outside)
        self.write(self.project/'plugins/demo/.git/HEAD','ref: refs/heads/main')  # a nested plugin checkout
        target=self.root/'target'; target.write_text('after')
        cases=('CLAUDE.local.md','.claude/settings.json','.claude/hooks/x.sh','.claude/workflows/x.js','.mcp.json',
               '.codex/config.toml','.codex/rules/default.rules','.claude/rules/linked.md',
               'plugins/demo/.claude/rules/x.md','plugins/demo/CLAUDE.md')
        for name in cases:
            with self.subTest(name=name):
                journal=Journal(self.project,self.root/'journals'/name.replace('/','_'))
                with self.assertRaises(ValueError): journal.prepare({name:None},{name:file_record(target)})
                self.assertFalse(list(journal.folder.iterdir()))
                if name.startswith('plugins/'):
                    # A staged copy holds no .git, so only the journal can see the nested checkout.
                    self.assertEqual(classify(name,origin='external_repository',in_checkout=True).writability,
                                     'recommendation_only')
                    continue
                folder=Path(tempfile.mkdtemp(dir=self.root))
                if name.endswith('linked.md'): self.write(folder/'.claude/rules/real.md','x'); (folder/name).symlink_to(outside)
                else: self.write(folder/name,'candidate')
                with self.assertRaisesRegex(ValueError,'outside project harness scope|symlink'): candidate_states(folder,{})
        self.assertFalse((self.project/'plugins/demo/CLAUDE.md').exists())

    def test_same_named_files_in_unrelated_scopes_never_become_writable_by_discovery(self):
        from agent_harness_lab.workflow import harness_files,snapshot,candidate_states
        for name in ('vendor/.git/HEAD','vendor/AGENTS.md','vendor/.claude/rules/x.md','plugin/skills/demo/SKILL.md',
                     'my-skills/demo/CLAUDE.local.md'):
            self.write(self.project/name,'foreign')
        self.assertEqual([str(p.relative_to(self.project)) for p in harness_files(self.project)],['AGENTS.md'])
        # Even an explicit selection of a nested repository's file stages but never adopts.
        names=['vendor/AGENTS.md','vendor/.claude/rules/x.md']
        original=snapshot(self.project,self.root/'baseline',allowed_paths=names)
        import shutil; shutil.copytree(self.root/'baseline',self.root/'candidate')
        for name in names: (self.root/'candidate'/name).write_text('after')
        with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
            self.adopt(original,candidate_states(self.root/'candidate',original))
        self.assertEqual([(self.project/n).read_text() for n in names],['foreign','foreign'])

    def stage_files(self,files,modes={}):
        """Stage candidate bytes over a baseline of NAMES; modes sets st_mode bits on staged files."""
        import shutil
        from agent_harness_lab.workflow import snapshot,candidate_states
        original=snapshot(self.project,self.root/'baseline',allowed_paths=list(self.NAMES))
        shutil.copytree(self.root/'baseline',self.root/'candidate')
        for name,text in files.items():
            self.write(self.root/'candidate'/name,text); (self.root/'candidate'/name).chmod(modes.get(name,0o644))
        return original,lambda:candidate_states(self.root/'candidate',original)

    def test_executable_files_on_gated_rows_are_never_staged_or_journaled(self):
        script='#!/bin/sh\necho synthetic\n'
        for name in ('.claude/rules/x.md','pkg/.claude/skills/demo/scripts/deploy','app/CLAUDE.md'):
            with self.subTest(name=name):
                import shutil; shutil.rmtree(self.root/'baseline',ignore_errors=True); shutil.rmtree(self.root/'candidate',ignore_errors=True)
                _,states=self.stage_files({name:script},{name:0o755})
                with self.assertRaisesRegex(ValueError,'needs a hook contract'): states()
                journal=Journal(self.project,self.root/'journals'/name.replace('/','_'))
                before=file_record(self.project/name) if (self.project/name).exists() else None
                with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
                    journal.prepare({name:before},{name:file_record(self.root/'candidate'/name)})
        self.assertFalse((self.project/'pkg').exists())
        self.assertEqual([(self.project/n).stat().st_mode&0o777 for n in self.NAMES],[0o644]*3)
        # An unchanged executable file is never written, so staging still accepts it.
        (self.project/'.claude/rules/x.md').chmod(0o755)
        import shutil; shutil.rmtree(self.root/'baseline'); shutil.rmtree(self.root/'candidate')
        original,states=self.stage_files({'app/CLAUDE.md':'after'})
        self.assertEqual(states()['.claude/rules/x.md'],original['.claude/rules/x.md'])

    def test_agent_definitions_that_register_hooks_are_never_adopted(self):
        hooked={'.claude/agents/r.md':'---\nname: r\ndescription: d\nhooks:\n  PreToolUse:\n    - hooks:\n'
                '        - type: command\n          command: synthetic-hook\n---\nBody\n',
                '.claude/agents/q.md':'---\n{name: q, description: d, "\\x68ooks": {}}\n---\n',
                '.codex/agents/r.toml':'name = "r"\ndeveloper_instructions = "x"\n[[hooks.PreToolUse]]\ncommand = "x"\n',
                '.codex/agents/bad.toml':'name = = "broken"\n'}
        for name,text in hooked.items():
            with self.subTest(name=name):
                import shutil; shutil.rmtree(self.root/'baseline',ignore_errors=True); shutil.rmtree(self.root/'candidate',ignore_errors=True)
                _,states=self.stage_files({name:text})
                with self.assertRaisesRegex(ValueError,'needs a hook contract'): states()
                journal=Journal(self.project,self.root/'journals'/name.replace('/','_'))
                with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
                    journal.prepare({name:None},{name:file_record(self.root/'candidate'/name)})
        self.assertFalse((self.project/'.claude/agents').exists()); self.assertFalse((self.project/'.codex').exists())
        import shutil; shutil.rmtree(self.root/'baseline'); shutil.rmtree(self.root/'candidate')
        plain={'.claude/agents/ok.md':'---\nname: ok\ndescription: reviews synthetic diffs\n---\nRead only.\n',
               '.codex/agents/ok.toml':'name = "ok"\ndeveloper_instructions = "Review synthetic diffs."\n'}
        original,states=self.stage_files(plain)
        self.assertEqual(self.adopt(original,states()),sorted(plain))

    def test_frontmatter_that_registers_hooks_or_grants_execution_is_never_adopted(self):
        hook='hooks:\n  PreToolUse:\n    - hooks:\n        - type: command\n          command: ./synthetic.sh\n'
        granted={'.claude/commands/deploy.md':'---\ndescription: x\n'+hook+'---\nBody\n',
                 'pkg/.claude/skills/s/SKILL.md':'---\nname: s\ndescription: d\n'+hook+'---\nBody\n',
                 '.claude/skills/s/SKILL.md':'---\nname: s\ndescription: d\n'+hook+'---\nBody\n',
                 '.claude/rules/r.md':'---\npaths: ["*.py"]\n'+hook+'---\nBody\n',
                 '.claude/commands/run.md':'---\ndescription: d\nallowed-tools: Bash(*)\n---\nBody\n',
                 '.claude/agents/p.md':'---\nname: p\ndescription: d\npermissionMode: bypassPermissions\n---\nBody\n',
                 '.claude/agents/m.md':'---\nname: m\ndescription: d\nmcpServers:\n  synthetic:\n'
                 '    command: /bin/sh\n    args: ["-c", "true"]\n---\nBody\n'}
        for name,text in granted.items():
            with self.subTest(name=name):
                import shutil; shutil.rmtree(self.root/'baseline',ignore_errors=True); shutil.rmtree(self.root/'candidate',ignore_errors=True)
                _,states=self.stage_files({name:text})
                with self.assertRaisesRegex(ValueError,'needs a hook contract'): states()
                journal=Journal(self.project,self.root/'journals'/name.replace('/','_'))
                with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
                    journal.prepare({name:None},{name:file_record(self.root/'candidate'/name)})
        self.assertFalse((self.project/'pkg').exists()); self.assertFalse((self.project/'.claude/commands').exists())
        import shutil; shutil.rmtree(self.root/'baseline'); shutil.rmtree(self.root/'candidate')
        plain={'.claude/commands/ok.md':'---\ndescription: summarize synthetic diffs\n---\nBody\n',
               'pkg/.claude/skills/ok/SKILL.md':'---\nname: ok\ndescription: d\n---\nBody\n'}
        original,states=self.stage_files(plain)
        self.assertEqual(self.adopt(original,states()),sorted(plain))

    def test_rolling_back_a_rollback_probes_containment_like_a_promotion(self):
        original,proposed=self.stage_files({'app/CLAUDE.md':'after'})
        self.assertEqual(self.adopt(original,proposed()),['app/CLAUDE.md'])
        (self.project/'app/.git').mkdir()
        journal=Journal(self.project,next((self.root/'state/promotions').iterdir()))
        rollback=journal.rollback(journal.active)
        self.assertEqual((self.project/'app/CLAUDE.md').read_text(),'before')
        # Reverting the rollback re-applies the promotion's content into what is now a nested repository.
        with self.assertRaisesRegex(ValueError,'outside owned harness surface'): journal.rollback(rollback['deployment'])
        self.assertEqual((self.project/'app/CLAUDE.md').read_text(),'before')
        # An external caller cannot claim the rollback exemption either.
        self.write(self.project/'vendor/AGENTS.md','before'); (self.project/'vendor/.git').mkdir()
        target=self.root/'target'; target.write_text('after')
        with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
            journal.prepare({'vendor/AGENTS.md':file_record(self.project/'vendor/AGENTS.md')},
                            {'vendor/AGENTS.md':file_record(target)},None,'rollback')
        self.assertEqual((self.project/'vendor/AGENTS.md').read_text(),'before')

    def test_rollback_survives_a_repository_created_below_the_project_after_adoption(self):
        original,proposed=self.stage_files({'AGENTS.md':'after','app/CLAUDE.md':'after'})
        self.assertEqual(self.adopt(original,proposed()),['AGENTS.md','app/CLAUDE.md'])
        (self.project/'app/.git').mkdir()  # a later git init, submodule or worktree
        journal=Journal(self.project,next((self.root/'state/promotions').iterdir()))
        self.assertEqual(journal.preview_rollback(journal.active)['status'],'rollback_eligible')
        journal.rollback(journal.active)
        self.assertEqual([(self.project/n).read_text() for n in ('AGENTS.md','app/CLAUDE.md')],['before','before'])
        # A new promotion still probes containment, so the nested repository's file is no longer adoptable.
        target=self.root/'target'; target.write_text('again')
        with self.assertRaisesRegex(ValueError,'outside owned harness surface'):
            journal.prepare({'app/CLAUDE.md':file_record(self.project/'app/CLAUDE.md')},{'app/CLAUDE.md':file_record(target)})
