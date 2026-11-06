from dataclasses import asdict
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics.retrieval import Incident


def test_simulation_entrypoint_does_not_require_model_or_credentials():
    command=[sys.executable,'-m','agent_harness_lab','research-simulate','--tasks','10','--scenario','delayed']
    run=subprocess.run(command,capture_output=True,text=True)
    assert run.returncode == 0, run.stderr
    data=json.loads(run.stdout)
    assert not data['training_started']; assert data['real_task_effectiveness'] == 'not_run'


def test_local_retrieval_cli_and_explicit_limit(tmp_path):
    path=tmp_path/'incidents.json'
    incident=Incident('i','r','synthetic timeout',('tool',),('event',),'review','reviewed_annotation','random','synthetic',True)
    records=[asdict(incident)|dict(incident_id=str(index),summary='timeout '+('x'*1024*1024)) for index in range(6)]
    path.write_text(json.dumps(records))
    with patch('builtins.print') as output:
        assert main(['incident-search','--input',str(path),'--query','timeout']) == 0
        data=json.loads(output.call_args.args[0]); assert len(data['results']) == 6
        assert main(['incident-search','--input',str(path),'--query','timeout','--top-k','100']) == 0
        assert len(json.loads(output.call_args.args[0])['results']) == 6
        assert main(['incident-search','--input',str(path),'--query','timeout','--top-k','2']) == 0
        assert len(json.loads(output.call_args.args[0])['results']) == 2
    with pytest.raises(SystemExit) as exit:
        main(['incident-search','--input',str(path),'--query','timeout','--top-k','0'])
    assert exit.value.code == 2


def test_benchmark_report_end_to_end_and_private_export_refusal(tmp_path):
    def record(source='synthetic'):
        runtime=dict(provider='local',runtime='fake',model_revision='synthetic',reasoning='none',
            task_bank_revision='v1',sandbox_image='fixture',permissions='offline',controller_version='v1',effective_settings_verified=True)
        return dict(baseline_runtime=runtime,candidate_runtime=runtime,status='not_run',execution_kind='fake_agent',
            provenance=dict(source_kind=source,approved_publication=True,source_ref='synthetic fixture',derived_from_private=False))
    root=tmp_path; source=root/'input.json'; source.write_text(' '*(10*1024*1024+1)+json.dumps([record()]))
    with patch('builtins.print'):
        assert main(['benchmark-report','--input',str(source),'--output',str(root/'report'),'--public']) == 0
    assert (root/'report/report.html').is_file()
    assert (root/'report/results.json').stat().st_mode & 0o777 == 0o600
    source.write_text(json.dumps([record(source='private')]))
    with pytest.raises(SystemExit):
        main(['benchmark-report','--input',str(source),'--output',str(root/'blocked'),'--public'])
    assert not (root/'blocked').exists()
    for index,fields in enumerate((dict(publication_allowed=False),
            dict(publication_allowed=True,receipt_sha256='1'*64),
            dict(publication_allowed=True,algorithm='private-preservation-decision-v1'),
            dict(publication_allowed=True,receipt=dict(algorithm='private-preservation-decision-v1')),
            dict(publication_allowed=True,algorithm='corrected-cohort-qualification-v1'),
            dict(publication_allowed=True,qualification_sha256='3'*64),
            dict(publication_allowed=True,analysis=dict(controller_run_id='2'*64)))):
        forged=record(); forged.update(fields)
        source.write_text(json.dumps([forged])); output=root/f'blocked-controller-{index}'
        with pytest.raises(SystemExit) as exit:
            main(['benchmark-report','--input',str(source),'--output',str(output),'--public'])
        assert exit.value.code == 2
        assert not output.exists()
