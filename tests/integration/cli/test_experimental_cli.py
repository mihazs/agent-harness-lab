from dataclasses import asdict
import json
import subprocess
import sys
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics.retrieval import Incident


@pytest.mark.parametrize('name', ['interaction-contract-v1-output', 'audit-sampling-receipt-v1',
    'ahl-definition-validation-v1', 'diagnostic-triage-evaluation-v2', 'ahl-config-inventory-v1',
    'ahl-config-recommendations-v1', 'ahl-commands-inventory-v1', 'ahl-plugin-inventory-v1',
    'ahl-harness-inventory-v1', 'ahl-claude-rules-v1'])
def test_private_golden_output_rejects_forged_public_benchmark_export(tmp_path, name):
    from pathlib import Path
    from tests.unit.evaluation.test_benchmark_contracts import record
    golden = Path(__file__).resolve().parents[2]/'support/golden_profiles'/f'{name}.json'
    private = json.loads(golden.read_text())
    source = tmp_path/'input.json'; source.write_text(json.dumps([private | record() | {'publication_allowed': True}]))
    output = tmp_path/'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(source), '--output', str(output), '--public'])
    assert exc.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize('marker', ['profile', 'ambiguous_sources', 'outcome_correlation', 'grouping_revision_sha256'])
def test_private_source_outcomes_cannot_be_exported_by_relabeling(tmp_path, marker):
    from agent_harness_lab.evaluation.benchmark import MATCH_FIELDS
    from agent_harness_lab.sources.outcomes import correlate
    report = correlate(dict(records=[], supersessions=[]), [])
    settings = dict.fromkeys(MATCH_FIELDS, 'synthetic'); settings['effective_settings_verified'] = True
    report.update(publication_allowed=True, baseline_runtime=settings, candidate_runtime=settings,
        provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic', derived_from_private=False))
    if marker != 'profile': report['profile'] = 'forged'
    if marker != 'ambiguous_sources': report.pop('ambiguous_sources')
    if marker == 'outcome_correlation': report['outcome_correlation'] = {}
    if marker == 'grouping_revision_sha256': report['grouping_revision_sha256'] = 'synthetic'
    source = tmp_path/'input.json'; source.write_text(json.dumps([report]))
    output = tmp_path/'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(source), '--output', str(output), '--public'])
    assert exc.value.code == 2
    assert not output.exists()


def test_public_benchmark_accepts_only_known_flat_records(tmp_path):
    from agent_harness_lab.evaluation.benchmark import MATCH_FIELDS
    from agent_harness_lab.evaluation.interaction_contract import evaluate
    runtime = dict.fromkeys(MATCH_FIELDS, 'synthetic'); runtime['effective_settings_verified'] = True
    base = dict(baseline_runtime=runtime, candidate_runtime=runtime, status='not_run', execution_kind='fake_agent',
        provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic', derived_from_private=False))
    private = evaluate({}, {})
    assert private['publication_allowed'] is False
    cases = [base, dict(base, **(private | {'publication_allowed': True})),
        dict(base, profile='unknown-v1'), dict(base, receipt={'publication_allowed': True}),
        dict(base, unrecognized='synthetic'), dict(base, baseline_runtime=runtime | {'unrecognized': 'synthetic'}),
        dict(base, provenance=base['provenance'] | {'unrecognized': 'synthetic'}),
        dict(base, baseline_runtime=runtime | {'model_revision': {'private': 'SYNTHETIC_CANARY'}},
             candidate_runtime=runtime | {'model_revision': {'private': 'SYNTHETIC_CANARY'}}),
        dict(base, status='SYNTHETIC_CANARY'), dict(base, execution_kind='SYNTHETIC_CANARY'),
        dict(base, baseline_runtime=runtime | {'effective_settings_verified': 'true'}),
        dict(base, provenance=base['provenance'] | {'source_ref': {'private': 'SYNTHETIC_CANARY'}})]
    for index, record in enumerate(cases):
        source = tmp_path/f'{index}.json'; source.write_text(json.dumps([record]))
        output = tmp_path/f'out-{index}'
        if index == 0:
            assert main(['benchmark-report', '--input', str(source), '--output', str(output), '--public']) == 0
            assert (output/'results.json').is_file()
        else:
            with pytest.raises(SystemExit) as exc:
                main(['benchmark-report', '--input', str(source), '--output', str(output), '--public'])
            assert exc.value.code == 2
            assert not output.exists()


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


def test_local_retrieval_cli_contrasts_a_hypothesis_with_counterexamples(tmp_path):
    path=tmp_path/'incidents.json'
    incident=Incident('i','r','synthetic timeout',('tool',),('event',),'review','reviewed_annotation','random','synthetic',True)
    path.write_text(json.dumps([asdict(incident),
        asdict(incident)|dict(incident_id='g',labels=['grader'],summary='timeout private-canary',source_kind='private'),
        asdict(incident)|dict(incident_id='i2'),
        asdict(incident)|dict(incident_id='ok',labels=['success'])]))
    with patch('builtins.print') as output:
        assert main(['incident-search','--input',str(path),'--query','timeout',
                     '--hypothesis','tool','--control-label','success']) == 0
    text=output.call_args.args[0]
    data=json.loads(text)
    assert data['profile'] == 'incident-contrast-v1'
    assert {name:[row['incident_id'] for row in group['results']] for name,group in data['groups'].items()} == \
        dict(supporting=['i','i2'],contradicting=['g'],controls=['ok'],unresolved=[])
    assert 'private-canary' not in text
    with patch('builtins.print') as output:
        assert main(['incident-search','--input',str(path),'--query','timeout','--top-k','1',
                     '--hypothesis','tool','--control-label','success']) == 0
    groups=json.loads(output.call_args.args[0])['groups']
    assert {name:(group['matches'],[row['incident_id'] for row in group['results']]) for name,group in groups.items()} == \
        dict(supporting=(2,['i']),contradicting=(1,['g']),controls=(1,['ok']),unresolved=(0,[]))
    with pytest.raises(SystemExit) as exit:
        main(['incident-search','--input',str(path),'--query','timeout','--control-label','success'])
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


@pytest.mark.parametrize('forgery', ['profile', 'marker', 'inventory', 'precedence'])
def test_synthetic_static_audit_cannot_be_exported_by_relabeling(tmp_path, forgery):
    from agent_harness_lab import audit
    from agent_harness_lab.evaluation.benchmark import MATCH_FIELDS
    project = tmp_path / 'project'; project.mkdir()
    (project / 'AGENTS.md').write_text('Synthetic instruction.\n')
    report = audit.run(project, 'codex').document()
    report.setdefault('instruction_precedence', {'publication_allowed': False})
    settings = dict.fromkeys(MATCH_FIELDS, 'synthetic'); settings['effective_settings_verified'] = True
    report.update(publication_allowed=True, baseline_runtime=settings, candidate_runtime=settings,
        provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='synthetic', derived_from_private=False))
    if forgery != 'profile': report['profile'] = 'forged'
    for name, case in (('audit_sha256', 'marker'), ('harness_inventory_sha256', 'inventory'),
                       ('instruction_precedence', 'precedence')):
        if forgery != case: report.pop(name)
    source = tmp_path / 'report.json'; source.write_text(json.dumps([report]))
    output = tmp_path / 'public'
    with pytest.raises(SystemExit) as exc:
        main(['benchmark-report', '--input', str(source), '--output', str(output), '--public'])
    assert exc.value.code == 2
    assert not output.exists()
