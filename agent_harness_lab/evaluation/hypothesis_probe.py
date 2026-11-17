"""Content-free inputs for a synthetic hypothesis probe."""

import hashlib
import json

from agent_harness_lab.diagnostics.brief import BUILT_IN, ERROR_CATEGORIES, NEXT_ACTIONS


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
    return {'task.json': encode(task), 'config.json': encode({'ready': False}), 'synthetic_worker.py': worker}


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
               and hashlib.sha256(read(root / 'synthetic_worker.py')).hexdigest() == pin)
except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
    outcome = False
receipt(True, outcome)
raise SystemExit(0 if outcome else 1)
'''


def grader_files(mechanism):
    """Freeze an artifact-only oracle for the generated worker, separate from the fixture."""
    worker = fixture_files(mechanism)['synthetic_worker.py'].encode()
    return {'grade.py.txt': GRADER, 'expected.json': json.dumps({'worker_sha256': hashlib.sha256(worker).hexdigest()}, sort_keys=True) + '\n'}
