import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.bank_tools import export_manifest,audit_graders
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate

ROOT=Path(__file__).resolve().parents[3]
BANK=ROOT/'agent_harness_lab/evaluation/fixtures/starter-v1/bank.json'


class TestStarterBank:
    def test_all_falsifiers_and_alternative_valid_solutions_are_graded(self):
        result=audit_graders(BANK,cooperative=True)
        assert len(result['checks']) == 29
        assert all(c['status']=='passed' for c in result['checks'])
        assert result['real_agent_trials'] == 'not_run'
        assert not validate(BANK)['certification_eligible']
        with pytest.raises(ValueError): audit_graders(BANK)

    def test_reproducible_pack_under_private_umask_and_declared_lineage(self, tmp_path):
        import os
        source=ROOT/'scripts/build_synthetic_bank.py'
        spec=importlib.util.spec_from_file_location('synthetic_bank_builder',source)
        builder=importlib.util.module_from_spec(spec); spec.loader.exec_module(builder)
        old=os.umask(0o077)
        try: rebuilt=builder.build(tmp_path/'bank')
        finally: os.umask(old)
        assert digest(rebuilt) == digest(BANK.parent)
        tasks=json.loads(BANK.read_text())['tasks']
        calculator=[t for t in tasks if t['root_id']=='calculator']
        assert len(calculator) == 2

    def test_manifest_export_freezes_bundles_and_respects_caller_budget(self, tmp_path):
        root=tmp_path
        with pytest.raises(ValueError): export_manifest(BANK,root/'small',max_trials=2)
        assert not (root/'small').exists()
        manifest=export_manifest(BANK,root/'export')
        assert len(manifest['tasks']) == 7
        assert len(manifest['grader_self_tests']) == 21
        assert not manifest['automatic_promotion']
        assert all((root/'export'/v['path']).is_dir() for v in manifest['grader_bundles'].values())
        with pytest.raises(ValueError): export_manifest(BANK,BANK.parent/'nested')

    def test_cli_audit_is_explicit_and_reports_unverified_independence(self):
        with patch('builtins.print') as output:
            assert main(['bank','--starter','--audit','--cooperative-controls']) == 3
        result=json.loads(output.call_args.args[0])
        assert len(result['grader_audit']['checks']) == 29
        assert not result['certification_eligible']

    def test_runner_rejects_changed_declared_grader_data_before_agent_launch(self, tmp_path):
        import subprocess,sys
        runner=ROOT/'skills/harness-evaluation/scripts/run_evaluation.py'
        root=tmp_path; export_manifest(BANK,root/'export')
        (root/'baseline').mkdir(); (root/'candidate').mkdir()
        expected=root/'export/graders/localization/expected.json'
        expected.write_text('{}')
        run=subprocess.run([sys.executable,str(runner),'--manifest',str(root/'export/manifest.json'),
            '--baseline',str(root/'baseline'),'--candidate',str(root/'candidate'),
            '--output',str(root/'output'),'--codex','missing-synthetic-cli'],capture_output=True,text=True)
        assert run.returncode == 2
        assert 'grader bundle integrity mismatch' in run.stderr
        assert not (root/'output').exists()

    def test_typed_grader_defects_remain_distinct_from_task_failure(self):
        spec=importlib.util.spec_from_file_location('typed_runner',ROOT/'skills/harness-evaluation/scripts/run_evaluation.py')
        runner=importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        def grade(result,code=0):
            return dict(exit_code=code,timeout=False,stdout=json.dumps(result))
        good=dict(schema_version=1,valid=True,outcome=True,grader_id='synthetic',grader_version='v1')
        assert runner.grading_status(grade(good),typed=True) == 'passed'
        assert runner.grading_status(grade(dict(good,outcome=False),1),typed=True) == 'failed'
        for broken in (grade(dict(good,valid=False),1),grade(good,1),grade({'outcome':True}),grade(dict(good,outcome=None)),grade(dict(good,schema_version=True))):
            assert runner.grading_status(broken,typed=True) == 'grader_error'

    def test_source_materialization_cannot_escape_fixture(self, tmp_path):
        from agent_harness_lab.evaluation.bank_tools import materialize_sources
        root=tmp_path/'fixture'; root.mkdir(); (root/'source_files.json').write_text(json.dumps({'app/../../escape.py':'pass'}))
        with pytest.raises(ValueError): materialize_sources(root)
        assert not (tmp_path/'escape.py').exists()

    @pytest.mark.parametrize('task,mutant',[
        pytest.param('regression',{'settings':{'theme':'dark','retries':2,'enabled':1}},id='regression-integer-for-boolean'),
        pytest.param('localization',{'sums':[5,-3,False]},id='localization-boolean-for-number')])
    def test_boolean_number_mutants_do_not_pass_semantic_grading(self, tmp_path, task, mutant):
        import shutil,subprocess,sys
        from agent_harness_lab.evaluation.bank_tools import materialize_sources
        workspace=tmp_path/'work'; shutil.copytree(BANK.parent/'tasks'/task,workspace)
        materialize_sources(workspace)
        if task=='localization': (workspace/'app/calc.py').write_text('def add(a,b): return a+b\n')
        (workspace/'result.json').write_text(json.dumps(mutant))
        bundle=BANK.parent/'graders'/task
        run=subprocess.run([sys.executable,str(bundle/'grade.py.txt'),str(workspace),str(bundle/'expected.json')],capture_output=True,text=True)
        assert run.returncode == 1
        assert json.loads(run.stdout)['outcome'] is False

    def test_copied_source_must_match_frozen_bank_before_manifest_is_written(self, tmp_path):
        import shutil
        original=shutil.copytree
        output=tmp_path/'export'
        def tamper(source,target,*args,**kwargs):
            result=original(source,target,*args,**kwargs)
            if Path(target).name=='localization' and Path(target).parent.name=='fixtures':
                (Path(target)/'original.txt').write_text('changed during copying')
            return result
        with patch('agent_harness_lab.evaluation.bank_tools.shutil.copytree',side_effect=tamper):
            with pytest.raises(ValueError, match='source changed'):
                export_manifest(BANK,output)
        assert not (output/'manifest.json').exists()

    def test_fake_agent_runs_exported_bank_with_typed_grades_and_no_promotion(self, tmp_path):
        import subprocess,sys
        fake_code='''#!/usr/bin/env python3
import json,pathlib,sys
if '--version' in sys.argv: print('synthetic-offline-agent-v1'); raise SystemExit(0)
sys.stdin.read()
w=pathlib.Path.cwd(); inputs=json.loads((w/'task.json').read_text())['inputs']
if 'pairs' in inputs:
 (w/'app/calc.py').write_text('def add(a,b): return a+b\\n')
 result={'sums':[sum(pair) for pair in inputs['pairs']]}
elif 'settings' in inputs: result={'settings':dict(inputs['settings'],enabled=True)}
elif 'responses' in inputs: result={'records':next(r['records'] for r in reversed(inputs['responses']) if r['status']=='ok')}
elif 'values' in inputs: result={'count':len(inputs['values'])}
elif 'requests' in inputs: result={'selections':['calculator' if r.startswith('calculate ') else None for r in inputs['requests']]}
elif 'queue' in inputs: result={'processed':inputs['processed']+inputs['queue'][inputs['cursor']:],'cursor':len(inputs['queue'])}
else: result={'token_count':len(inputs['text'].split())}
(w/'result.json').write_text(json.dumps(result))
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
'''
        root=tmp_path; export_manifest(BANK,root/'export')
        (root/'baseline').mkdir(); (root/'candidate').mkdir()
        binary=root/'fake-agent'; binary.write_text(fake_code); binary.chmod(0o700)
        runner=ROOT/'skills/harness-evaluation/scripts/run_evaluation.py'
        run=subprocess.run([sys.executable,str(runner),'--manifest',str(root/'export/manifest.json'),
            '--baseline',str(root/'baseline'),'--candidate',str(root/'candidate'),'--output',str(root/'results'),
            '--codex',str(binary)],capture_output=True,text=True)
        assert run.returncode == 0, run.stderr
        result=json.loads((root/'results/results.json').read_text())
        assert len(result['trials']) == 14
        assert all(t['status']=='passed' and t['grader']['valid'] is True for t in result['trials'])
        assert not result['automatic_promotion']
        assert not result['runtime']['effective_settings_verified']
        assert result['inputs_unchanged']
