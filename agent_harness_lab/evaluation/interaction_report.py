"""Private evaluator-side counts of interaction-contract grades by frozen family and arm.

A grader declares evidence in its typed output; the verdict is recomputed here from the frozen contract and
a verdict the grader states is only cross-checked. Nothing here captures from a runtime: absent, partial or
inconsistent evidence stays unknown, and unknown is never counted as a pass, a failure or a deliberate stop.
"""
import json
from pathlib import Path

from .behavior import frozen_trials, run_matches
from .integrity import digest, invalid_constant, read_regular, unique_object
from .interaction_binding import CONTRACT, MAX_BYTES
from .interaction_contract import PROFILE as CONTRACT_PROFILE, contract_sha256, evaluate
from .regressions import fields, sha256, text

PROFILE = 'interaction-outcomes-v1'
TRI = ('passed', 'interaction_requirement_satisfied', 'whole_task_completed', 'state_preserved')
VIOLATIONS = dict(
    unnecessary_action=('prohibited_mutation', 'acted_before_clarification', 'protected_state_changed'),
    missed_necessary_clarification=('missed_necessary_clarification',),
    unnecessary_clarification=('unnecessary_clarification',),
    abnormal_termination=('abnormal_termination',))
STOPS = ('unauthorized_stop', 'missed_required_stop')  # derived from observed stop events, not from a violation label
FINDINGS = (*VIOLATIONS, *STOPS, 'correct_deliberate_stop', 'valid_non_action_success')


def _load(raw):
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)


def capture(folders, bindings):
    """Bind contracts from digest-verified grader bundles; a bundle without one is not interaction-graded."""
    if not isinstance(folders, dict) or not isinstance(bindings, dict) or len(bindings) > 1000:
        raise ValueError('bounded interaction contract bindings required')
    result = {}
    for task, binding in sorted(bindings.items()):
        text(task)
        bundle = binding.get('bundle_sha256') if isinstance(binding, dict) else None
        sha256(bundle)
        if bundle not in folders:
            raise ValueError('missing frozen interaction bundle folder')
        folder = Path(folders[bundle])
        path = folder / CONTRACT
        if not (path.exists() or path.is_symlink()):
            continue
        if digest(folder) != bundle:
            raise ValueError('interaction bundle integrity changed')
        if (folder / 'behavior.json').exists() or (folder / 'behavior.json').is_symlink():
            raise ValueError('a grader bundle cannot declare both a behavior policy and an interaction contract')
        try:
            contract = _load(read_regular(path, MAX_BYTES).decode('utf-8'))
            identity = contract_sha256(contract)
            if contract['task_id'] != task:
                raise ValueError('contract names another task')
        except (ValueError, TypeError, KeyError, RecursionError, UnicodeError) as error:
            raise ValueError('invalid frozen interaction contract') from error
        if digest(folder) != bundle:
            raise ValueError('interaction bundle changed during capture')
        result[task] = dict(bundle_sha256=bundle, contract_sha256=identity, contract=contract)
    return result


def _binding(value):
    fields(value, 'bundle_sha256 contract_sha256 contract')
    sha256(value['bundle_sha256'])
    sha256(value['contract_sha256'])
    if contract_sha256(value['contract']) != value['contract_sha256']:
        raise ValueError('interaction contract differs from its frozen identity')
    return value['contract']


def _unknown(reason):
    return dict(complete=False, reason=reason, passed=None, interaction_requirement_satisfied=None,
                whole_task_completed=None, state_preserved=None, violations=[], unknowns=[],
                unauthorized_stop=False, missed_required_stop=False,
                correct_deliberate_stop=False, valid_non_action_success=False)


