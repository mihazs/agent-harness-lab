"""Shared synthetic controller instruction fixture."""
from copy import deepcopy
import os
from pathlib import Path
import time
from types import SimpleNamespace

import agent_harness_lab
from agent_harness_lab.evaluation import responses
from tests.integration.sandbox import test_sandbox_inference as wire

PROTOCOL = 'ahl-controlled-agents-v1'


class InstructionFixture:
    def __init__(self, root):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected: assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
        self.root = root
        self.harness=self.root/'harness';self.harness.mkdir();(self.harness/'AGENTS.md').write_text('SYNTHETIC_GUIDANCE')
        self.work=self.root/'work';self.work.mkdir();self.deadline=time.monotonic()+10
        self.settings=dict(schema_version=1,model='synthetic',credential_file=str(self.root/'unused'),max_requests=3,
            max_tool_calls=3,max_output_tokens=100,max_request_bytes=32768,max_response_bytes=16384,instruction_protocol=PROTOCOL)
        self.receipts=[];self.bodies=[]
        self.backend=SimpleNamespace(image=SimpleNamespace(sha256='a'*64))

    def client(self,transport=None):
        def final(*args):
            self.bodies.append(args[1]);return wire.reply([wire.message(str(len(self.bodies)))])
        return responses.Client(self.settings,None,self.deadline,transport=transport or final,
            record=lambda value:self.receipts.append(deepcopy(value)))

    def run_role(self,client):
        return responses.run_role(client,self.backend,self.work,{'harness':self.harness},'executor',
            'Synthetic controller executor.','Synthetic task.',self.deadline,trial_id='synthetic-trial')


    def run_cwd(self, client, task_cwd):
        return responses.run_role(client, self.backend, self.work, {'harness': self.harness}, 'executor',
                                  'Synthetic controller executor.', 'Synthetic task.', self.deadline,
                                  trial_id='synthetic-trial', task_cwd=task_cwd)
