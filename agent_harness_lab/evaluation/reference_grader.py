"""Controller-owned, bounded JSON artifact grader for final-bank qualification."""

PROFILE = 'ahl-json-reference-v1'
PROGRAM = '''import json,sys
from pathlib import Path

def pairs(items):
    result = {}
    for key, value in items:
        if key in result: raise ValueError('duplicate key')
        result[key] = value
    return result

def invalid(value):
    raise ValueError('invalid number')

def load(path):
    raw = path.read_bytes()
    if len(raw) > 1048576: raise ValueError('input too large')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)

def matches(actual, expected):
    if type(actual) is not type(expected): return False
    if isinstance(expected, dict):
        return all(key in actual and matches(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(matches(a, b) for a, b in zip(actual, expected))
    return actual == expected

try:
    expected = load(Path(sys.argv[2]))
    if type(expected) is not dict or not expected: raise ValueError('invalid reference')
    actual = load(Path(sys.argv[1]) / 'result.json')
    outcome = matches(actual, expected)
except (OSError, ValueError, TypeError, RecursionError):
    outcome = False
print(json.dumps(dict(schema_version=1, valid=True, outcome=outcome, grader_id='ahl-json-reference', grader_version='v1')))
raise SystemExit(0 if outcome else 1)
'''


def validate(bundle):
    import json
    from agent_harness_lab.private_journal import encoded
    from .integrity import invalid_constant, read_regular, unique_object
    if read_regular(bundle/'grade.py.txt', 1024*1024) != PROGRAM.encode():
        raise ValueError('unsupported reference grader')
    value = json.loads(read_regular(bundle/'expected.json', 1024*1024),
        object_pairs_hook=unique_object, parse_constant=invalid_constant)
    if type(value) is not dict or not value:
        raise ValueError('unsupported reference data')
    encoded(value)
