"""Frozen acceptance declaration for the evaluate > improve > evaluate loop; reading it never grants authority."""
from dataclasses import dataclass
import hashlib
import json
import math

from .errors import LoopError

PROFILE = 'ahl-loop-spec-v1'
RELATIVE = 'relative_supported_improvement'
SOURCES = ('cli', 'user', 'project')  # precedence order; a field no source declares takes its documented default
DECLARED = ('accept_score', 'accept_basis', 'regression_margin', 'noise_margin', 'max_rounds', 'plateau_rounds',
            'on_regression', 'alpha')
DEFAULTS = dict(accept_score=None, accept_basis='lower_bound', regression_margin=0.0, noise_margin=0.0, max_rounds=3,
                plateau_rounds=2, on_regression='stop', alpha=0.05)
MAX_ROUNDS_CAP = 10
# A loop declaration sets a bar, never authority: these keys are refused in every source, even with a safe value.
AUTHORITY = frozenset({'recommendation_only', 'automatic_promotion', 'apply', 'global', 'scope', 'promotion',
                       'publication_allowed', 'certification'})


def _number(value):
    try: return type(value) in (int, float) and math.isfinite(value)
    except OverflowError: return False  # an integer too large for a float is as unusable as an infinite one


def _score(value):
    return _number(value) and 0 < value <= 1


def _accept_score(value, source, areas):
    if isinstance(value, dict):
        if not value: raise LoopError('loop_accept_score_invalid', 'accept_score', source)
        if not areas: raise LoopError('areas_not_declared', 'accept_score', source)
        for area, score in value.items():
            field = f'accept_score.{str(area)[:64]}'
            if not isinstance(area, str) or area not in areas: raise LoopError('loop_area_undeclared', field, source)
            if not _score(score): raise LoopError('loop_accept_score_invalid', field, source)
        return tuple(sorted((area, float(score)) for area, score in value.items()))
    if not _score(value): raise LoopError('loop_accept_score_invalid', 'accept_score', source)
    return float(value)


def _choice(reason, allowed):
    def check(value, source, areas, field):
        if value not in allowed or type(value) is not str: raise LoopError(reason, field, source)
        return value
    return check


def _margin(value, source, areas, field):
    if not _number(value) or not 0 <= value < 1: raise LoopError('loop_margin_invalid', field, source)
    return float(value)


def _rounds(reason, upper):
    def check(value, source, areas, field):
        if type(value) is not int or not 1 <= value <= upper: raise LoopError(reason, field, source)
        return value
    return check


def _alpha(value, source, areas, field):
    if not _number(value) or not 0 < value < 1: raise LoopError('loop_alpha_invalid', field, source)
    return float(value)


_CHECK = dict(
    accept_score=lambda value, source, areas, field: _accept_score(value, source, areas),
    accept_basis=_choice('loop_accept_basis_invalid', ('lower_bound',)),
    regression_margin=_margin, noise_margin=_margin,
    max_rounds=_rounds('loop_max_rounds_invalid', MAX_ROUNDS_CAP),
    plateau_rounds=_rounds('loop_plateau_rounds_invalid', 10**6),  # the upper bound is max_rounds, checked across fields
    on_regression=_choice('loop_on_regression_invalid', ('stop', 'rollback_continue')),
    alpha=_alpha)


def _plateau_within_rounds(values, source):
    if values.get('plateau_rounds', 1) > values.get('max_rounds', MAX_ROUNDS_CAP):
        raise LoopError('loop_plateau_rounds_invalid', 'plateau_rounds', source)


def parse_declaration(value, source, areas=()):
    """Validate one declaration layer and return only the fields it declares, normalized; None declares nothing.

    areas is the project's declared area ids: a per-area accept_score map is valid only against them.
    """
    if value is None: return {}
    if not isinstance(value, dict): raise LoopError('loop_declaration_invalid', None, source)
    for key in value:
        field = str(key)[:64]
        if key in AUTHORITY: raise LoopError('loop_authority_refused', field, source)
        if key not in DECLARED: raise LoopError('loop_field_unknown', field, source)
    parsed = {key: _CHECK[key](item, source, areas, key) for key, item in value.items()}
    _plateau_within_rounds(parsed, source)
    return parsed


