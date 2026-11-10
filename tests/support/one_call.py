"""Shared synthetic one-call project."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main


class OneCallProject:
    def __init__(self, root):
        self.root = root
        self.project = root/'project'; self.project.mkdir()
        self.skill = self.project/'.agents/skills/example/SKILL.md'
        self.skill.parent.mkdir(parents=True)
        self.valid = b'---\nname: example\ndescription: Use for synthetic checks.\n---\nKeep the body intact.\n'
        self.skill.write_bytes(b'\xef\xbb\xbf'+self.valid)
        self.skill.chmod(0o640)

    def invoke(self, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(['improve', str(self.project), *args])
        return code, json.loads(output.getvalue())


@pytest.fixture
def one():
    with TemporaryDirectory() as folder:
        root = Path(folder)
        with patch('agent_harness_lab.workflow.state_root', return_value=root/'state'):
            yield OneCallProject(root)
