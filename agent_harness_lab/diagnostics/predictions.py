"""Frozen candidate predictions and pure reconciliation against observed outcomes (#10).

A prediction is a hypothesis registered before the candidate's outcomes are read. It is
never a verified cause or a calibrated probability, and reconciliation never certifies a
candidate: certification and stochastic analysis stay with the paired evaluation owner.
"""
from dataclasses import asdict, dataclass
import hashlib
import json

PREDICTION_SCHEMA = 'prediction-record-v1'
RECONCILIATION_PROFILE = 'prediction-reconciliation-v1'
KINDS = ('deterministic_boundary', 'stochastic_performance')
STATUSES = ('supported_under_test', 'contradicted_under_test', 'inconclusive', 'not_tested', 'not_comparable')
VERDICTS = {'supports': 'supported_under_test', 'contradicts': 'contradicted_under_test', 'inconclusive': 'inconclusive'}
_TEXT, _ITEMS = 4096, 64
_ID_CHARS = frozenset('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-')


def _ident(value):
    return isinstance(value, str) and 0 < len(value) <= 128 and value[0].isalnum() and set(value) <= _ID_CHARS


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= _TEXT


def _list(value, check):
    return isinstance(value, tuple) and len(value) <= _ITEMS and all(check(v) for v in value)


@dataclass(frozen=True)
class PredictionRecord:
    """Immutable prediction. `development_task_ids` are the only task identities a proposer may name;
    protected evaluation is predicted only as concealed family/property labels in `protected_properties`."""
    prediction_id: str
    mechanism_id: str
    baseline_hash: str
    candidate_hash: str
    bank_version: str
    grader_version: str
    kind: str
    observable_effect: str
    competing_explanation: str
    falsifier: str
    criterion: str
    development_task_ids: tuple
    control_task_ids: tuple = ()
    protected_properties: tuple = ()
    regression_risks: tuple = ()
    evidence_refs: tuple = ()
    components: tuple = ('candidate',)
    ablation_design: str | None = None

    def validate(self, *, protected_task_ids=(), allowed_refs=None):
        if not all(_ident(v) for v in (self.prediction_id, self.mechanism_id, self.baseline_hash,
                                       self.candidate_hash, self.bank_version, self.grader_version)):
            raise ValueError('invalid prediction identity')
        if self.kind not in KINDS:
            raise ValueError('invalid prediction kind')
        if not all(_text(v) for v in (self.observable_effect, self.competing_explanation, self.falsifier, self.criterion)):
            raise ValueError('prediction needs an effect, competing explanation, falsifier and criterion')
        if not (_list(self.development_task_ids, _ident) and self.development_task_ids
                and _list(self.control_task_ids, _ident) and _list(self.components, _ident) and self.components
                and _list(self.evidence_refs, _ident) and _list(self.protected_properties, _text)
                and _list(self.regression_risks, _text)):
            raise ValueError('invalid prediction lists')
        if self.ablation_design is not None and not _text(self.ablation_design):
            raise ValueError('invalid ablation design')
        if set(self.development_task_ids) & set(self.control_task_ids):
            raise ValueError('a task cannot be both effect and control')
        if (set(self.development_task_ids) | set(self.control_task_ids)) & set(protected_task_ids):
            raise ValueError('prediction names protected task identities')
        if allowed_refs is not None and not set(self.evidence_refs) <= set(allowed_refs):
            raise ValueError('prediction cites evidence outside the allowlist')
        return self

    def as_dict(self):
        return dict(schema=PREDICTION_SCHEMA, epistemic_status='hypothesis', **asdict(self))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class PredictionLedger:
    """Append-only registry. Receipts chain by hash; nothing is edited or removed."""

    def __init__(self):
        self.receipts = []
        self._records = {}

    def register(self, record, *, outcomes_read=(), protected_task_ids=(), allowed_refs=None):
        """Freeze `record`. A candidate whose outcomes were already read is recorded as late (not preregistered)."""
        record.validate(protected_task_ids=protected_task_ids, allowed_refs=allowed_refs)
        body = _digest(record.as_dict())
        earlier = self._records.get(record.prediction_id)
        if earlier is not None:
            if earlier[0] == body:
                return earlier[1]
            raise ValueError('a frozen prediction cannot be revised; register a new record for changed content')
        late = record.candidate_hash in set(outcomes_read)
        previous = self.receipts[-1]['receipt_id'] if self.receipts else None
        receipt = dict(prediction_id=record.prediction_id, record_sha256=body, preregistered=not late,
                       previous_receipt_id=previous)
        receipt['receipt_id'] = _digest(receipt)
        self.receipts.append(receipt)
        self._records[record.prediction_id] = (body, receipt, record)
        return receipt

    def verify(self):
        previous = None
        for receipt in self.receipts:
            body = {k: v for k, v in receipt.items() if k != 'receipt_id'}
            if receipt['previous_receipt_id'] != previous or receipt['receipt_id'] != _digest(body):
                return False
            previous = receipt['receipt_id']
        return True

    def entries(self):
        return [(rec, receipt) for _, receipt, rec in self._records.values()]


