"""Opt-in regressions over existing private runs; never create fixture histories."""
import hashlib
import inspect
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

import pytest

from agent_harness_lab.diagnostics.improve_coverage import citable
from agent_harness_lab.evaluation.construction import views
from agent_harness_lab.evaluation.integrity import relative
from agent_harness_lab.loop.improve import mechanisms
from agent_harness_lab.monitoring.state import exit_code
from agent_harness_lab.workflow import file_state, harness_files


HOST_HOME = Path.home()
RUNS = json.loads(os.environ.get('REAL_IMPROVE_RUNS', '[]'))
CODE_ROOT = Path(os.environ.get('REAL_IMPROVE_CODE_ROOT', Path(__file__).resolve().parents[3])).resolve()


@pytest.fixture(params=RUNS or [None], ids=lambda _: 'private-run')
def real_run(request):
    if request.param is None:
        pytest.skip('set REAL_IMPROVE_RUNS to existing private run directories')
    run = Path(request.param).resolve(strict=True)
    checkpoint = json.loads((run/'recovery.json').read_text())
    project = Path(checkpoint['project'])
    if not project.is_absolute() or project.resolve(strict=True) != project or run.parent.parent.name != 'runs':
        raise ValueError('private run must name a canonical project and run directory')
    worktrees = HOST_HOME/'dev/.worktrees'
    worktrees.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ahl-real-regression-', dir=worktrees) as folder:
        base = Path(folder)
        checkout = base/'project'
        git = ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'index.threads=1', '-C', str(project)]
        added = False
        try:
            subprocess.run([*git, 'worktree', 'add', '-q', '--detach', str(checkout), 'HEAD'], check=True)
            added = True
            allowed, expected = checkpoint['allowed_paths'], checkpoint['original']
            for name in [*expected, *(allowed or [])]:
                if relative(name) != name:
                    raise ValueError('noncanonical harness path')
            for path in harness_files(checkout, allowed):
                if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(checkout)):
                    raise ValueError('worktree harness path crosses a symlink')
                if str(path.relative_to(checkout)) not in expected:
                    path.unlink()
            for name in expected:
                target = checkout/name
                source = project/name
                if any(path.is_symlink() for root, leaf in ((project, source), (checkout, target))
                       for path in (leaf, *leaf.parents) if path.is_relative_to(root)):
                    raise ValueError('harness path crosses a symlink')
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            def states(root):
                return {str(path.relative_to(root)):list(file_state(path)) for path in harness_files(root, allowed)}
            assert states(project) == states(checkout) == expected
            yield SimpleNamespace(run=run, project=project, checkout=checkout, base=base, states=states, expected=expected)
            assert states(project) == states(checkout) == expected
        finally:
            if added:
                subprocess.run([*git, 'worktree', 'remove', '--force', str(checkout)], check=True)


def test_real_mechanism_summaries_reach_only_the_proposer(real_run):
    run, base = real_run.run, real_run.base
    data = json.loads((run/'analysis.json').read_text())
    brief = json.loads((run/'analysis-brief.json').read_text())
    summaries = json.loads((run/'mechanism-summaries.json').read_text())
    options = dict(brief=brief)
    if 'mechanisms' in inspect.signature(views).parameters:
        options['mechanisms'] = summaries
    role = views(data, CODE_ROOT, base/'roles', time.monotonic()+120, **options)
    private = role['proposer']/'mechanism-summaries.json'
    assert private.is_file()
    assert json.loads(private.read_text()) == summaries and private.stat().st_mode & 0o077 == 0
    assert not (role['evaluator_author']/'mechanism-summaries.json').exists()
    from agent_harness_lab.diagnostics.brief import no_change_result
    disposition = no_change_result(data)
    assert (disposition['status'], disposition['reason'], exit_code(disposition['status'])) == (
        'inconclusive', 'no_change_evidence_insufficient', 3)
    ids = {row['mechanism_id'] for row in summaries['mechanisms']}
    assert ids - citable(brief)
    assert ids <= citable(brief, summaries)
    assert ids <= set(mechanisms(SimpleNamespace(_analysis_brief=brief, _mechanism_summaries=summaries, focus=None)))
    from agent_harness_lab.workflow import check_mechanism_summaries
    copied = base/'mechanism-summaries.json'
    shutil.copy2(run/'mechanism-summaries.json', copied)
    args = SimpleNamespace(_mechanism_summaries_digest=hashlib.sha256(copied.read_bytes()).hexdigest())
    check_mechanism_summaries(args, base)
    with copied.open('ab') as stream:
        stream.write(b' ')
    with pytest.raises(ValueError, match='changed during preparation'):
        check_mechanism_summaries(args, base)


@pytest.mark.parametrize('entry', [('improve',), ('skills', 'invoke')], ids=['cli', 'skill'])
def test_real_no_change_receipt_resumes_as_inconclusive(real_run, entry):
    run, base = real_run.run, real_run.base
    state_root = run.parents[2]
    destination = base/'state'/run.relative_to(state_root)
    (destination/'preparation/proposal-0').mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    shutil.copy2(run/'recovery.json', destination/'recovery.json')
    shutil.copy2(run/'preparation/proposal-0/proposal.json', destination/'preparation/proposal-0/proposal.json')
    environment = dict(os.environ, AHL_STATE_HOME=str(state_root), PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(CODE_ROOT))
    command = ['unshare', '--user', '--map-root-user', '--mount', 'bash', '-c',
        'set -eu; mount --make-rprivate /; mount --bind "$1" "$2"; mount --bind "$3" "$4"; '
        'exec "$5" -m agent_harness_lab "${@:7}" "$4" --resume "$6"',
        '_', str(base/'state'), str(state_root), str(real_run.checkout), str(real_run.project), sys.executable, run.name, *entry]
    completed = subprocess.run(command, env=environment, cwd=base, capture_output=True, text=True, timeout=30)
    result = json.loads(completed.stdout)
    assert completed.returncode == 3
    assert (result['status'], result['reason']) == ('inconclusive', 'no_change_evidence_insufficient')
    assert result['changed_files'] == [] and result['exit_receipt']
