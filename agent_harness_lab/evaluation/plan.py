"""Immutable repeated paired plans and deterministic AB/BA scheduling."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import random


@dataclass(frozen=True)
class ExperimentSpec:
    experiment_id: str
    tasks: tuple  # (task ID, independent root ID, family)
    repeats: int
    seed: int
    max_trials: int
    min_roots: int
    effect_margin: float
    family_margin: float
    alpha: float
    family_weights: tuple  # (family, positive weight), sums to 1
    baseline_hash: str
    candidate_hash: str
    grader_hash: str
    runtime_hash: str
    profile: str = 'descriptive_smoke'
    partition: str = 'development'

    def validate(self):
        if not self.tasks or self.profile not in ('descriptive_smoke', 'certification'):
            raise ValueError('invalid experiment profile/tasks')
        if any(type(n) is not int or n <= 0 for n in (self.repeats, self.max_trials, self.min_roots)) or self.min_roots < 2:
            raise ValueError('invalid repetitions/roots/budget')
        if any(not isinstance(t, (tuple, list)) or len(t) != 3 or
               any(not isinstance(v, str) or not 0 < len(v) <= 128 for v in t) for t in self.tasks):
            raise ValueError('bounded task/root/family identities required')
        if type(self.seed) is not int or len({t[0] for t in self.tasks}) != len(self.tasks):
            raise ValueError('duplicate task ID or invalid seed')
        if 2 * len(self.tasks) * self.repeats > self.max_trials:
            raise ValueError('planned experiment exceeds trial budget')
        for n in (self.effect_margin, self.family_margin, self.alpha):
            if type(n) not in (int, float) or not math.isfinite(n):
                raise ValueError('nonfinite decision margin/confidence')
        if not 0 < self.alpha < 1 or not 0 <= self.effect_margin < 1 or not 0 <= self.family_margin < 1:
            raise ValueError('invalid decision margin/confidence')
        weights = dict(self.family_weights)
        if len(weights) != len(self.family_weights) or set(weights) != {t[2] for t in self.tasks} or any(
                type(w) not in (int, float) or not math.isfinite(w) or w <= 0 for w in weights.values()) or not math.isclose(sum(weights.values()), 1):
            raise ValueError('invalid frozen family weights')
        roots = {}
        for _, root, family in self.tasks:
            if roots.setdefault(root, family) != family:
                raise ValueError('root cannot straddle weighted families')
        if not all(isinstance(v, str) and v for v in (self.experiment_id, self.baseline_hash, self.candidate_hash, self.grader_hash, self.runtime_hash)):
            raise ValueError('missing experiment provenance')
        if self.profile == 'certification' and self.partition != 'certification':
            raise ValueError('certification requires an untouched reserved partition')

    @property
    def digest(self):
        self.validate()
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()

    @classmethod
    def parse(cls, value):
        return cls(**{**value, 'tasks': tuple(tuple(t) for t in value['tasks']),
                      'family_weights': tuple(tuple(t) for t in value['family_weights'])})


def schedule(spec):
    spec.validate()
    frozen_digest = spec.digest
    rng, pairs = random.Random(spec.seed), []
    tasks = list(spec.tasks); rng.shuffle(tasks)
    for repetition in range(spec.repeats):
        for index, (task, root, family) in enumerate(tasks):
            order = ('baseline', 'candidate') if (index + repetition + spec.seed) % 2 == 0 else ('candidate', 'baseline')
            for version in order:
                trial = hashlib.sha256(f'{frozen_digest}:{task}:{repetition}:{version}'.encode()).hexdigest()
                pairs.append(dict(trial_id=trial, task=task, root=root, family=family,
                                  repetition=repetition, version=version, plan_digest=frozen_digest))
    return pairs
