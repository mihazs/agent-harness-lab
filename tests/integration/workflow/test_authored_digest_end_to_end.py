"""Real `ahl improve` entry with a stub runtime on PATH: authored digests never reach an integrity check."""
import json
import os
import stat
import sys

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.integrity import digest
from tests.integration.diagnostics.test_reported_actionable_analysis import history

# One stub, two steps: the proposer's cwd holds a candidate-phase context.json and the construction session's holds an
# evaluation-phase one. The construction step writes a tiny synthetic evaluation whose declarations carry WRONG digests.
STUB = r'''#!{python}
import json, pathlib, sys
p = pathlib.Path
if '--version' in sys.argv: print('stub-runtime'); sys.exit(0)
prompt = sys.stdin.read()
mode = p({mode!r}).read_text().strip() if p({mode!r}).exists() else 'digests'
if p('context.json').exists():
    c = json.loads(p('context.json').read_text()); evaluation = p(c['evaluation'])
    if c['phase'] == 'candidate':
        (p(c['candidate']) / 'AGENTS.md').write_text('good\n')
        (evaluation / 'proposal.json').write_text(json.dumps({{'status': 'candidate', 'mechanisms': ['synthetic'], 'falsifiers': ['artifact check']}}))
    else:
        tasks, controls, bundles = [], [], {{}}
        for split in ('development', 'regression', 'heldout'):
            fixture = evaluation / split; fixture.mkdir(); (fixture / 'seed').write_text(c['harness_paths'][0] + split + str(c['attempt']))
            bundle = evaluation / ('oracle-' + split); bundle.mkdir()
            (bundle / 'helper.py').write_text("def passed(workspace):\n    return (workspace / 'artifact').read_text() == 'done'\n")
            note = "import pathlib, sys\nopen(pathlib.Path(sys.argv[0]).parent / 'note', 'w').write('x')\n" if mode == 'writes-bundle' else ''
            (bundle / 'grade.py.txt').write_text(note + (
                "import json, pathlib, sys\n"
                + ("ok = (pathlib.Path(sys.argv[1]) / 'artifact').read_text() == 'done'\n" if mode.endswith('-plain') else
                   "sys.path.insert(0, str(pathlib.Path(sys.argv[0]).parent))\nimport helper\nok = helper.passed(pathlib.Path(sys.argv[1]))\n") +
                "print(json.dumps(dict(schema_version=1, valid=True, outcome=ok, grader_id='synthetic', grader_version='1')))\n"
                "sys.exit(0 if ok else 1)\n"))
            (bundle / 'expected.json').write_text('{{}}')
            tasks.append(dict(id=split, split=split, fixture=split, prompt='Produce the synthetic artifact', artifacts=['artifact'],
                grader=[sys.executable, str(bundle / 'grade.py.txt'), '{{workspace}}', str(bundle / 'expected.json')]))
            bundles[split] = dict(path='oracle-' + split, digest='0' * 64) if mode.startswith('digests') else dict(path='oracle-' + split)
            for state in ('broken', 'partial', 'complete'):
                control = evaluation / (split + '-' + state); control.mkdir()
                (control / 'artifact').write_text('done' if state == 'complete' else 'wrong')
                controls.append(dict(task=split, state=state, fixture=control.name, **({{'digest': 'f' * 64}} if mode.startswith('digests') else {{}})))
        (evaluation / 'manifest.json').write_text(json.dumps(dict(harness_paths=c['harness_paths'], skills=[], tasks=tasks,
            grader_self_tests=controls, grader_bundles=bundles)))
else:
    good = p('AGENTS.md').read_text().strip() == 'good'
    p('artifact').write_text('done' if good or 'regression' in p('seed').read_text() else 'wrong')
print(json.dumps({{'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {{'output_tokens': 1}}}}
                 if p(sys.argv[0]).name == 'claude' else {{'type': 'turn.completed', 'usage': {{'output_tokens': 1}}}}))
'''


