"""Owned bank controls: synthetic fixtures and opt-in actual kernel execution."""
from copy import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from agent_harness_lab.evaluation import bank_qualification_controls as controls
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.sandbox import SandboxBackend
from tests.integration.evaluation.test_regression_controls import make_draft
from tests.integration.sandbox import test_sandbox_kernel as kernel


def make_bank(root):
    root.mkdir()
    _,draft=make_draft(root)
    task=json.loads(draft.read_bytes())['task']
    bundle=root/task['grader_bundle']
    (bundle/'controls.json').write_text(json.dumps([dict(state=state,fixture='controls/'+state) for state in controls.STATES]))
    task.update(partition='certification',grader_digest=digest(bundle))
    bank=root/'bank.json'
    bank.write_text(json.dumps(dict(schema_version=1,separate_by=['root_id'],tasks=[task])))
    return bank


class BankControlBoundaryTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root=Path(folder.name);self.bank=make_bank(self.root/'bank')

    def test_all_five_states_and_inventory_are_required(self):
        bound=controls.inventory(self.bank)
        self.assertEqual({c['state'] for c in bound['tasks'][0]['controls']},set(controls.STATES))
        bundle=self.bank.parent/'oracle'
        value=json.loads((bundle/'controls.json').read_bytes());value.pop()
        (bundle/'controls.json').write_text(json.dumps(value))
        bank=json.loads(self.bank.read_bytes());bank['tasks'][0]['grader_digest']=digest(bundle)
        self.bank.write_text(json.dumps(bank))
        with self.assertRaisesRegex(ValueError,'five'):controls.inventory(self.bank)

    def test_root_and_nested_directory_modes_are_bound(self):
        before=controls.inventory(self.bank)
        root=self.bank.parent;original=root.stat().st_mode & 0o777
        root.chmod(0o700 if original!=0o700 else 0o750)
        self.assertNotEqual(controls.inventory(self.bank),before)
        root.chmod(original)
        nested=root/'oracle/controls';original=nested.stat().st_mode & 0o777
        nested.chmod(0o700 if original!=0o700 else 0o750)
        self.assertNotEqual(controls.inventory(self.bank),before)
        nested.chmod(0o2700)
        with self.assertRaisesRegex(ValueError,'special'):controls.directory_modes(root)
        nested.chmod(original)
        self.assertEqual(controls.inventory(self.bank),before)

    def test_simulated_backend_and_serialized_success_cannot_issue(self):
        with self.assertRaisesRegex(ValueError,'actual fully pinned'):
            controls.audit(self.bank,self.root/'audit',SimpleNamespace(),time.monotonic()+10)
        with self.assertRaisesRegex(ValueError,'actual owned'):
            controls.consume({'status':'owned_controls_passed'},self.bank)
        self.assertFalse((self.root/'audit').exists())


@unittest.skipUnless(kernel.ENABLED,'explicit delegated kernel profile required')
class OwnedBankControlKernelTests(kernel.UnittestKernelFixture):
    def test_actual_controls_issue_one_process_owned_completion(self):
        bank=make_bank(self.root/'bank')
        runtime=self.root/'runtime';pin=kernel.pinned_runtime(runtime)['sha256']
        backend=SandboxBackend(cgroup_root=self.cgroup_root,image_root=runtime,image_sha256=pin,disk_bytes=64*1024*1024)
        handle,report=controls.audit(bank,self.root/'audit',backend,time.monotonic()+60)
        self.assertEqual(report['status'],'owned_controls_passed')
        self.assertEqual([c['observed'] for c in report['checks']],[False,False,False,True,True,False])
        self.assertTrue(all(len(c['controller']['kernels'])==2 for c in report['checks']))
        with self.assertRaisesRegex(ValueError,'fresh owned'):controls.consume(copy(handle),bank)
        if hasattr(os,'fork'):
            child=os.fork()
            if child==0:
                try:controls.consume(handle,bank)
                except ValueError:os._exit(0)
                except BaseException:os._exit(1)
                os._exit(1)
            _,status=os.waitpid(child,0)
            self.assertEqual(os.waitstatus_to_exitcode(status),0)
        self.assertEqual(controls.consume(handle,bank),report)
        with self.assertRaisesRegex(ValueError,'fresh owned'):controls.consume(handle,bank)
