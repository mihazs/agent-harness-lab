"""Controller-profile checks must bind the same manifest bytes they validate."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from agent_harness_lab.evaluation import task_bank
if __package__:
    from . import test_regression_admission as fixtures
else:
    import test_regression_admission as fixtures


class AdmissionProfileTests(unittest.TestCase):
    def test_changing_profile_between_validation_reads_cannot_bypass_acceptance(self):
        f = fixtures.RegressionAdmissionTests(); f.setUp(); self.addCleanup(f.doCleanups)
        result = f.accept(); bank = f.bank(result); validate = task_bank._validate
        def change(path, **kwargs):
            checked = validate(path, **kwargs)
            value = json.loads(bank.read_text()); value.pop('controller_profile'); bank.write_text(json.dumps(value))
            return checked
        with patch.object(task_bank,'_validate',side_effect=change):
            with self.assertRaises(ValueError): task_bank.validate(bank)

    @unittest.skipUnless(os.environ.get('AHL_ADMISSION_BASELINE_ROOT'), 'requires explicit frozen admission baseline')
    def test_frozen_baseline_preserves_legacy_validation_and_export(self):
        import agent_harness_lab
        baseline = Path(os.environ['AHL_ADMISSION_BASELINE_ROOT']).resolve()
        candidate = Path(agent_harness_lab.__file__).resolve().parent.parent
        self.assertTrue((baseline/'agent_harness_lab/evaluation/task_bank.py').is_file())
        script = '''import hashlib,json,sys
from pathlib import Path
import agent_harness_lab
from agent_harness_lab.evaluation.bank_tools import export_manifest
from agent_harness_lab.evaluation.task_bank import validate
bank=Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1/bank.json'
output=Path(sys.argv[1]); manifest=export_manifest(bank,output)
for task in manifest['tasks']:
 task['grader']=[arg.replace(str(output),'<export>') for arg in task['grader']]
files={str(p.relative_to(output)):hashlib.sha256(p.read_bytes()).hexdigest()
 for p in output.rglob('*') if p.is_file() and p.name!='manifest.json'}
print(json.dumps(dict(validation=validate(bank),manifest=manifest,files=files),sort_keys=True))
'''
        results = []
        with tempfile.TemporaryDirectory() as folder:
            for name, root in (('baseline',baseline),('candidate',candidate)):
                run = subprocess.run([sys.executable,'-c',script,str(Path(folder)/name)],cwd=root,
                    env={**os.environ,'PYTHONPATH':str(root)},capture_output=True,text=True,timeout=30)
                self.assertEqual(run.returncode,0,run.stderr)
                results.append(json.loads(run.stdout))
        self.assertEqual(results[0],results[1])
