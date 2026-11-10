"""Immutable, private campaign declarations; no execution authority or state ledger."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

PROFILE = 'ahl-campaign-spec-v1'
_HASHES = ('project_sha256','baseline_sha256','config_sha256','task_bank_sha256',
           'grader_sha256','runtime_sha256','authorization_sha256','evaluation_plan_sha256',
           'execution_profile_sha256','measurement_policy_sha256')
_TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z')


@dataclass(frozen=True, slots=True)
class CampaignSpec:
    project_sha256: str
    baseline_sha256: str
    config_sha256: str
    task_bank_sha256: str
    grader_sha256: str
    runtime_sha256: str
    authorization_sha256: str
    evaluation_plan_sha256: str
    execution_profile_sha256: str
    measurement_policy_sha256: str
    strategy_id: str
    strategy_version: str
    allowed_surfaces: tuple[str, ...]
    max_trials: int
    max_seconds: int
    final_trial_reserve: int
    schema_version: int = 1
    profile: str = PROFILE
    retry_policy: str = 'manual_reconciliation_only'
    approval_policy: str = 'explicit_review'
    publication_allowed: bool = False
    automatic_promotion: bool = False

    def validate(self):
        if (type(self.schema_version) is not int or self.schema_version != 1 or self.profile != PROFILE
                or self.retry_policy != 'manual_reconciliation_only' or self.approval_policy != 'explicit_review'
                or self.publication_allowed is not False or self.automatic_promotion is not False):
            raise ValueError('unsupported campaign declaration')
        for name in _HASHES:
            value = getattr(self,name)
            if type(value) is not str or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
                raise ValueError('campaign identities require exact lowercase SHA-256 values')
        if any(type(value) is not str or _TOKEN.fullmatch(value) is None for value in (self.strategy_id,self.strategy_version)):
            raise ValueError('bounded campaign strategy identity required')
        if (type(self.allowed_surfaces) is not tuple or not self.allowed_surfaces
                or len(self.allowed_surfaces) > 3 or any(value not in ('instruction','skill','hook') for value in self.allowed_surfaces)
                or self.allowed_surfaces != tuple(sorted(set(self.allowed_surfaces)))):
            raise ValueError('unique ordered supported campaign surfaces required')
        if (type(self.max_trials) is not int or not 1 <= self.max_trials <= 10000
                or type(self.max_seconds) is not int or not 1 <= self.max_seconds <= 604800
                or type(self.final_trial_reserve) is not int or not 1 <= self.final_trial_reserve <= self.max_trials):
            raise ValueError('bounded campaign caps and final reserve required')
        return self

    def to_dict(self):
        self.validate()
        value = asdict(self); value['allowed_surfaces'] = list(self.allowed_surfaces)
        return value

    @property
    def digest(self):
        return hashlib.sha256(json.dumps(self.to_dict(),sort_keys=True,separators=(',', ':')).encode()).hexdigest()

    @classmethod
    def parse(cls,value):
        if not isinstance(value,dict) or set(value) != set(cls.__dataclass_fields__):
            raise ValueError('campaign declaration fields mismatch')
        if type(value['allowed_surfaces']) is not list:
            raise ValueError('campaign surfaces must be a list')
        return cls(**dict(value,allowed_surfaces=tuple(value['allowed_surfaces']))).validate()