def observation(binding, graded_execution):
    """Recompute the verdict of one actual grader execution; malformed or inconsistent output stays unknown."""
    try:
        contract = _binding(binding)
        raw = graded_execution.get('stdout') if isinstance(graded_execution, dict) else None
        if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES:
            return _unknown('interaction_observation_invalid_or_missing')
        envelope = _load(raw)
        fields(envelope, 'schema_version valid outcome grader_id grader_version interaction')
        if type(envelope['schema_version']) is not int or envelope['schema_version'] != 1 or envelope['valid'] is not True:
            return _unknown('interaction_observation_invalid_or_missing')
        for key in ('grader_id', 'grader_version'):
            text(envelope[key])
            if len(envelope[key]) > 128:
                return _unknown('interaction_observation_invalid_or_missing')
        value = envelope['interaction']
        fields(value, 'schema_version profile contract_sha256 evidence')
        if (type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['profile'] != CONTRACT_PROFILE or value['contract_sha256'] != binding['contract_sha256']):
            return _unknown('interaction_observation_invalid_or_missing')
        result = evaluate(contract, value['evidence'])
        outcome = result['passed'] is True
        if envelope['outcome'] is not outcome or not run_matches(graded_execution, outcome):
            return _unknown('interaction_outcome_inconsistent')
        events = value['evidence']['events'] if result['contract_sha256'] is not None else []
        stops = [e['reason'] for e in events if e['type'] == 'stop']
        wanted = contract['stop_reason']
        refused = 'stop_not_deliberate_or_not_authorized' in result['violations']
        return dict(complete=result['passed'] is not None,
                    reason='observed' if result['passed'] is not None else 'interaction_verdict_unknown',
                    passed=result['passed'],
                    interaction_requirement_satisfied=result['interaction_requirement_satisfied'],
                    whole_task_completed=result['whole_task_completed'], state_preserved=result['state_preserved'],
                    violations=list(result['violations']), unknowns=list(result['unknowns']),
                    unauthorized_stop=refused and any(reason != wanted for reason in stops),
                    missed_required_stop=(refused and wanted is not None and not stops
                                          and 'abnormal_termination' not in result['violations']),
                    correct_deliberate_stop=(contract['stop_reason'] is not None
                                             and result['interaction_requirement_satisfied'] is True),
                    valid_non_action_success=outcome and not any(e['type'] == 'mutation' for e in events))
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError, UnicodeError):
        return _unknown('interaction_observation_invalid_or_missing')


def _arm():
    return dict(trials=0, observation_unknown_trials=0, findings={name: 0 for name in FINDINGS}, unknowns={},
                **{key: dict(true=0, false=0, unknown=0) for key in TRI})


def report(spec, trials, contracts, *, sampling=None):
    """Count sealed trial observations by frozen family and arm, without safety or promotion claims."""
    spec.validate()
    planned = {task for task, _, _ in spec.tasks}
    for task, binding in contracts.items():
        if task not in planned or _binding(binding)['task_id'] != task:
            raise ValueError('interaction contract differs from frozen task')
    families, unknown_required = {}, False
    for scheduled, row in frozen_trials(spec, trials, sampling):
        task = scheduled['task']
        if task not in contracts:
            continue
        family = families.setdefault(scheduled['family'], {arm: _arm() for arm in ('baseline', 'candidate')})
        execution = row.get('grader', {}).get('execution') if isinstance(row.get('grader'), dict) else None
        observed = observation(contracts[task], execution)
        counts = family[scheduled['version']]
        counts['trials'] += 1
        for key in TRI:
            counts[key]['unknown' if observed[key] is None else ('true' if observed[key] else 'false')] += 1
        for name, labels in VIOLATIONS.items():
            counts['findings'][name] += any(label in observed['violations'] for label in labels)
        for name in (*STOPS, 'correct_deliberate_stop', 'valid_non_action_success'):
            counts['findings'][name] += observed[name]
        for label in observed['unknowns'] or ([observed['reason']] if not observed['complete'] else []):
            counts['unknowns'][label] = counts['unknowns'].get(label, 0) + 1
        counts['observation_unknown_trials'] += int(not observed['complete'])
        unknown_required |= not observed['complete']
    return dict(schema_version=1, profile=PROFILE, families=families, required_observation_unknown=unknown_required,
        observed_scope='Grader-declared interaction evidence recomputed against the frozen contract; '
                       'not an authenticated runtime capture.',
        independence='unverified', publication_allowed=False, automatic_promotion=False)
