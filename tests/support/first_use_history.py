"""Synthetic recorded sessions and a counting fake runtime for first-use scenarios; no real data."""
import json
import os

from tests.support.claude_order_fixture import session_rows
from agent_harness_lab.telemetry.claude_discovery import directory_names

PINNED_DAY = 1767225600  # 2026-01-01T00:00:00Z; every capture gets a fixed modification time
OUTSIDE_RANGE = '2.1.399'  # newer than any release a verified range could name
RUNTIME = '''#!/usr/bin/env python3
import json, pathlib, sys
if '--version' in sys.argv: print('synthetic-agent'); sys.exit(0)
sys.stdin.read()
pathlib.Path(%r).open('a').write('call\\n')
if pathlib.Path('context.json').exists():
    context = json.loads(pathlib.Path('context.json').read_text())
    status = pathlib.Path(%r).read_text() if pathlib.Path(%r).exists() else 'no_change'  # a candidate is written without editing it
    pathlib.Path(context['evaluation'], 'proposal.json').write_text(json.dumps(dict(status=status, reason='synthetic')))
print(json.dumps({'type': 'turn.completed', 'usage': {'output_tokens': 1}}))
'''


class Home:
    """A synthetic project, session stores under tmp_path and a fake runtime that counts its model calls."""

    def __init__(self, root, monkeypatch):
        self.root, self.project = root, root/'project'
        self.project.mkdir()
        (self.project/'AGENTS.md').write_text('synthetic instructions\n')
        self.calls, self.proposal = root/'model-calls', root/'proposal-status'
        self.runtime = root/'agent'
        self.runtime.write_text(RUNTIME % (str(self.calls), str(self.proposal), str(self.proposal)))
        self.runtime.chmod(0o755)
        for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('AHL_STATE_HOME', 'state')):
            monkeypatch.setenv(name, str(root/folder))
        monkeypatch.setenv('XDG_STATE_HOME', str(root/'unused-xdg'))
        monkeypatch.setenv('HOME', str(root/'home'))
        for marker in ('CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_CI', 'CODEX_SANDBOX', 'CODEX_SANDBOX_NETWORK_DISABLED'):
            monkeypatch.delenv(marker, raising=False)

    def proposes_an_unedited_candidate(self):
        """The fake preparation agent declares a candidate but leaves the candidate identical to the baseline."""
        self.proposal.write_text('candidate')

    @property
    def model_calls(self):
        return len(self.calls.read_text().split()) if self.calls.exists() else 0

    def codex(self, name, *, calls=2, status=1, cwd=None, version=None):
        """A Codex session with ``calls`` tool calls ending in exit status ``status`` (None leaves it unrecorded)."""
        cwd = str(self.project if cwd is None else cwd)
        meta = dict(id=name, cwd=cwd, **(dict(cli_version=version) if version else {}))
        rows = [dict(type='session_meta', cwd=cwd, payload=meta)]
        for number in range(calls):
            output = dict(exit_code=status) if status is not None else {}
            rows += [dict(type='response_item', payload=dict(type='function_call', name='shell', call_id=f'c{number}')),
                     dict(type='response_item', payload=dict(type='function_call_output', call_id=f'c{number}', output=output))]
        path = self.root/'codex/sessions'/f'{name}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        os.utime(path, (PINNED_DAY, PINNED_DAY))

    def claude(self, number, *, version=OUTSIDE_RANGE, project=None):
        """A Claude session whose one failing tool result was recorded by ``version``."""
        project = self.project if project is None else project
        session = f'00000000-0000-4000-8000-00000000{number:04d}'
        rows = [dict(row, version=version) for row in session_rows(project, session)]
        path = self.root/'claude/projects'/directory_names(project)[0]/f'{session}.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        os.utime(path, (PINNED_DAY, PINNED_DAY))

    def ungraded_evaluation(self):
        """A recorded evaluation whose trials carry no valid grader result: its outcomes are unknown, never success or failure."""
        path = self.project/'artifacts/earlier/results.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(dict(inputs_unchanged=True, trials=[dict(task='t', version='baseline', status='failed')])))

    def graded_evaluation(self):
        """A recorded evaluation with a valid executable-grader result: the verified outcome the blocked receipt asks for."""
        path = self.project/'artifacts/earlier/results.json'
        path.parent.mkdir(parents=True)
        grader = dict(exit_code=0, timeout=False, valid=True, grader_id='synthetic', grader_version='1', outcome=True)
        trial = dict(task='t', version='baseline', status='passed', grader=grader, execution=dict(exit_code=0, timeout=False))
        path.write_text(json.dumps(dict(inputs_unchanged=True, trials=[trial])))