def _limit(prediction_id, status, reason, **extra):
    return dict(prediction_id=prediction_id, status=status, reason=reason, **extra)


def _pairs(record, observation):
    """Return (pairs_by_task, problem) where problem is (status, reason) or None."""
    rows = observation.get('pairs')
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        return None, ('inconclusive', 'no_paired_outcomes')
    seen, by_task = set(), {}
    for row in rows:
        key = (row.get('task_id'), row.get('root_id'), row.get('repeat'))
        if key in seen:
            return None, ('not_comparable', 'duplicate_outcome')
        seen.add(key)
        by_task.setdefault(row.get('task_id'), []).append(row)
    needed = set(record.development_task_ids) | set(record.control_task_ids)
    if needed - set(by_task):
        return None, ('inconclusive', 'missing_task_outcomes')
    if any(type(r.get(side)) is not bool for t in needed for r in by_task[t] for side in ('baseline_pass', 'candidate_pass')):
        return None, ('inconclusive', 'unknown_outcome')
    return by_task, None


def _judge(record, observation):
    if record.kind == 'stochastic_performance':
        verdict = observation.get('paired_analysis')
        if verdict not in VERDICTS:
            return 'inconclusive', 'stochastic_claim_needs_paired_analysis', []
        return VERDICTS[verdict], 'paired_analysis_verdict', []
    by_task, problem = _pairs(record, observation)
    if problem:
        return problem[0], problem[1], []
    limits = []
    effect = [r for t in record.development_task_ids for r in by_task[t]]
    if any(r['baseline_pass'] for r in effect):
        limits.append('baseline_already_passes')
    if any(r['baseline_pass'] and not r['candidate_pass'] for t in record.control_task_ids for r in by_task[t]):
        return 'contradicted_under_test', 'control_regressed', limits
    if any(not r['candidate_pass'] for r in effect):
        return 'contradicted_under_test', 'effect_not_observed', limits
    if limits:
        return 'inconclusive', 'non_discriminative', limits
    return 'supported_under_test', 'fail_to_pass_observed', limits


def reconcile(ledger, observations):
    """Table of every registered prediction against the observation matching it; no prediction is omitted.

    `observations` maps prediction_id to dict(candidate_hash, bank_version, grader_version, runtime_valid,
    grader_valid, pairs, paired_analysis). Missing, stale or invalid evidence never counts as support.
    """
    rows = []
    for record, receipt in ledger.entries():
        pid, obs = record.prediction_id, observations.get(record.prediction_id)
        extra = dict(kind=record.kind, preregistered=receipt['preregistered'], receipt_id=receipt['receipt_id'],
                     expected=record.observable_effect, regression_risks=list(record.regression_risks),
                     attribution='unresolved' if len(record.components) > 1 and not record.ablation_design else 'single_or_isolated')
        if obs is None:
            rows.append(_limit(pid, 'not_tested', 'no_outcome_observed', **extra))
        elif not receipt['preregistered']:
            rows.append(_limit(pid, 'not_comparable', 'late_registration', **extra))
        elif any(obs.get(k) != getattr(record, k) for k in ('candidate_hash', 'bank_version', 'grader_version')):
            rows.append(_limit(pid, 'not_comparable', 'stale_identity', **extra))
        elif obs.get('grader_valid') is not True or obs.get('runtime_valid') is not True:
            rows.append(_limit(pid, 'not_comparable', 'invalid_or_unknown_evaluator_or_runtime',
                               route='evaluator_or_runtime_defect', **extra))
        else:
            status, reason, limits = _judge(record, obs)
            rows.append(_limit(pid, status, reason, limitations=limits, **extra))
    counts = {s: sum(r['status'] == s for r in rows) for s in STATUSES}
    return dict(profile=RECONCILIATION_PROFILE, eligible_predictions=len(rows), counts=counts, entries=rows,
                certifies=False,
                limitations=['predictions are self-selected hypotheses, not population-wide diagnostic accuracy',
                             'a status is bookkeeping under the registered criterion, not a causal or certification claim'])
