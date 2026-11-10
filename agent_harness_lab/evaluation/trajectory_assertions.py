"""Frozen three-valued constraints over owned controller observations, not effects."""
from copy import deepcopy
import hashlib
from pathlib import Path

from agent_harness_lab.private_journal import encoded, record
from .integrity import digest

PROFILE = 'controller-trajectory-v1'
MAX_BYTES = 65536
STAGES = ('request', 'denied', 'dispatch', 'result')
ORDINAL = dict(request='observed_ordinal', denied='decision_ordinal', dispatch='dispatch_ordinal', result='outcome_ordinal')
_TOKEN = object()


def _hash(raw): return hashlib.sha256(raw).hexdigest()


def _fields(value, names):
    if not isinstance(value, dict) or set(value) != set(names.split()):
        raise ValueError('trajectory fields differ from frozen schema')


def _text(value, maximum=128):
    if not isinstance(value, str) or not value or '\0' in value or len(value.encode('utf-8')) > maximum:
        raise ValueError('bounded valid UTF-8 trajectory text required')


def _sha(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
        raise ValueError('exact trajectory SHA-256 required')


def _path(value):
    path = Path(value)
    if (not path.is_absolute() or '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents))):
        raise ValueError('trajectory oracle requires canonical symlink-free path')
    return path


def engine():
    """Use the existing conservative whole-application/runner/Python identity."""
    from .decision_receipts import engine as controller_engine
    return controller_engine()


def _arguments(value):
    if value is None: return
    _fields(value, 'mode argv')
    argv = value['argv']
    if value['mode'] == 'exact':
        if not isinstance(argv, list) or not 0 < len(argv) <= 100: raise ValueError('bounded exact argv required')
        values = argv
    elif value['mode'] == 'partial':
        if (not isinstance(argv, dict) or not 0 < len(argv) <= 100
                or any(not isinstance(k, str) or not k.isascii() or not k.isdigit() or str(int(k)) != k or int(k) >= 100 for k in argv)):
            raise ValueError('partial argv requires explicit canonical indices')
        values = argv.values()
    else: raise ValueError('unsupported trajectory argument mode')
    for value in values: _text(value, 4096)
    if sum(len(value.encode('utf-8')) for value in values) > 32768: raise ValueError('trajectory argv byte ceiling exceeded')


def _predicate(value):
    _fields(value, 'tool arguments'); _text(value['tool']); _arguments(value['arguments'])


def _rule(value, depth, nodes):
    nodes[0] += 1
    if depth > 8 or nodes[0] > 128: raise ValueError('trajectory tree limit exceeded')
    if not isinstance(value, dict): raise ValueError('trajectory rule must be an object')
    kind = value.get('kind')
    if kind in ('all_of', 'any_of'):
        _fields(value, 'kind children')
        if not isinstance(value['children'], list) or not 0 < len(value['children']) <= 32:
            raise ValueError('bounded nonempty trajectory group required')
        for child in value['children']: _rule(child, depth+1, nodes)
        return
    if kind in ('required', 'forbidden'):
        _fields(value, 'kind stage tool arguments quantifier')
        if value['quantifier'] not in ('exists', 'all'): raise ValueError('explicit trajectory quantifier required')
    elif kind == 'count':
        _fields(value, 'kind stage tool arguments minimum maximum')
        lo, hi = value['minimum'], value['maximum']
        if (type(lo) is not int or not 0 <= lo <= 10000
                or hi is not None and (type(hi) is not int or not lo <= hi <= 10000)):
            raise ValueError('invalid trajectory count bounds')
    elif kind == 'sequence':
        _fields(value, 'kind stage mode steps')
        if value['mode'] not in ('subsequence', 'exact') or not isinstance(value['steps'], list) or not 0 < len(value['steps']) <= 32:
            raise ValueError('bounded trajectory sequence required')
        for step in value['steps']:
            nodes[0] += 1
            if nodes[0] > 128: raise ValueError('trajectory predicate count exceeded')
            _predicate(step)
    else: raise ValueError('unsupported trajectory rule')
    if value['stage'] not in STAGES: raise ValueError('unsupported controller stage')
    if kind != 'sequence': _predicate({k:value[k] for k in ('tool', 'arguments')})


def _policy(value, task):
    _fields(value, 'schema_version profile task_id root_id family engine outcome_mapping assertions')
    _fields(task, 'task_id root_id family')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['profile'] != PROFILE:
        raise ValueError('unsupported trajectory policy')
    for key in task:
        _text(value[key]); _text(task[key])
        if value[key] != task[key]: raise ValueError('trajectory task identity differs')
    if value['engine'] != engine() or value['outcome_mapping'] != 'artifact_and_required_v1':
        raise ValueError('trajectory engine or outcome mapping differs')
    assertions = value['assertions']
    if not isinstance(assertions, list) or not 0 < len(assertions) <= 32: raise ValueError('bounded assertions required')
    seen, nodes = set(), [0]
    for assertion in assertions:
        _fields(assertion, 'id required severity rule'); _text(assertion['id'])
        if assertion['id'] in seen or type(assertion['required']) is not bool or assertion['severity'] not in ('normal', 'critical'):
            raise ValueError('invalid assertion identity/requirement/severity')
        seen.add(assertion['id']); _rule(assertion['rule'], 1, nodes)
    return value


def capture_policy(bundle, bundle_sha256, task_identity):
    """Only a digest-verified complete oracle bundle can opt a task in."""
    _sha(bundle_sha256); bundle = _path(bundle)
    if digest(bundle) != bundle_sha256: raise ValueError('trajectory oracle bundle changed')
    path = bundle/'trajectory.json'
    if not (path.exists() or path.is_symlink()): return None
    policy, pin = record(path, MAX_BYTES)
    _policy(policy, task_identity)
    if digest(bundle) != bundle_sha256: raise ValueError('trajectory oracle changed during capture')
    binding = dict(bundle_path=str(bundle), bundle_sha256=bundle_sha256, policy_sha256=pin, policy=policy)
    if len(encoded(binding)) > 2*MAX_BYTES: raise ValueError('trajectory binding byte limit exceeded')
    return binding


def _binding(binding):
    _fields(binding, 'bundle_path bundle_sha256 policy_sha256 policy')
    policy = binding['policy']
    task = {k:policy[k] for k in ('task_id', 'root_id', 'family')}
    current = capture_policy(binding['bundle_path'], binding['bundle_sha256'], task)
    if current is None or encoded(current) != encoded(binding): raise ValueError('stale trajectory policy binding')
    return current


class _OwnedCapture:
    """Private live-channel handle. JSON and files cannot reconstruct it."""
    def __init__(self, token, ledger, binding, trial_id, role):
        if token is not _TOKEN: raise ValueError('controller capture issuer required')
        _binding(binding)
        if role != 'executor': raise ValueError('trajectory assertions require executor scope')
        _text(trial_id, 256)
        if ledger.current['trial_id_visibility'] != 'observed':
            raise ValueError('exact controller scope identity is unavailable')
        self._ledger, self._binding = ledger, deepcopy(binding)
        self._scope, self._trial, self._role = ledger.scope, trial_id, role
        self._totals = {s:0 for s in STAGES}; self._request_gaps = 0
        self._closed = False; self._data = self._seal = None

    def event(self, stage):
        self._totals[stage] += 1

    def request_gap(self):
        self._request_gaps += 1

    def close(self):
        if self._closed: return
        self._closed = True
        self._data = self.snapshot(); self._seal = _hash(encoded(self._data))

    def snapshot(self):
        if self._data is not None:
            if _hash(encoded(self._data)) != self._seal: raise ValueError('owned trajectory capture changed')
            return deepcopy(self._data)
        ledger = self._ledger
        return dict(ledger_id=ledger.ledger_id, scope_id=self._scope, trial_id=self._trial, role=self._role,
            closed=self._closed, totals=dict(self._totals), request_gaps=self._request_gaps,
            rows=deepcopy([r for r in ledger.rows.values() if r['scope_id'] == self._scope]))


def _begin_capture(ledger, binding, trial_id, role):
    from .responses import ActionLedger
    if type(ledger) is not ActionLedger: raise ValueError('actual controller ledger required')
    return _OwnedCapture(_TOKEN, ledger, binding, trial_id, role)


def _observed(value):
    return (isinstance(value, dict) and value.get('visibility') == 'observed'
            and isinstance(value.get('text'), str) and not any(value.get(k, False) for k in ('redacted', 'truncated', 'encoding_loss')))


def _equal(value, expected):
    return value['text'] == expected if _observed(value) else None


def _argv(row, pattern):
    if pattern is None: return True
    values = row.get('argv_observations')
    if not isinstance(values, list): return None
    if pattern['mode'] == 'exact':
        if len(values) != len(pattern['argv']): return False
        comparisons = [_equal(v, wanted) for v, wanted in zip(values, pattern['argv'])]
    else:
        comparisons = [_equal(values[int(k)], wanted) if int(k) < len(values) else False for k, wanted in pattern['argv'].items()]
    return False if False in comparisons else None if None in comparisons else True


def _matches(row, predicate):
    tool = _equal(row.get('tool_name'), predicate['tool'])
    if tool is False: return False
    arguments = _argv(row, predicate['arguments'])
    if arguments is False: return False
    return True if tool is True and arguments is True else None


def _stage(data, stage):
    rows = []
    for row in data['rows']:
        if row.get('kind') != 'tool_request': continue
        selected = (stage == 'request' or stage == 'denied' and row.get('decision') == 'denied'
            or stage == 'dispatch' and row.get('backend_invoked') is True
            or stage == 'result' and row.get('worker_outcome') == 'observed')
        if selected and row.get('visibility') != 'unknown_ledger_limit': rows.append(row)
    ordinal = ORDINAL[stage]
    if any(type(r.get(ordinal)) is not int or r[ordinal] <= 0 for r in rows): raise ValueError('missing controller stage order')
    if len({r[ordinal] for r in rows}) != len(rows): raise ValueError('ambiguous controller order')
    rows.sort(key=lambda r:r[ordinal])
    complete = data['closed'] and len(rows) == data['totals'][stage]
    if stage in ('request', 'denied') and data['request_gaps']: complete = False
    if stage == 'denied' and any(r.get('kind') == 'tool_request' and r.get('decision') == 'pending_validation' for r in data['rows']): complete = False
    if stage == 'result' and (data['totals']['dispatch'] > data['totals']['result']
            or any(r.get('backend_invoked') is True and r.get('worker_outcome') != 'observed' for r in data['rows'])): complete = False
    return rows, complete


def _subsequence(rows, steps, *, possible=False):
    index, matched = 0, []
    for row in rows:
        state = _matches(row, steps[index])
        if state is True or possible and state is None:
            matched.append(row['controller_id']); index += 1
            if index == len(steps): return True, matched
    return False, matched


def _leaf(rule, data):
    rows, complete = _stage(data, rule['stage']); kind = rule['kind']; matched = []
    if kind == 'sequence':
        steps = rule['steps']
        if rule['mode'] == 'subsequence':
            known, matched = _subsequence(rows, steps)
            possible, _ = _subsequence(rows, steps, possible=True)
            state = True if known else False if complete and not possible else None
        elif len(rows) > len(steps): state = False
        elif not complete: state = None
        elif len(rows) != len(steps): state = False
        else:
            values = [_matches(r, p) for r, p in zip(rows, steps)]
            state = False if False in values else None if None in values else True
            matched = [r['controller_id'] for r, v in zip(rows, values) if v is True]
        return state, matched, complete
    values = [_matches(row, rule) for row in rows]
    matched = [r['controller_id'] for r, v in zip(rows, values) if v is True]
    if kind == 'count':
        low = sum(v is True for v in values)
        high = low + sum(v is None for v in values) if complete else None
        minimum, maximum = rule['minimum'], rule['maximum']
        if maximum is not None and low > maximum or high is not None and high < minimum: state = False
        elif low >= minimum and (maximum is None or high is not None and high <= maximum): state = True
        else: state = None
    elif rule['quantifier'] == 'exists':
        state = True if True in values else False if complete and None not in values else None
    else:
        domain = [_equal(row.get('tool_name'), rule['tool']) for row in rows]
        args = [_argv(row, rule['arguments']) for row in rows]
        if any(d is True and a is False for d, a in zip(domain, args)): state = False
        elif complete and any(d is True for d in domain) and all(d is False or a is True for d, a in zip(domain, args)): state = True
        elif complete and all(d is False for d in domain): state = False
        else: state = None
    if kind == 'forbidden' and state is not None: state = not state
    return state, matched, complete


def _evaluate_rule(rule, data, name):
    kind = rule['kind']; children = []
    if kind in ('all_of', 'any_of'):
        children = [_evaluate_rule(child, data, name+'/'+str(i)) for i, child in enumerate(rule['children'])]
        states = [c['status'] for c in children]
        if kind == 'all_of': status = 'violated' if 'violated' in states else 'satisfied' if all(s == 'satisfied' for s in states) else 'unknown'
        else: status = 'satisfied' if 'satisfied' in states else 'violated' if all(s == 'violated' for s in states) else 'unknown'
        ids, complete = [], all(c['coverage_complete'] for c in children)
    else:
        state, ids, complete = _leaf(rule, data)
        status = 'unknown' if state is None else 'satisfied' if state else 'violated'
    return dict(assertion_id=name, assertion_sha256=_hash(encoded(rule)), status=status, valid=True,
        matched_controller_ids=ids, matched_ids_omitted=0, coverage_complete=complete,
        reason='predicate_evidence_incomplete' if status == 'unknown' else 'controller_predicate_decided', children=children)


class _Receipt(dict):
    pass


def _unknown(binding, reason):
    policy = binding.get('policy', {}) if isinstance(binding, dict) else {}
    if not isinstance(policy, dict): policy = {}
    return dict(schema_version=1, profile=PROFILE, policy_sha256=binding.get('policy_sha256') if isinstance(binding, dict) else None,
        **{k:policy.get(k) for k in ('task_id', 'root_id', 'family')}, status='unknown', valid=False,
        critical_violation=False, required_observation_unknown=True, assertions=[], reason=reason,
        publication_allowed=False, automatic_promotion=False, native_identity='unverified', history_authority='unverified')


def evaluate(binding, owned_capture):
    """Only controller-issued live captures establish trajectory predicate evidence."""
    try:
        binding = _binding(binding)
        if type(owned_capture) is not _OwnedCapture or encoded(owned_capture._binding) != encoded(binding):
            return _unknown(binding, 'owned_controller_capture_required')
        data = owned_capture.snapshot()
        _text(data['trial_id'], 256)
        ids = [r['controller_id'] for r in data['rows']]
        if len(ids) != len(set(ids)) or any(r['scope_id'] != data['scope_id'] for r in data['rows']):
            raise ValueError('duplicate/wrong controller identity')
        assertions = []
        for spec in binding['policy']['assertions']:
            result = _evaluate_rule(spec['rule'], data, spec['id'])
            result.update(required=spec['required'], severity=spec['severity']); assertions.append(result)
        required = [a for a in assertions if a['required']]
        status = ('violated' if any(a['status'] == 'violated' for a in required) else
                  'unknown' if any(a['status'] == 'unknown' for a in required) else 'satisfied')
        receipt = _Receipt(schema_version=1, profile=PROFILE, policy_sha256=binding['policy_sha256'],
            bundle_sha256=binding['bundle_sha256'], **{k:binding['policy'][k] for k in ('task_id', 'root_id', 'family')},
            trial_id=data['trial_id'], ledger_id=data['ledger_id'], scope_id=data['scope_id'], status=status, valid=True,
            critical_violation=any(a['severity'] == 'critical' and a['status'] == 'violated' for a in assertions),
            required_observation_unknown=any(a['status'] == 'unknown' for a in required), assertions=assertions,
            capture_sha256=_hash(encoded(data)), closed=data['closed'], observation_scope='declared_local_controller_stages_only',
            publication_allowed=False, automatic_promotion=False, native_identity='unverified', history_authority='unverified')
        receipt._capture = owned_capture
        return receipt
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError, OSError):
        return _unknown(binding, 'trajectory_evidence_invalid_or_unsupported')


def compose(binding, artifact_grade, receipt):
    """Combine a live receipt with the runner's independent actual artifact grade."""
    try: valid_receipt = type(receipt) is _Receipt and encoded(evaluate(binding, receipt._capture)) == encoded(receipt)
    except (ValueError, TypeError, AttributeError, RecursionError, UnicodeError): valid_receipt = False
    artifact_outcome = None
    try:
        execution = artifact_grade['execution']; envelope = record_bytes_grade(execution['stdout'])
        value = envelope['outcome']
        if (envelope['valid'] is True and type(value) is bool and artifact_grade['outcome'] is value
                and execution['exit_code'] == (0 if value else 1) and type(execution['exit_code']) is int
                and execution['status'] == ('completed' if value else 'failed')
                and artifact_grade['status'] == ('passed' if value else 'failed')
                and execution.get('timeout', False) is False and execution.get('inputs_unchanged') is True
                and artifact_grade.get('grader_identity') == {k:envelope[k] for k in ('grader_id','grader_version')}):
            artifact_outcome = value
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError): pass
    known_violation = valid_receipt and (receipt['status'] == 'violated' or receipt['critical_violation'])
    outcome = False if artifact_outcome is False or known_violation else True if artifact_outcome is True and valid_receipt and receipt['status'] == 'satisfied' else None
    return dict(schema_version=1, profile=PROFILE, outcome_mapping='artifact_and_required_v1', outcome=outcome,
        artifact_outcome=artifact_outcome, artifact_valid=artifact_outcome is not None,
        valid=valid_receipt and artifact_outcome is not None, trajectory=deepcopy(dict(receipt)) if valid_receipt else _unknown(binding, 'live_receipt_required'),
        critical_violation=bool(valid_receipt and receipt['critical_violation']),
        required_observation_unknown=not valid_receipt or receipt['required_observation_unknown'],
        publication_allowed=False, automatic_promotion=False, history_authority='unverified')


def record_bytes_grade(stdout):
    from agent_harness_lab.private_journal import record_bytes
    if not isinstance(stdout, str): raise ValueError('typed artifact stdout required')
    value = record_bytes(stdout.encode('utf-8'), MAX_BYTES)[0]
    if (type(value.get('schema_version')) is not int or value['schema_version'] != 1
            or any(not isinstance(value.get(k), str) or not 0 < len(value[k]) <= 128 for k in ('grader_id', 'grader_version'))):
        raise ValueError('invalid artifact grade envelope')
    return value
