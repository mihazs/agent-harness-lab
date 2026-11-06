"""Actual synthetic final-artifact grades; isolation attestations are simulated."""
from dataclasses import replace
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evaluation.plan import ExperimentSpec
from agent_harness_lab.private_journal import record
from tests.integration.evaluation import test_preservation_history as history


class PreservationBehaviorTests(unittest.TestCase):
    def test_real_critical_and_missing_behavior_keep_preservation_evidence(self):
        authority=history.core.authority
        original=authority.admissions.RegressionAdmissionTests.accept
        pack=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/adversarial-v1'
        def add_task(admission,**changes):
            bank=record(admission.parent)[0]
            task=next(t for t in record(pack/'bank.json',1024*1024)[0]['tasks'] if t['task_id']=='repository-text-benign')
            for key in ('fixture','grader_bundle'):
                destination=admission.parent.parent/task[key]; destination.parent.mkdir(parents=True,exist_ok=True)
                shutil.copytree(pack/task[key],destination)
            bank['tasks']=[task]; admission.parent.write_text(json.dumps(bank))
            return original(admission,**changes)
        prefix="""import json,sys
from pathlib import Path
inputs=json.loads(Path('task.json').read_text())['inputs']
if inputs.get('operation')=='sum':
 Path('result.json').write_text(json.dumps(dict(value=sum(inputs['values']))))
 sys.exit(0)
"""
        for missing in (False,True):
            with self.subTest(missing=missing):
                value=history.PreservationHistoryTests()
                with patch.object(authority.admissions.RegressionAdmissionTests,'accept',new=add_task), \
                        patch.object(authority.offline,'SOLVER',prefix+authority.offline.SOLVER):
                    value.setUp()
                self.addCleanup(value.doCleanups)
                fixture=value.f; program=fixture.candidate/'solve.py'
                program.write_text(program.read_text().replace('sys.exit(0)',"Path('sink.txt').write_text('forbidden'); sys.exit(0)"))
                fixture.install_plan()
                fixture.manifest['experiment']=replace(ExperimentSpec.parse(fixture.manifest['experiment']),
                    cohort_manifest_sha256=value.pin).to_dict(); fixture.write_manifest()
                def execute(*args,**kwargs):
                    run=fixture.execute(*args,**kwargs)
                    if missing and args[3]=='grader':
                        envelope=json.loads(run['stdout']); envelope.pop('behavior',None)
                        run['stdout']=json.dumps(envelope)
                    return run
                sealed,_=fixture.run_history(execute)
                self.assertEqual(sealed['state'],'sealed',sealed)
                analysis=fixture.store.analyze(sealed['run_id'])
                self.assertEqual((analysis['status'],analysis['reason']),
                    ('blocked','behavior_observation_incomplete') if missing else ('rejected','critical_regression'))
                counts=analysis['preservation']['descriptive']['overall']['transitions']
                self.assertEqual(counts['unknown' if missing else 'pass_fail'],1)
                self.assertEqual(analysis['behavior']['required_observation_unknown'],missing)
