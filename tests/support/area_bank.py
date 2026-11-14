"""A synthetic two-area task bank: the starter bank's tasks, each tagged with one or two capability areas."""
import json
from pathlib import Path
import shutil

import agent_harness_lab

# Declared project registry for the synthetic bank (ahl.json `areas`).
REGISTRY = [
    dict(id='coding', label='Coding', description='Locate, fix and verify behavior in source files.'),
    dict(id='ux-ui', label='UX/UI', description='Follow interface instructions and recover from tool errors.'),
]
# Task id -> areas. 'instruction-conflict' is counted in both areas; 'resumption' stays untagged.
TAGS = {
    'localization': ['coding'], 'regression': ['coding'], 'adversarial-input': ['coding'],
    'tool-recovery': ['ux-ui'], 'skill-routing': ['ux-ui'], 'instruction-conflict': ['coding', 'ux-ui'],
}


def two_area_bank(root, tags=None):
    """Copy the starter bank under root and tag its tasks; returns the new bank.json path."""
    source = Path(agent_harness_lab.__file__).parent/'evaluation/fixtures/starter-v1'
    shutil.copytree(source, Path(root)/'bank')
    path = Path(root)/'bank/bank.json'
    tags = TAGS if tags is None else tags
    declared = json.loads(path.read_text())
    for task in declared['tasks']:
        if task['task_id'] in tags:
            task['areas'] = tags[task['task_id']]
    path.write_text(json.dumps(declared))
    return path
