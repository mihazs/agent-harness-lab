from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics.retrieval import Incident


class ExperimentalCliTests(unittest.TestCase):
    def test_simulation_entrypoint_does_not_require_model_or_credentials(self):
        command=[sys.executable,'-m','agent_harness_lab','research-simulate','--tasks','10','--scenario','delayed']
        run=subprocess.run(command,capture_output=True,text=True)
        self.assertEqual(run.returncode,0,run.stderr)
        data=json.loads(run.stdout)
        self.assertFalse(data['training_started']); self.assertEqual(data['real_task_effectiveness'],'not_run')

    def test_local_retrieval_cli_and_invalid_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'incidents.json'
            incident=Incident('i','r','synthetic timeout',('tool',),('event',),'review','reviewed_annotation','random','synthetic',True)
            path.write_text(json.dumps([asdict(incident)]))
            with patch('builtins.print') as output:
                self.assertEqual(main(['incident-search','--input',str(path),'--query','timeout']),0)
                data=json.loads(output.call_args.args[0]); self.assertEqual(len(data['results']),1)
            with self.assertRaises(SystemExit) as exit:
                main(['incident-search','--input',str(path),'--query','timeout','--top-k','100'])
            self.assertEqual(exit.exception.code,2)

    def test_benchmark_report_end_to_end_and_private_export_refusal(self):
        def record(source='synthetic'):
            runtime=dict(provider='local',runtime='fake',model_revision='synthetic',reasoning='none',
                task_bank_revision='v1',sandbox_image='fixture',permissions='offline',harness_version='v1',effective_settings_verified=True)
            return dict(baseline_runtime=runtime,candidate_runtime=runtime,status='not_run',execution_kind='fake_agent',
                provenance=dict(source_kind=source,approved_publication=True,source_ref='synthetic fixture',derived_from_private=False))
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); source=root/'input.json'; source.write_text(json.dumps([record()]))
            with patch('builtins.print'):
                self.assertEqual(main(['benchmark-report','--input',str(source),'--output',str(root/'report'),'--public']),0)
            self.assertTrue((root/'report/report.html').is_file())
            self.assertEqual((root/'report/results.json').stat().st_mode & 0o777,0o600)
            source.write_text(json.dumps([record(source='private')]))
            with self.assertRaises(SystemExit):
                main(['benchmark-report','--input',str(source),'--output',str(root/'blocked'),'--public'])
            self.assertFalse((root/'blocked').exists())
