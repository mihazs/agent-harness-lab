"""Private, live-only collection of completed trial usage for one fixed plan."""
from copy import deepcopy
import hashlib
import json
import os
from weakref import WeakKeyDictionary, ref

from .comparison_custody import Comparison, current, hashed
from .resource_decision import TOKEN_PROFILE, validate
from .trial_usage import latest_completed_trial_usage

_ACTIVE = WeakKeyDictionary()
MAX_TRIALS = 5000


class UsageCollector:
    """Collect summaries only inside a fresh live comparison; never grants decision authority."""
    def __init__(self, comparison, contract):
        if type(comparison) is not Comparison: raise ValueError('owned fixed comparison required')
        frozen = deepcopy(contract); validate(frozen)
        if frozen['profile'] != TOKEN_PROFILE or not any(endpoint['kind'] == 'measured_tokens' for endpoint in frozen['endpoints']):
            raise ValueError('v4 token contract required')
        self._comparison = ref(comparison)
        self._contract = frozen
        self._contract_sha256 = hashlib.sha256(json.dumps(frozen,sort_keys=True,separators=(',', ':')).encode()).hexdigest()
        plan_pin = comparison.spec.resource_contract_sha256
        if plan_pin is not None and plan_pin != self._contract_sha256:
            raise ValueError('resource contract differs from experiment plan')
        self._plan_pinned = plan_pin is not None
        self._plan = comparison.spec.digest
        self._schedule = hashed(comparison.expected)
        self._samples = []
        self._pid = os.getpid()
        self._entered = False
        self._invalid = False

    def __enter__(self):
        comparison = self._comparison()
        if (self._entered or comparison is None or self._pid != os.getpid() or current() is not comparison
                or not comparison._valid() or comparison.controls or comparison.active is not None or comparison.rows
                or comparison in _ACTIVE or len(comparison.expected) > MAX_TRIALS):
            raise ValueError('fresh live comparison required before collecting')
        self._entered = True
        _ACTIVE[comparison] = self
        return self

    def __exit__(self, *errors):
        comparison = self._comparison()
        if comparison is not None and _ACTIVE.get(comparison) is self: _ACTIVE.pop(comparison,None)
        self._entered = False

    def _valid(self, comparison):
        return (self._entered and not self._invalid and self._pid == os.getpid() and self._comparison() is comparison
                and _ACTIVE.get(comparison) is self and current() is comparison and comparison._valid()
                and comparison.spec.digest == self._plan and hashed(comparison.expected) == self._schedule
                and hashlib.sha256(json.dumps(self._contract,sort_keys=True,separators=(',', ':')).encode()).hexdigest() == self._contract_sha256)

    def _record(self, comparison):
        if not self._valid(comparison) or comparison.active is not None or len(comparison.rows) != len(self._samples)+1:
            raise ValueError('comparison collection ordering changed')
        scheduled = comparison.expected[len(self._samples)]
        if comparison.rows[-1]['custody']['scheduled'] != scheduled:
            raise ValueError('completed trial differs from frozen schedule')
        summary = latest_completed_trial_usage()
        if summary['scheduled_trial_sha256'] != hashed(scheduled): raise ValueError('completed usage binding changed')
        self._samples.append(dict(scheduled_trial_sha256=hashed(scheduled), usage=summary))

    def report(self, result, *, public=False):
        if public is not False: raise ValueError('resource collection is private')
        comparison = self._comparison()
        if (comparison is None or not self._valid(comparison) or comparison.active is not None
                or len(self._samples) != len(comparison.expected)):
            raise ValueError('complete live comparison collection required')
        comparison.finish(result)
        result = dict(profile='ahl-live-plan-provider-usage-v1', plan_sha256=self._plan, schedule_sha256=self._schedule,
                    resource_contract_sha256=self._contract_sha256, contract_binding='experiment_plan_digest' if self._plan_pinned else 'live_before_first_trial',
                    experiment_plan_contains_contract=False, samples=deepcopy(self._samples), durable=False,
                    source='provider_reported_unverified', quality='unverified', billing_completeness='unknown',
                    publication='private', publication_allowed=False, certification_authority=False, automatic_promotion=False)
        if self._plan_pinned: result['experiment_plan_contains_contract_digest'] = True
        return result


def _record_completed(comparison):
    collector = _ACTIVE.get(comparison)
    if collector is None: return
    try: collector._record(comparison)
    except Exception: collector._invalid = True
