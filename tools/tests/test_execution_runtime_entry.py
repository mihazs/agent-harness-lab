"""Fresh configured fake runtimes exercise the public controller and presentation."""
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab import config
from agent_harness_lab.cli import main
import test_workflow as workflow_fixture


class TTYOutput(io.StringIO):
    def isatty(self):return True


class ExecutionRuntimeEntryTests(unittest.TestCase):
    def setUp(self):
        expected=Path(os.environ.get('AHL_EXPECTED_PACKAGE_ROOT') or Path(__file__).resolve().parents[2]).resolve()
        self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(expected))

    def configured(self,runtime,mode=None):
        fixture=workflow_fixture.WorkflowTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.calls=fixture.root/'runtime-calls.jsonl'
        trace="print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))"
        wire="print(json.dumps({'type':'result','subtype':'success','is_error':False,'usage':{'output_tokens':1}} if '--print' in sys.argv else {'type':'turn.completed','usage':{'output_tokens':1}}))"
        script=workflow_fixture.FAKE.replace(trace,wire)
        self.assertNotEqual(script,workflow_fixture.FAKE)
        script=script.replace('prompt=sys.stdin.read()',
            "prompt=sys.stdin.read()\nwith p("+repr(str(fixture.calls))+").open('a') as calls:\n calls.write(json.dumps(dict(argv=sys.argv[1:],phase=json.loads(p('context.json').read_text())['phase'] if p('context.json').exists() else 'executor'))+'\\n')")
        fixture.agent.write_text(script)
        fixture.original=b'bad\nSYNTHETIC_PRIVATE_BODY_CANARY\n'
        (fixture.project/'AGENTS.md').write_bytes(fixture.original);(fixture.project/'AGENTS.md').chmod(0o640)
        (fixture.project/'CLAUDE.md').write_text('SYNTHETIC_OUT_OF_SCOPE_CANARY')
        if mode:(fixture.project/'mode').write_text(mode)
        config.create(fixture.project,config.prepare(dict(profile='cooperative',
            runtime=dict(name=runtime,executable=str(fixture.agent)),harness_paths=['AGENTS.md'],limits=dict(seconds=30,trials=20))))
        fixture.configuration_pin=config.load(fixture.project/'ahl.json')[1]
        return fixture

    def invoke(self,fixture,*,tty=False,json_output=False):
        stdout=TTYOutput() if tty else io.StringIO();stderr=io.StringIO()
        arguments=['improve',str(fixture.project)]+(['--json'] if json_output else [])
        with redirect_stdout(stdout),redirect_stderr(stderr),patch('builtins.input',side_effect=AssertionError('CLI must not prompt')):
            code=main(arguments)
        outputs=list((fixture.root/'state/runs').glob('*/run-*'))
        self.assertEqual(len(outputs),1)
        result=json.loads((outputs[0]/'workflow.json').read_bytes())
        self.assertEqual((fixture.project/'AGENTS.md').read_bytes(),fixture.original)
        self.assertEqual((fixture.project/'AGENTS.md').stat().st_mode&0o777,0o640)
        self.assertEqual(config.load(fixture.project/'ahl.json')[1],fixture.configuration_pin)
        self.assertFalse((outputs[0]/'preparation/baseline/CLAUDE.md').exists())
        return code,result,stdout.getvalue(),stderr.getvalue()

    def assert_pipeline(self,fixture,result,runtime):
        output=Path(result['output'])
        self.assertEqual(result['status'],'inconclusive');self.assertEqual(result['changed_files'],[])
        self.assertEqual(result['recommendation'],'candidate_requires_independent_certification')
        self.assertTrue(Path(result['plan']).is_file());self.assertFalse((output/'evaluation-1').exists())
        trials=json.loads((output/'evaluation-0/results.json').read_bytes())['trials']
        self.assertEqual(len(trials),6)
        self.assertEqual({(row['version'],row['split']) for row in trials},
            {(version,split) for version in ('baseline','candidate') for split in ('development','regression','heldout')})
        calls=[json.loads(line) for line in fixture.calls.read_text().splitlines()]
        self.assertEqual([row['phase'] for row in calls],['candidate','evaluation']+['executor']*6)
        self.assertTrue(all(('--print' in row['argv'])==(runtime=='claude') for row in calls))

    def test_fresh_configured_codex_and_claude_run_one_public_pipeline_with_non_tty_json(self):
        for runtime in ('codex','claude'):
            with self.subTest(runtime=runtime):
                fixture=self.configured(runtime)
                code,result,stdout,stderr=self.invoke(fixture)
                self.assertEqual(code,3);self.assert_pipeline(fixture,result,runtime)
                self.assertEqual(json.loads(stdout),result);self.assertEqual(stdout,json.dumps(result,indent=2)+'\n')
                self.assertNotIn('AHL: ',stderr)

    def test_tty_claude_reports_actual_stage_order_and_a_bounded_human_outcome(self):
        fixture=self.configured('claude')
        code,result,stdout,stderr=self.invoke(fixture,tty=True)
        self.assertEqual(code,3);self.assert_pipeline(fixture,result,'claude')
        for label in ('Outcome: inconclusive','Changed files: 0','Artifacts: ','Next action: '):self.assertIn(label,stdout)
        stages=[line[len('AHL: '):].split()[0].rstrip(':') for line in stderr.splitlines() if line.startswith('AHL: ')]
        required=['preflight','evidence','proposal','evaluation','comparison']
        self.assertEqual([stage for stage in stages if stage in required],required)
        for canary in ('SYNTHETIC_PRIVATE_BODY_CANARY','SYNTHETIC_OUT_OF_SCOPE_CANARY','output_tokens','"usage"'):
            self.assertNotIn(canary,stdout+stderr)
        self.assertLess(len(stdout),2048)

    def test_json_flag_preserves_machine_output_even_on_a_tty(self):
        fixture=self.configured('codex')
        code,result,stdout,stderr=self.invoke(fixture,tty=True,json_output=True)
        self.assertEqual(code,3);self.assert_pipeline(fixture,result,'codex')
        self.assertEqual(stdout,json.dumps(result,indent=2)+'\n');self.assertNotIn('AHL: ',stderr)

    def test_no_change_does_not_report_unexecuted_evaluation_or_comparison_stages(self):
        fixture=self.configured('codex',mode='no_change')
        code,result,stdout,stderr=self.invoke(fixture,tty=True)
        self.assertEqual((code,result['status']),(0,'no_change'));self.assertIn('Outcome: no_change',stdout)
        self.assertIn('AHL: proposal',stderr)
        self.assertNotIn('AHL: evaluation',stderr);self.assertNotIn('AHL: comparison',stderr)
        self.assertEqual(len(fixture.calls.read_text().splitlines()),1)
        self.assertFalse((Path(result['output'])/'evaluation-0').exists())
