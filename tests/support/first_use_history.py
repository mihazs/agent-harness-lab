"""Synthetic recorded sessions and a counting fake runtime for first-use scenarios; no real data."""
import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout

from agent_harness_lab import cli
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
    proposal = dict(status=status, reason='synthetic')
    scripted = pathlib.Path(%r)
    text = None
    if scripted.exists():  # a scripted transcript replaces the default proposal; "edit" changes the candidate's instructions
        script = json.loads(scripted.read_text())
        proposal, text = (None, None) if script['omit'] else (script['proposal'], script['raw'])
        if script['edit']: pathlib.Path(context['candidate'], 'AGENTS.md').open('a').write('synthetic edit\\n')
    if proposal is not None or text is not None:
        pathlib.Path(context['evaluation'], 'proposal.json').write_text(text if text is not None else json.dumps(proposal))
print(json.dumps({'type': 'turn.completed', 'usage': {'output_tokens': 1}}))
'''


class Home:
    """A synthetic project, session stores under tmp_path and a fake runtime that counts its model calls."""

    def __init__(self, root, monkeypatch):
        self.root, self.project = root, root/'project'
        self.project.mkdir()
        (self.project/'AGENTS.md').write_text('synthetic instructions\n')
        self.calls, self.proposal, self.script = root/'model-calls', root/'proposal-status', root/'proposal-script.json'
        self.runtime = root/'agent'
        self.runtime.write_text(RUNTIME % (str(self.calls), str(self.proposal), str(self.proposal), str(self.script)))
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

    def scripts(self, proposal=None, *, edit=False, raw=None, omit=False):
        """The fake preparation agent writes exactly this proposal (``raw`` text instead, or no file with ``omit``).

        ``edit`` also changes the candidate's instructions, so the candidate differs from the baseline."""
        self.script.write_text(json.dumps(dict(proposal=proposal, raw=raw, edit=edit, omit=omit)))

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


def improve(home, *extra):
    """Run ``ahl improve --history`` on the synthetic project; returns the exit code, the stdout receipt and stderr."""
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = cli.main(['improve', str(home.project), '--history', '--agent', 'codex', '--codex', str(home.runtime),
                         '--timeout', '30', *extra])
    return code, json.loads(out.getvalue()), err.getvalue()


def candidates(receipt):
    return len(receipt.get('candidates') or []) + int('plan' in receipt)


def assert_candidate_or_typed_reason(receipt):
    """The first-use rule: an evidenced candidate, or a typed reason with a remediation that explains the findings per cause."""
    if candidates(receipt):
        assert all(row.get('evidence') for row in receipt.get('candidates', []))
        return
    assert isinstance(receipt.get('reason'), str) and receipt['reason'], receipt
    assert receipt.get('remediation'), receipt
    findings = receipt['analysis']['findings'] if 'analysis' in receipt else 0
    if findings:
        explained = receipt['findings_not_candidates']
        assert explained['reason'] == 'findings_not_actionable' and explained['findings'] == findings
        assert explained['causes'] and all(type(count) is int and 0 < count <= findings for count in explained['causes'].values())
