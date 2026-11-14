"""#758: every receipt field a clock produces is an allowed CLI/skill difference, derived from the code and not hand-listed."""
# fixed-in: daa30269
import ast
from fnmatch import fnmatchcase
from pathlib import Path
import re

import agent_harness_lab
from agent_harness_lab import cli, entry_parity

PACKAGE = Path(agent_harness_lab.__file__).parent
# The modules that write the compared run artifacts: receipt, invocation context, construction repairs and trial results.
WRITERS = (PACKAGE/'workflow.py', PACKAGE/'one_call.py', PACKAGE/'evaluation/construction.py',
           cli.assets()/'skills/harness-evaluation/scripts/run_evaluation.py')
DURATION = re.compile(r'(_seconds|_elapsed)$|^elapsed_')
CLOCKS = {'monotonic', 'time', 'perf_counter'}


def _reads(node):
    """Names, attributes and string keys an expression mentions, and whether it reads a clock."""
    ids, clock = set(), False
    for item in ast.walk(node):
        if isinstance(item, ast.Name): ids.add(item.id)
        elif isinstance(item, ast.Attribute): ids.add(item.attr)
        elif isinstance(item, ast.Constant) and isinstance(item.value, str): ids.add(item.value)
        clock |= (isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute) and item.func.attr in CLOCKS
                  and getattr(item.func.value, 'id', None) == 'time')
    return ids, clock


def _stored(target):
    if isinstance(target, ast.Name): yield target.id
    elif isinstance(target, ast.Attribute): yield target.attr
    elif isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant): yield target.slice.value
    elif isinstance(target, (ast.Tuple, ast.List)):
        for item in target.elts: yield from _stored(item)


def clock_fields(source):
    """Duration-named output fields (stored keys, call keywords, dict keys) whose value derives from a clock read.

    Derivation follows names, attributes and string keys through assignments to a fixed point, so
    ``total += time.monotonic() - t`` makes ``out['x_seconds'] = round(total, 1)`` a clock field while a
    configured limit such as ``max_seconds=args.timeout`` is not.
    """
    tree = ast.parse(source)
    assigned, fields = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assigned += [(name, node.value) for target in node.targets for name in _stored(target)]
            fields += [(target.slice.value, node.value) for target in node.targets
                       if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)]
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)) and node.value is not None:
            assigned += [(name, node.value) for name in _stored(node.target)]
        elif isinstance(node, ast.FunctionDef): assigned.append((node.name, node))  # a clock helper such as now()
        elif isinstance(node, ast.keyword) and node.arg: fields.append((node.arg, node.value))
        elif isinstance(node, ast.Dict):
            fields += [(key.value, value) for key, value in zip(node.keys, node.values)
                       if isinstance(key, ast.Constant) and isinstance(key.value, str)]
    clocked = set()
    while True:
        grown = {name for name, value in assigned if name not in clocked
                 and (lambda ids, clock: clock or ids & clocked)(*_reads(value))}
        if not grown: break
        clocked |= grown
    return {name for name, value in fields if DURATION.search(name)
            and (lambda ids, clock: clock or ids & clocked)(*_reads(value))}


def disallowed(fields):
    """Fields no artifact pattern in the contract lets differ (matched on the last key segment)."""
    names = {pattern.rsplit('.', 1)[-1] for patterns in entry_parity.ALLOWED_DIFFERENCES.values() for pattern in patterns}
    return sorted(field for field in fields if not any(fnmatchcase(field, name) for name in names))


def test_every_clock_derived_receipt_field_is_an_allowed_difference():
    found = set().union(*(clock_fields(path.read_text(encoding='utf-8')) for path in WRITERS))
    assert {'evaluation_construction_seconds', 'elapsed_seconds', 'remaining_seconds'} <= found  # the scan still sees known timings
    assert disallowed(found) == []


def test_a_new_measured_field_is_found_and_rejected_but_a_configured_limit_is_not():
    source = '''
import time
def stage(args, result):
    started = time.monotonic()
    args._spent += time.monotonic() - started
    result['stage_seconds'] = round(args._spent, 1)
    result.update(dict(wait_elapsed=time.monotonic() - started, elapsed_total=args._spent, max_seconds=args.timeout))
'''
    assert clock_fields(source) == {'stage_seconds', 'wait_elapsed', 'elapsed_total'}
    assert disallowed(clock_fields(source)) == ['elapsed_total', 'stage_seconds', 'wait_elapsed']


def test_measured_durations_may_differ_but_every_other_difference_still_fails():
    def receipt(seconds, status='no_change'):
        return dict(status=status, output='/a/run', evaluation_construction_seconds=seconds,
                    evaluation_construction_repairs=[dict(attempt=1, outcome='repaired', elapsed_seconds=seconds)])
    first, second = receipt(0.1), receipt(0.4)
    compare = lambda a, b, name='workflow.json': entry_parity.document_differences(name, a, b, '/a/run', '/a/run')
    assert compare(first, second) == []
    assert compare(dict(receipt=first), dict(receipt=second), 'recovery.json') == []
    assert compare(dict(manifest_repairs=first['evaluation_construction_repairs']),
                   dict(manifest_repairs=second['evaluation_construction_repairs']), 'evaluation-construction-0.json') == []
    assert compare(first, receipt(0.4, status='applied')) == ['status']  # a non-duration difference is reported
    changed = receipt(0.4); changed['evaluation_construction_repairs'][0]['outcome'] = 'requested'
    assert compare(first, changed) == ['evaluation_construction_repairs.0.outcome']
    missing = receipt(0.4); del missing['evaluation_construction_seconds']
    assert compare(first, missing) == ['evaluation_construction_seconds']  # a duration may differ in value, never in presence
