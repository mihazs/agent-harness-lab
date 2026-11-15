"""A synthetic project, a pinned starter-bank copy and a scripted stub agent for `ahl improve` loop runs (#814). Nothing here is real data.

The stub agent answers both calls the loop makes. A trial passes the tasks whose input kind the harness lists on its first line
(AGENTS.md: `solves=pairs,settings,...`), so a score is a function of the harness and the bank. A preparation call reads its
step from a script file keyed by the call index, so a test decides what each round proposes: a harness text, no change, a crash,
an invalid proposal, or a change to a frozen bank fixture between the baseline evaluation and the candidate evaluation.
"""
import contextlib
import hashlib
import io
import json
import shutil

from agent_harness_lab import config
from agent_harness_lab.cli import main
from tests.integration.evaluation.test_offline_evaluation import SOLVER

KINDS = ('pairs', 'settings', 'responses', 'values', 'requests', 'queue', 'text')  # the six scoring tasks of the starter bank (five roots) and its held-out one, which only a single pass runs
EVERYTHING = 'solves=' + ','.join(KINDS)
NOTHING = 'solves='
AGENT = '''#!/usr/bin/env python3
import json, pathlib, sys
p = pathlib.Path
if '--version' in sys.argv: print('loop-stub-agent'); raise SystemExit(0)
sys.stdin.read()
SCRIPT = json.loads(p(@SCRIPT@).read_text())
KINDS = @KINDS@
if p('context.json').exists():
 c = json.loads(p('context.json').read_text()); assert c['phase'] == 'candidate'
 step = SCRIPT.get(str(c['attempt']), {}); candidate = p(c['candidate']); proposal = p(c['evaluation'])/'proposal.json'
 with open(@CALLS@, 'a') as calls: calls.write(json.dumps(dict(attempt=c['attempt'], mechanism=c.get('mechanism'))) + chr(10))
 if step.get('crash'): raise SystemExit(3)
 if step.get('tamper_bank'):
  fixtures = p(c['baseline']).parent/'evaluation-0'/'fixtures'; target = fixtures/'localization'/'task.json'  # a development task, so it is one the loop scores
  target.write_text(target.read_text() + ' ')
 if step.get('invalid'): proposal.write_text('not json')
 elif 'harness' in step:
  (candidate/'AGENTS.md').write_text(step['harness'])
  proposal.write_text(json.dumps(dict(status='candidate', mechanisms=['synthetic'], falsifiers=['artifact'])))
 else: proposal.write_text(json.dumps(dict(status='no_change', reason='synthetic')))
else:
 harness = p('AGENTS.md').read_text()
 if 'crash' in harness: raise SystemExit(3)
 inputs = json.loads(p('task.json').read_text())['inputs']
 kind = next(name for name in KINDS if name in inputs)
 listed = harness.splitlines()[0].split('=', 1)[1].split(',')
 if kind in listed: exec(@SOLVER@)
 else: p('result.json').write_text('{}')
print(json.dumps(dict(type='turn.completed', usage=dict(output_tokens=1))))
'''


class LoopProject:
    """A project with a pinned bank, a codex-style stub runtime and a baseline harness, wired to the real `ahl improve`.

    baseline is the first line of AGENTS.md; script maps a preparation call (0, 1, ...) to its step; trials is the configured
    shared trial ceiling and loop the project's `loop` declaration (None for none).
    """
    def __init__(self, stack, monkeypatch, *, baseline=NOTHING, script=None, trials=60, loop=None, pinned=True):
        from tests.integration.workflow.test_workflow import WorkflowTests
        self.fixture = WorkflowTests(stack.callback)
        monkeypatch.setenv('CODEX_HOME', str(self.fixture.root/'codex-home'))
        self.project, self.bank = self.fixture.project, self.fixture.project/'task-bank/bank.json'
        self.script_path, self.calls_path = self.fixture.root/'script.json', self.fixture.root/'calls.jsonl'
        shutil.copytree(config.starter().parent, self.bank.parent)
        declared = json.loads(self.bank.read_text())
        for task in declared['tasks']:
            task['partition'] = {'tool-recovery': 'calibration', 'adversarial-input': 'certification'}.get(task['task_id'], 'development')
        self.bank.write_text(json.dumps(declared))
        self.value = config.prepare(dict(profile='cooperative', runtime=dict(name='codex', executable=str(self.fixture.agent)),
                                         bank=dict(path='task-bank/bank.json', sha256=hashlib.sha256(self.bank.read_bytes()).hexdigest()),
                                         harness_paths=['AGENTS.md'], limits=dict(trials=trials)))
        if not pinned: self.value['bank'] = config.defaults()['bank']  # the starter bank is not a pinned, frozen one
        if loop is not None: self.value['loop'] = loop  # config.prepare takes the declared settings only; validate() accepts the loop object
        self.fixture.agent.write_text(AGENT.replace('@SCRIPT@', repr(str(self.script_path))).replace('@CALLS@', repr(str(self.calls_path)))
                                      .replace('@KINDS@', repr(KINDS)).replace('@SOLVER@', repr(SOLVER)))
        (self.project/'AGENTS.md').write_text(baseline + '\n')
        config.create(self.project, self.value)
        self.write(script or {})

    def write(self, script):
        self.script_path.write_text(json.dumps({str(call): step for call, step in script.items()}))

    def calls(self):
        return [json.loads(line) for line in self.calls_path.read_text().splitlines()] if self.calls_path.exists() else []

    def invoke(self, *arguments, entry=('improve',)):
        """(exit code, the JSON receipt on stdout or None, stderr) of one command; a typed usage exit raises SystemExit inside main."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try: code = main([*entry, str(self.project), *arguments])
            except SystemExit as stop: code = stop.code
        try: receipt = json.loads(out.getvalue())
        except ValueError: receipt = None
        return code, receipt, err.getvalue()

    def run(self, *arguments, entry=('improve',)):
        code, receipt, _ = self.invoke(*arguments, entry=entry)
        return code, receipt
