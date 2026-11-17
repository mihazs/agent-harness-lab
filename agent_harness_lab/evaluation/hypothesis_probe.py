"""Content-free inputs for a synthetic hypothesis probe."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys

from agent_harness_lab.diagnostics.brief import BUILT_IN, ERROR_CATEGORIES, NEXT_ACTIONS

PROBE_MARKER = 'ahl-synthetic-probe.json'
PROBE_PROFILE = 'ahl-synthetic-probe-v1'


def is_probe(fixture):
    """Recognize an intact probe marker; an invalid marker is never treated as absence."""
    from agent_harness_lab.private_journal import record
    marker = Path(fixture) / PROBE_MARKER
    if not os.path.lexists(marker): return False
    value, _ = record(marker, 4096)
    if value != {'profile': PROBE_PROFILE}: raise ValueError('invalid synthetic probe marker')
    return True


def structural_pattern(mechanism):
    """Select only closed structural labels; reject patterns that cannot define one follow-up."""
    if not isinstance(mechanism, dict) or mechanism.get('status') != 'observed_pattern':
        raise ValueError('probe requires an observed pattern')
    tool, category, actions = (mechanism.get(name) for name in ('tool_class', 'error_category', 'next_action'))
    if not isinstance(tool, str) or tool not in BUILT_IN or category not in ERROR_CATEGORIES or not isinstance(actions, dict):
        raise ValueError('probe requires known structural labels')
    if len(actions) > len(NEXT_ACTIONS) or set(actions) - set(NEXT_ACTIONS) or any(type(value) is not int or value < 0 for value in actions.values()):
        raise ValueError('invalid follow-up counts')
    highest = max(actions.values(), default=0)
    selected = [name for name, value in actions.items() if value == highest and value > 0]
    if len(selected) != 1: raise ValueError('probe follow-up is ambiguous')
    return dict(tool_class=tool, error_category=category, follow_up=selected[0])


def fixture_files(mechanism):
    """Build a deterministic synthetic recovery task; no source-session content enters it."""
    pattern = structural_pattern(mechanism)
    if pattern['tool_class'] not in ('Bash', 'exec_command', 'shell', 'shell_command') or pattern['follow_up'] != 'same_call_retry':
        raise ValueError('no synthetic fixture for this pattern')
    task = dict(prompt=('Run python3 synthetic_worker.py. If it fails, inspect the synthetic configuration, '
                        'correct it and run it again. Leave config.json and result.json as the final artifacts.'),
                pattern=pattern)
    worker = '''import json
from pathlib import Path
import sys

config = json.loads(Path('config.json').read_text())
if config.get('ready') is not True:
    print(json.dumps({'status': 'error', 'category': %r}))
    raise SystemExit(1)
Path('result.json').write_text(json.dumps({'records': [3, 5]}) + '\\n')
''' % pattern['error_category']
    encode = lambda value: json.dumps(value, sort_keys=True, separators=(',', ':')) + '\n'
    return {'task.json': encode(task), 'config.json': encode({'ready': False}), 'synthetic_worker.py': worker,
            PROBE_MARKER: encode({'profile': PROBE_PROFILE})}


GRADER = '''import hashlib
import json
import os
from pathlib import Path
import stat
import sys

def read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536: raise ValueError('invalid artifact')
        with os.fdopen(fd, 'rb', closefd=False) as stream: return stream.read(65537)
    finally:
        os.close(fd)

def receipt(valid, outcome, **extra):
    print(json.dumps(dict(schema_version=1, grader_id='ahl-synthetic-probe', grader_version='v1', valid=valid, outcome=outcome, **extra)))

try:
    reference = json.loads(read(sys.argv[2]))
    pin = reference['worker_sha256']
    if set(reference) != {'worker_sha256'} or not isinstance(pin, str) or len(pin) != 64 or any(c not in '0123456789abcdef' for c in pin):
        raise ValueError('invalid reference')
except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
    receipt(False, None, error='reference_invalid')
    raise SystemExit(2)
try:
    root = Path(sys.argv[1])
    config, result = (json.loads(read(root / name)) for name in ('config.json', 'result.json'))
    outcome = (type(config) is dict and config.get('ready') is True and type(result) is dict
               and result.get('records') == [3, 5] and all(type(n) is int for n in result['records'])
               and hashlib.sha256(read(root / 'synthetic_worker.py')).hexdigest() == pin
               and json.loads(read(root / 'ahl-synthetic-probe.json')) == {'profile': 'ahl-synthetic-probe-v1'})
except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
    outcome = False
receipt(True, outcome)
raise SystemExit(0 if outcome else 1)
'''


def grader_files(mechanism):
    """Freeze an artifact-only oracle for the generated worker, separate from the fixture."""
    worker = fixture_files(mechanism)['synthetic_worker.py'].encode()
    return {'grade.py.txt': GRADER, 'expected.json': json.dumps({'worker_sha256': hashlib.sha256(worker).hexdigest()}, sort_keys=True) + '\n'}


def materialize(mechanism, output, *, project, harness_paths):
    """Create one private development-only probe manifest, outside the project and without overwriting."""
    from agent_harness_lab.evaluation.integrity import digest, open_directory_nofollow
    from agent_harness_lab.evaluation.workspace import names
    from agent_harness_lab.private_journal import encoded, write_bytes
    from agent_harness_lab.read_only import require_writable

    fixture, grader = fixture_files(mechanism), grader_files(mechanism)
    if not isinstance(harness_paths, list) or list(names(harness_paths)) != harness_paths:
        raise ValueError('safe harness paths required')
    output, project = Path(output).absolute(), Path(project).resolve(strict=True)
    if '..' in output.parts or output.is_relative_to(project) or project.is_relative_to(output):
        raise ValueError('probe output must not overlap project')
    require_writable(output)
    parent = open_directory_nofollow(output.parent)
    try: os.mkdir(output.name, 0o700, dir_fd=parent)
    finally: os.close(parent)
    fixture_dir, grader_dir = output / 'fixture', output / 'grader'
    fixture_dir.mkdir(mode=0o700); grader_dir.mkdir(mode=0o700)
    for name, content in fixture.items(): write_bytes(fixture_dir / name, content.encode())
    for name, content in grader.items(): write_bytes(grader_dir / name, content.encode())
    controls = []
    for state in ('broken', 'partial', 'complete'):
        folder = grader_dir / 'controls' / state
        folder.mkdir(mode=0o700, parents=True)
        values = dict(fixture)
        if state != 'broken': values['config.json'] = '{"ready": true}\n'
        if state == 'complete': values['result.json'] = '{"records": [3, 5]}\n'
        for name, content in values.items(): write_bytes(folder / name, content.encode())
        controls.append(dict(task='hypothesis-probe', state=state, fixture=str(folder.relative_to(output)), digest=digest(folder)))
    task = json.loads(fixture['task.json'])
    manifest = dict(schema_version=1, profile='descriptive_smoke', skills=[], harness_paths=harness_paths,
        tasks=[dict(id='hypothesis-probe', root='hypothesis-probe', split='development', fixture='fixture', prompt=task['prompt'],
                    artifacts=['config.json', 'result.json', 'synthetic_worker.py', PROBE_MARKER],
                    grader=[sys.executable, str(grader_dir / 'grade.py.txt'), '{workspace}', str(grader_dir / 'expected.json')])],
        grader_self_tests=controls, grader_bundles={'hypothesis-probe': dict(path='grader', digest=digest(grader_dir))},
        max_trials=2, budget_seconds=60, timeout_seconds=20, no_nested_agents=True,
        probe=dict(profile='ahl-synthetic-probe-v1', source_session_outcome='unknown', heldout_allowed=False, automatic_promotion=False))
    write_bytes(output / 'manifest.json', encoded(manifest))
    return output / 'manifest.json'


def _paired_result(result, *, inputs_unchanged):
    """Summarize one verified synthetic pair; malformed or missing grading is unknown."""
    verdict = 'inconclusive'
    if inputs_unchanged is True and isinstance(result, dict) and result.get('inputs_unchanged') is True:
        trials = result.get('trials')
        if isinstance(trials, list) and len(trials) == 2 and all(isinstance(row, dict) for row in trials):
            rows = {row.get('version'): row for row in trials if row.get('version') in ('baseline', 'candidate')}
            if len(rows) == 2:
                outcomes = []
                for arm in ('baseline', 'candidate'):
                    row = rows[arm]; grade = row.get('grader')
                    outcome = grade.get('outcome') if isinstance(grade, dict) and grade.get('valid') is True else None
                    if (row.get('task') != 'hypothesis-probe' or row.get('split') != 'development'
                            or type(outcome) is not bool or row.get('status') != ('passed' if outcome else 'failed')):
                        break
                    outcomes.append(outcome)
                if outcomes == [False, True]: verdict = 'supported_hypothesis'
                elif outcomes == [True, False]: verdict = 'refuted_hypothesis'
    return dict(profile='ahl-synthetic-probe-result-v1', verdict=verdict, scope='one_synthetic_pair',
                independence='unverified', source_session_outcome='unknown', automatic_promotion=False, publication_allowed=False)


def _runtime_digest(path):
    from agent_harness_lab.evaluation.integrity import open_nofollow
    fd = open_nofollow(path)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode): raise ValueError('runtime must be a regular file')
        value = hashlib.sha256()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''): value.update(chunk)
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError('runtime changed while hashing')
        return value.hexdigest()
    finally:
        os.close(fd)


def run(mechanism, *, project, baseline, candidate, output, agent, binary, harness_paths):
    """Run both snapshots through the existing executor and report only a frozen synthetic pair."""
    from agent_harness_lab.cli import evaluator
    from agent_harness_lab.evaluation.integrity import digest, read_regular
    from agent_harness_lab.loop.adapters import ExecutorRunner
    from agent_harness_lab.private_journal import encoded, write_bytes

    if agent not in ('codex', 'claude') or not isinstance(binary, str) or not binary:
        raise ValueError('explicit cooperative agent and executable required')
    executable = shutil.which(binary)
    if executable is None: raise ValueError('runtime executable unavailable')
    executable = Path(executable).resolve(strict=True)
    roots = {name: Path(path).resolve(strict=True) for name, path in (('baseline', baseline), ('candidate', candidate))}
    output = Path(output).absolute()
    if any(output.is_relative_to(root) or root.is_relative_to(output) for root in roots.values()):
        raise ValueError('probe output must not overlap either snapshot')
    snapshots = {name: digest(path) for name, path in roots.items()}
    if snapshots['baseline'] == snapshots['candidate']: raise ValueError('probe requires distinct snapshots')
    runtime = _runtime_digest(executable)
    manifest = materialize(mechanism, output, project=project, harness_paths=harness_paths)
    def frozen():
        return dict(snapshots={name: digest(path) for name, path in roots.items()}, runtime=_runtime_digest(executable),
                    fixture=digest(output / 'fixture'), grader=digest(output / 'grader'),
                    manifest=hashlib.sha256(read_regular(manifest, 1024 * 1024)).hexdigest())
    before = frozen()
    if before['snapshots'] != snapshots or before['runtime'] != runtime: raise ValueError('probe inputs changed before execution')
    runner = ExecutorRunner(evaluator().evaluate, agent, str(executable), inherit_env=False)
    result = runner.run(('baseline', 'candidate'), roots, manifest, output / 'evaluation')
    try: unchanged = frozen() == before
    except (OSError, ValueError): unchanged = False
    summary = _paired_result(result, inputs_unchanged=unchanged)
    write_bytes(output / 'probe-result.json', encoded(summary))
    return summary
