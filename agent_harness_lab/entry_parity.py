"""Receipt parity between ``ahl improve PROJECT`` and ``ahl skills invoke PROJECT``.

Both routes enter one ``improve`` controller. For the same project, frozen
inputs and runtime, every receipt key and value must match except the fields
named here. This is a verification contract only: it grants no authority and
is not a public receipt.

Each route allocates its own private run directory, so string values are
compared, including mapping keys, after replacing that route's run directory
with ``RUN_PLACEHOLDER``. Every other allowed difference is listed below with
its cause.
"""
from fnmatch import fnmatchcase
import json
from pathlib import Path
import re

PROFILE = 'ahl-entry-receipt-parity-v1'
RUN_PLACEHOLDER = '<run>'

# Private artifact written only by the named route as soon as its run directory
# exists, including a run that the configuration preflight blocks.
ROUTES = ('cli', 'skill')
ENTRY_ONLY_ARTIFACTS = {'invocation.json': 'skill'}

# Artifact pattern -> dotted key patterns that may differ. A ``*`` stands for a
# list index. The plan's references are keyed by name.
_RUN_TIMING = ('invocation.deadline', 'invocation.remaining_seconds')
ALLOWED_DIFFERENCES = {
    # stdout receipt and workflow.json: the run directory itself, plus digests
    # of run artifacts that contain the timing and context fields below.
    'workflow.json': ('output', 'plan_sha256', 'decision.bindings.results_sha256',
                      'decision.bindings.manifest_sha256'),
    'change-plan.json': ('plan_sha256', 'harness_change_plan.affected_controls_sha256',
                         'harness_change_plan.references.evidence'),
    # Measured trial durations, and the digest of a manifest that carries the
    # remaining budget. Exact environment digests include the unique temporary Codex home;
    # the normalized comparison digest remains checked.
    'evaluation-*/results.json': ('elapsed_seconds', 'manifest_sha256',
                                  'trials.*.execution.elapsed_seconds', 'trials.*.grader.elapsed_seconds',
                                  'trials.*.environment.sha256', 'trials.*.grader_environment.sha256'),
    'feedback-*.json': ('trials.*.execution.elapsed_seconds', 'trials.*.grader.elapsed_seconds',
                        'trials.*.environment.sha256', 'trials.*.grader_environment.sha256'),
    'preparation-*.environment.json': ('sha256',),
    'evaluation-construction-*.environment.json': ('sha256',),
    'preparation/evaluation-*/manifest.json': ('budget_seconds', 'timeout_seconds'),
    # Descriptive invocation context given to preparation children: the entry
    # origin, the parent run identity and the remaining budget.
    'preparation/context.json': ('invocation.origin', 'invocation.parent_run_id', *_RUN_TIMING),
    'preparation/evaluation-*/context.json': ('invocation.origin', 'invocation.parent_run_id', *_RUN_TIMING),
}
RECEIPT = 'workflow.json'

# Progress lines on stderr report elapsed wall time.
STDERR_ELAPSED = re.compile(r'elapsed=\d+(?:\.\d+)?s')


def _normalized(value, run):
    if isinstance(value, str):
        return value.replace(str(run), RUN_PLACEHOLDER)
    if isinstance(value, dict):
        return {_normalized(key, run): _normalized(item, run) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalized(item, run) for item in value]
    return value


def _flatten(value, prefix=''):
    if isinstance(value, dict):
        if not value:
            yield prefix, {}
        for key, item in value.items():
            yield from _flatten(item, f'{prefix}.{key}' if prefix else str(key))
    elif isinstance(value, list):
        named = (prefix.endswith('references') and value
                 and all(isinstance(row, list) and len(row) == 2 and isinstance(row[0], str) for row in value)
                 and len({row[0] for row in value}) == len(value))
        if not value:
            yield prefix, []
        for index, item in enumerate(value):
            if named:
                yield from _flatten(item[1], f'{prefix}.{item[0]}')
            else:
                yield from _flatten(item, f'{prefix}.{index}')
    else:
        yield prefix, value


def allowed(artifact, key):
    return any(fnmatchcase(artifact, pattern) and any(fnmatchcase(key, field) for field in fields)
               for pattern, fields in ALLOWED_DIFFERENCES.items())


def document_differences(artifact, first, second, first_run, second_run):
    """Dotted keys that differ outside the allowed set, sorted.

    Allowed keys may differ in value only; a key present on one side is reported.
    """
    left = dict(_flatten(_normalized(first, first_run)))
    right = dict(_flatten(_normalized(second, second_run)))
    return sorted(key for key in left.keys() | right.keys()
                  if key not in left or key not in right
                  or left[key] != right[key] and not allowed(artifact, key))


def run_differences(first_run, second_run):
    """Compare the CLI run (first) with the skill run (second); return {artifact: [keys]} outside the contract.

    A file present in only one run is reported with the key ``<presence>``. An
    ``ENTRY_ONLY_ARTIFACTS`` file is reported unless it is present exactly in the
    run of its named route. Non-JSON files must match byte for byte after
    run-directory normalization.
    """
    first_run, second_run = Path(first_run), Path(second_run)
    files = [{str(path.relative_to(root)) for path in root.rglob('*') if path.is_file()}
             for root in (first_run, second_run)]
    found = {name: ['<presence>'] for name in files[0] ^ files[1] if name not in ENTRY_ONLY_ARTIFACTS}
    for name, route in ENTRY_ONLY_ARTIFACTS.items():
        if [name in names for names in files] != [owner == route for owner in ROUTES]:
            found[name] = ['<presence>']
    for name in sorted(files[0] & files[1] - ENTRY_ONLY_ARTIFACTS.keys()):
        raw = [(root / name).read_bytes() for root in (first_run, second_run)]
        try:
            first, second = (json.loads(data) for data in raw)
        except (UnicodeDecodeError, ValueError):
            same = raw[0].replace(str(first_run).encode(), RUN_PLACEHOLDER.encode()) == \
                raw[1].replace(str(second_run).encode(), RUN_PLACEHOLDER.encode())
            if not same:
                found[name] = ['<bytes>']
            continue
        keys = document_differences(name, first, second, first_run, second_run)
        if keys:
            found[name] = keys
    return found


def normalized_stderr(text, run):
    return STDERR_ELAPSED.sub('elapsed=<seconds>s', text.replace(str(run), RUN_PLACEHOLDER))