class Stub:
    """A temporary project, temporary state directories and a stub runtime named like the real one, first on PATH."""

    def __init__(self, root, monkeypatch, runtime):
        self.root, self.runtime = root, runtime
        self.project, self.bin = root / 'project', root / 'bin'
        self.project.mkdir(); self.bin.mkdir()
        (self.project / 'AGENTS.md').write_text('bad\n')
        self.mode = root / 'stub-mode'
        script = self.bin / runtime
        script.write_text(STUB.format(python=sys.executable, mode=str(self.mode)))
        script.chmod(script.stat().st_mode | stat.S_IXUSR)
        (self.bin / 'python3').symlink_to(sys.executable)
        self.events = root / 'events.jsonl'
        self.events.write_text(''.join(json.dumps(row) + '\n' for row in history(3)))
        (root / 'home').mkdir()
        for name, value in dict(PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}', HOME=str(root / 'home'),
                                XDG_STATE_HOME=str(root / 'xdg'), AHL_STATE_HOME=str(root / 'state'),
                                CODEX_HOME=str(root / 'codex'), CLAUDE_CONFIG_DIR=str(root / 'claude')).items():
            monkeypatch.setenv(name, value)

    def improve(self, capsys, mode, *extra, events=True):
        """Run the real CLI entry; return (exit code, the private workflow receipt, the run directory)."""
        self.mode.write_text(mode)
        try:
            code = main(['improve', str(self.project), *(['--events', str(self.events)] if events else []),
                         '--agent', self.runtime, '--timeout', '120', *extra])
        except SystemExit as stop:
            code = stop.code
        capsys.readouterr()
        run = next((self.root / 'state/runs').glob('*/run-*'))
        return code, json.loads((run / 'workflow.json').read_text()), run


@pytest.fixture(params=['codex', 'claude'])
def stub(tmp_path, monkeypatch, request):
    return Stub(tmp_path, monkeypatch, request.param)


def frozen(run, attempt=0):
    return json.loads((run / f'preparation/evaluation-{attempt}/manifest.json').read_text())


@pytest.mark.parametrize('mode', ['digests-plain', 'digests', 'omitted'])  # plain: no imported helper; the others import one
def test_wrong_authored_digests_are_ignored_and_counted_by_the_real_cli(stub, capsys, mode):
    code, receipt, run = stub.improve(capsys, mode)
    assert (code, receipt['status']) == (3, 'inconclusive'), receipt
    attempts = len(list(run.glob('preparation/evaluation-?')))
    assert receipt['authored_digest_ignored'] == (12 * attempts if mode.startswith('digests') else 0)  # 3 bundles + 9 controls per construction
    assert isinstance(receipt['evaluation_construction_seconds'], float)
    evaluation = run / 'preparation/evaluation-0'
    manifest = frozen(run)
    assert all(b['digest'] == digest(evaluation / b['path']) != '0' * 64 for b in manifest['grader_bundles'].values())
    assert all(c['digest'] == digest(evaluation / c['fixture']) != 'f' * 64 for c in manifest['grader_self_tests'])
    assert not list(evaluation.rglob('__pycache__'))  # an importing grader never dirties its frozen bundle


def test_a_grader_that_writes_into_its_frozen_bundle_ends_with_a_typed_reason_and_a_full_receipt(stub, capsys):
    code, receipt, run = stub.improve(capsys, 'writes-bundle')
    assert (code, receipt['status'], receipt['reason']) == (2, 'invalid_input', 'grader_input_changed_after_freeze'), receipt
    violation = receipt['contract_violation']
    assert (violation['stage'], violation['field'], violation['origin']) == ('manifest_validation', 'grader_bundles', 'agent_output_defect')
    assert 'integrity mismatch' not in json.dumps(receipt) and violation['next_step']
    assert receipt['authored_digest_ignored'] == 0 and isinstance(receipt['evaluation_construction_seconds'], float)
    assert not (run / 'evaluation-0').exists()  # found before any trial ran


def test_a_blocked_result_before_any_construction_still_carries_the_construction_facts(stub, capsys):
    code, receipt, _ = stub.improve(capsys, 'omitted', '--history', events=False)  # no captured sessions: blocked before preparation
    assert (code, receipt['status']) == (3, 'blocked'), receipt
    assert (receipt['authored_digest_ignored'], receipt['evaluation_construction_seconds']) == (0, 0.0)
