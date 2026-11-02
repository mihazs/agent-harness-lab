"""Public CLI tests use independently synthetic projects; no model or network."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab import config
from agent_harness_lab.promotion.journal import Journal
from agent_harness_lab.one_call import structural_candidate


class OneCallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'; self.project.mkdir()
        self.skill = self.project/'.agents/skills/example/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.valid = b'---\nname: example\ndescription: Use for synthetic checks.\n---\nKeep the body intact.\n'
        self.skill.write_bytes(b'\xef\xbb\xbf'+self.valid)
        self.skill.chmod(0o640)
        state=patch('agent_harness_lab.workflow.state_root', return_value=self.root/'state')
        state.start(); self.addCleanup(state.stop)

    def invoke(self, *args):
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            code=main(['improve',str(self.project),*args])
        return code,json.loads(output.getvalue())

    def test_single_call_applies_verified_contract_and_is_idempotent(self):
        with patch('agent_harness_lab.cli.evaluator', side_effect=AssertionError('no model')):
            code,result=self.invoke('--repair-contracts')
        self.assertEqual(code,0)
        self.assertEqual(result['status'],'applied')
        self.assertEqual(self.skill.read_bytes(),self.valid)
        self.assertEqual(self.skill.stat().st_mode&0o777,0o640)
        journal=Journal(self.project,result['journal'])
        self.assertEqual(journal.load(result['deployment'])['status'],'committed')
        self.assertFalse(result['behavioral_certification'])
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['rollback',result['deployment'],'--project',str(self.project)]),0)
        self.assertEqual(self.skill.read_bytes(),b'\xef\xbb\xbf'+self.valid)
        self.invoke('--repair-contracts')
        self.assertEqual(self.invoke('--repair-contracts')[1]['status'],'no_change')

    def test_recommendation_config_never_applies_contract_patch(self):
        settings=config.defaults(); settings['harness_paths']=['.agents/skills/example/SKILL.md']
        config.create(self.project,settings)
        before=self.skill.read_bytes()
        code,result=self.invoke('--repair-contracts')
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'configuration_requires_recommendation_only')
        self.assertEqual(self.skill.read_bytes(),before)

    def test_unsupported_executable_or_ambiguous_metadata_is_not_repaired(self):
        for header in [b'name: wrong\ndescription: Example.\n',
                       b'name: example\ndescription: Example.\nhooks: value\n',
                       b'name: example\nname: example\ndescription: Example.\n']:
            raw=b'\xef\xbb\xbf---\n'+header+b'---\nBody\n'
            self.assertIsNone(structural_candidate(raw,'example'))

    def test_source_symlink_is_not_modified(self):
        self.skill.unlink(); external=self.root/'external'; external.write_bytes(b'\xef\xbb\xbf'+self.valid)
        self.skill.symlink_to(external)
        with self.assertRaises(SystemExit): self.invoke('--repair-contracts')
        self.assertTrue(external.read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_ambiguous_runtime_blocks_before_inference(self):
        with patch('agent_harness_lab.one_call.shutil.which', return_value='/synthetic/runtime'):
            code,result=self.invoke()
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'select_one_runtime_with_agent')

    def test_offline_configuration_blocks_before_inference(self):
        config.create(self.project,config.defaults())
        code,result=self.invoke()
        self.assertEqual(code,3)
        self.assertEqual(result['reason'],'configured_profile_does_not_allow_cooperative_improvement')

    def test_configuration_and_cli_precedence(self):
        from argparse import Namespace
        from agent_harness_lab.one_call import resolve
        settings=config.prepare(dict(profile='cooperative',runtime=dict(name='codex',executable='/synthetic/codex'), limits=dict(seconds=100,trials=8)))
        config.create(self.project,settings)
        args=Namespace(project=self.project,timeout=None,agent='auto',codex='codex',claude='claude')
        resolve(args,set())
        self.assertEqual((args.agent,args.codex,args.timeout,args.trial_limit),('codex','/synthetic/codex',100,8))
        args=Namespace(project=self.project,timeout=20,agent='claude',codex='/explicit/codex',claude='claude')
        resolve(args,{'--codex','--timeout','--agent'})
        self.assertEqual((args.agent,args.codex,args.timeout),('claude','/explicit/codex',20))
