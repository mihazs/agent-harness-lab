"""Preregistered class-wise precision/recall uncertainty plan for further diagnostic annotation.

The plan reports how many more independent roots each class needs before a Wilson
interval, at the worst-case proportion, is as narrow as the declared target. It is a
planning bound, not a performance estimate, and no total corpus size is sufficient alone.
"""
from copy import deepcopy
import hashlib
import math

from agent_harness_lab.evaluation.power import Z95
from agent_harness_lab.evaluation.task_bank import identifier
from agent_harness_lab.private_journal import encoded

PROFILE = 'class-annotation-plan-v1'
METRICS = ('precision', 'recall')
MAX_CLASSES = 64
MAX_ROOTS = 1_000_000


def half_width(roots):
    """Worst-case (p = 0.5) Wilson 95% half-width for a metric over this many independent roots."""
    if roots <= 0: return math.inf
    z2 = Z95 * Z95
    return Z95 * math.sqrt(0.25 / roots + z2 / (4 * roots * roots)) / (1 + z2 / roots)


def roots_needed(target):
    low, high = 1, MAX_ROOTS  # half_width falls monotonically with roots
    while low < high:
        middle = (low + high) // 2
        low, high = (low, middle) if half_width(middle) <= target else (middle + 1, high)
    return low


def _count(value, name):
    if type(value) is not int or not 0 <= value <= MAX_ROOTS: raise ValueError(f'{name} must be an integer in [0, {MAX_ROOTS}]')
    return value


def plan(spec, observed):
    """Compare observed independent-root counts per class and metric with a frozen spec."""
    if (not isinstance(spec, dict) or set(spec) != {'schema_version', 'classes'} or spec['schema_version'] != 1
            or not isinstance(spec['classes'], dict) or not 0 < len(spec['classes']) <= MAX_CLASSES):
        raise ValueError('annotation plan requires schema_version 1 and 1..64 classes')
    if not isinstance(observed, dict) or set(observed) - set(spec['classes']):
        raise ValueError('observed counts may only name classes declared in the plan')
    classes = {}
    for name in sorted(spec['classes']):
        identifier(name); target = spec['classes'][name]
        if not isinstance(target, dict) or set(target) != {'half_width', 'min_roots'}:
            raise ValueError('each class declares half_width and min_roots')
        width, minimum = target['half_width'], _count(target['min_roots'], 'min_roots')
        if type(width) not in (int, float) or not 0.005 <= width <= 0.5 or minimum < 1:
            raise ValueError('half_width must be in [0.005, 0.5] and min_roots at least 1')
        counts = observed.get(name, {})
        if not isinstance(counts, dict) or set(counts) - set(METRICS): raise ValueError('observed counts name precision and recall roots only')
        classes[name] = {}
        for metric in METRICS:
            roots = _count(counts[metric], f'{name} {metric} roots') if metric in counts else None
            need = max(roots_needed(width), minimum)
            known = roots or 0  # unreported counts stay inconclusive rather than becoming zero evidence
            classes[name][metric] = dict(roots=roots, worst_case_half_width=None if not known else round(half_width(known), 4),
                required_roots=need, additional_roots=max(0, need - known),
                status='sufficient' if known >= need else 'inconclusive')
    done = all(m['status'] == 'sufficient' for c in classes.values() for m in c.values())
    return dict(profile=PROFILE, spec_sha256=hashlib.sha256(encoded(deepcopy(spec))).hexdigest(), classes=classes,
        status='sufficient' if done else 'inconclusive', qualification='planning_only', certification='not_issued',
        classifier_selection_allowed=False, publication_allowed=False, root_independence='unverified',
        limitations=['Counts are caller-supplied independent-root totals; independence and preregistration order are not verified.',
            'Worst-case interval width bounds annotation needs; it is not observed precision, recall or classifier evidence.',
            'A class below its required roots stays inconclusive whatever the corpus size.'])


def main(argv=None):
    import argparse
    from pathlib import Path
    import json
    from agent_harness_lab.private_journal import record
    parser = argparse.ArgumentParser(prog='ahl annotation-plan', description='Compare declared independent-root counts per class with a frozen precision/recall interval plan and report additional annotation needs. Planning only: no certification, selection or publication authority.',
        epilog='Exit: 0 report printed; 2 invalid input. Output is private JSON.')
    parser.add_argument('spec', type=Path, help='Existing schema-1 JSON: {"schema_version":1,"classes":{NAME:{"half_width":0.1,"min_roots":20}}}')
    parser.add_argument('observed', type=Path, help='Existing JSON {NAME:{"precision":ROOTS,"recall":ROOTS}} of independent-root counts; omitted metrics stay inconclusive')
    args = parser.parse_args(argv)
    try:
        print(json.dumps(plan(record(args.spec, 65536)[0], record(args.observed, 65536)[0]), indent=2, sort_keys=True))
    except (OSError, ValueError) as error:
        print(f'ahl annotation-plan: {error}', file=__import__('sys').stderr); return 2
    return 0
