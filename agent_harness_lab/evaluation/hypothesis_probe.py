"""Content-free inputs for a synthetic hypothesis probe."""

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