@dataclass(frozen=True, slots=True)
class LoopSpec:
    """The declared bar and stop policy, frozen before round 0 and immutable for the run."""
    accept_score: float | tuple[tuple[str, float], ...] | None
    accept_basis: str
    regression_margin: float
    noise_margin: float
    max_rounds: int
    plateau_rounds: int
    on_regression: str
    alpha: float
    limit_seconds: int | float
    limit_trials: int
    sources: tuple[tuple[str, str], ...]  # (field, source) for the eight declared fields and limits, in DECLARED order
    schema_version: int = 1
    profile: str = PROFILE
    recommendation_only: bool = True
    automatic_promotion: bool = False
    certification: str = 'unverified'

    def declared(self):
        score = dict(self.accept_score) if isinstance(self.accept_score, tuple) else self.accept_score
        return dict(accept_score=score, accept_basis=self.accept_basis, regression_margin=self.regression_margin,
                    noise_margin=self.noise_margin, max_rounds=self.max_rounds, plateau_rounds=self.plateau_rounds,
                    on_regression=self.on_regression, alpha=self.alpha)

    @property
    def mode(self):
        return RELATIVE if self.accept_score is None else 'per_area' if isinstance(self.accept_score, tuple) else 'absolute'

    @property
    def acceptance_source(self):
        return dict(self.sources)['accept_score']

    @property
    def per_look_alpha(self):
        """Each round is one look at the same frozen bank, so the error level is split across the round cap."""
        return self.alpha / self.max_rounds

    def validate(self):
        if self.schema_version != 1 or self.profile != PROFILE: raise LoopError('loop_declaration_invalid', 'profile', 'declared')
        for name, safe in (('recommendation_only', self.recommendation_only is True),
                           ('automatic_promotion', self.automatic_promotion is False), ('certification', self.certification == 'unverified')):
            if not safe: raise LoopError('loop_authority_refused', name, 'declared')
        score = self.declared()['accept_score']
        areas = tuple(score) if isinstance(score, dict) else ()
        parse_declaration({key: item for key, item in self.declared().items() if item is not None}, 'declared', areas)
        if (not _number(self.limit_seconds) or self.limit_seconds <= 0 or type(self.limit_trials) is not int
                or self.limit_trials <= 0):
            raise LoopError('loop_limits_invalid', 'limits', 'declared')
        if (type(self.sources) is not tuple or [key for key, _ in self.sources] != [*DECLARED, 'limits']
                or any(source not in (*SOURCES, 'default') for _, source in self.sources)):
            raise LoopError('loop_declaration_invalid', 'sources', 'declared')
        return self

    def _identity(self):
        self.validate()
        return dict(profile=self.profile, schema_version=self.schema_version, **self.declared(),
                    limits=dict(seconds=self.limit_seconds, trials=self.limit_trials), mode=self.mode,
                    per_look_alpha=self.per_look_alpha, recommendation_only=True, automatic_promotion=False,
                    certification=self.certification)

    @property
    def digest(self):
        """SHA-256 of the canonical declared values; where a value came from is a label, not part of the identity."""
        return hashlib.sha256(json.dumps(self._identity(), sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    def to_dict(self):
        return dict(self._identity(), looks=self.max_rounds, acceptance_source=self.acceptance_source,
                    sources=dict(self.sources), digest=self.digest)


def resolve(*, limits, limits_source='project', cli=None, user=None, project=None, areas=()):
    """Merge the layers (cli over user over project over default) into one validated spec naming each value's source.

    limits is the project's {seconds, trials} cap, referenced rather than redeclared by the loop.
    """
    layers = [(source, parse_declaration(layer, source, areas)) for source, layer in (('cli', cli), ('user', user), ('project', project))]
    values, sources = {}, {}
    for name in DECLARED:
        source, found = next(((source, layer[name]) for source, layer in layers if name in layer), ('default', DEFAULTS[name]))
        values[name], sources[name] = found, source
    if sources['plateau_rounds'] == 'default': values['plateau_rounds'] = min(DEFAULTS['plateau_rounds'], values['max_rounds'])
    _plateau_within_rounds(values, sources['plateau_rounds'])
    if not isinstance(limits, dict) or set(limits) != {'seconds', 'trials'} or limits_source not in (*SOURCES, 'default'):
        raise LoopError('loop_limits_invalid', 'limits', limits_source)
    return LoopSpec(**values, limit_seconds=limits['seconds'], limit_trials=limits['trials'],
                    sources=(*sources.items(), ('limits', limits_source))).validate()
