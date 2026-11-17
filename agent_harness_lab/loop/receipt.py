"""The typed stop-reason receipt of one loop run: the `loop` block of workflow.json, its validator and the exit mapping.

build() turns the controller result and the round ledger into the block; validate_receipt() fails closed, so a run that
cannot name the criterion that stopped it, or reports an accepted result without the evaluation behind it, is an AHL defect
and never a result. The block holds counts, digests, numbers and closed labels only, never task, grader, candidate or
captured text, and it states recommendation_only, automatic_promotion false and certification unverified on every path.
"""
import json
import math

from .errors import LoopError
from .machine import OUTCOMES, STOPS as CRITERIA

PROFILE = 'ahl-loop-receipt-v1'
MAX_BYTES = 64*1024
CARRY_PROFILE = 'ahl-loop-carryover-v1'
MAX_CARRIED, MAX_HISTORY = 50, 10  # the most dispositions and earlier runs a carry_over block holds; more are pruned and marked truncated
# Each fine stop criterion and the coarse status and exit code it maps onto; the code is monitoring.state.exit_code(status).
ACCEPTED, NOT_ACCEPTED, INTERVENTION = 'accepted', 'not_accepted', 'requires_intervention'
STATUS = dict(accepted_without_change=ACCEPTED, acceptance_score_reached=ACCEPTED,
              budget_exhausted=INTERVENTION, interrupted=INTERVENTION, comparison_invalid=INTERVENTION,
              max_rounds=NOT_ACCEPTED, plateau=NOT_ACCEPTED, regression=NOT_ACCEPTED, no_actionable_mechanism=NOT_ACCEPTED,
              evidence_insufficient=NOT_ACCEPTED)
EXIT = {ACCEPTED: 0, INTERVENTION: 1, NOT_ACCEPTED: 3}
ACCEPTING = ('accepted_without_change', 'acceptance_score_reached')
# One content-free next step per criterion. None says the harness is fine or improved: an accepted stop names the bar and
# the evidence it was read on, and every other stop says what it does not show.
STEPS = dict(
    accepted_without_change='The baseline meets the declared bar on the frozen development and regression evidence, so no improvement ran and nothing changed; this is not certification, so read loop.before and run an independent certification before relying on it.',
    acceptance_score_reached='A candidate meets the declared bar on the frozen development and regression evidence; this is not certification and nothing was applied, so read loop.rounds, review the best candidate and run an independent certification before adopting it.',
    max_rounds='The round cap ended the loop with the declared bar unmet, which does not show the harness is fine; raise --max-rounds (at most 10) or lower --accept-score, then rerun.',
    plateau='Consecutive rounds produced no supported improvement, which does not show the harness is fine; read loop.rounds for the mechanisms tried, add verified outcome evidence or pass --focus, then rerun.',
    regression='A candidate made the frozen development or regression score worse and was kept out of the result; read the investigation in the private run output, revise or discard that mechanism, then rerun.',
    no_actionable_mechanism='No mechanism was left to try, which does not show the harness is fine; read loop.mechanisms for what was tried or deferred, add verified outcome evidence or pass --focus, then rerun.',
    evidence_insufficient='The evidence could not support a result, so none is claimed; read loop.before and loop.rounds for the evidence state and any vacuity label, fix the named cause, then rerun.',
    budget_exhausted='The budget could not cover improving and re-evaluating again, so the loop stopped before spending it; raise it with --timeout SECONDS or limits.trials in ahl.json, then rerun.',
    interrupted='The loop was interrupted before it reached a criterion; resume it from its round ledger with the controller, or rerun the command, which starts from the harness on disk and carries over the mechanism dispositions recorded here; read loop.rounds for what finished.',
    comparison_invalid='A frozen input differed between evaluations, which is an AHL defect and not a verdict on the harness; read the investigation in the private run output, fix the cause, add a regression test for it, then rerun.')
# The regression stop has two causes; one that a scored capability area lost paired outcomes (loop.rounds[].regressed_areas, area_gates) names the area evidence.
AREA_REGRESSION = ('A candidate lost paired outcomes in a scored capability area and was kept out of the result, even if its pooled score did not fall; '
                   'read regressed_areas in loop.rounds and area_gates in the private receipt, revise or discard that mechanism so no named area regresses, then rerun without raising the margin.')
RECONCILE = ('An evaluation or proposal may have an unresolved provider request, so the loop did not repeat it and ended interrupted; '
             'reconcile the provider records before resuming, and report consumed evaluation trials from the evaluator.')
_EVIDENCE = ('scored', 'provisional', 'no_evidence', 'blocked')
_VALIDITY = ('valid', 'vacuous', 'incomplete', 'comparison_invalid')


def status_of(stop):
    return STATUS[stop]


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _score(summary):
    """The interval of one ledger evaluation summary, or None when it was not a sound score or no root was scored.

    An evaluation with no evidence or blocked inputs carries the whole [0, 1] interval around a placeholder point, which
    must never read as a measured score of zero.
    """
    if not isinstance(summary, dict) or summary.get('sound') is not True or summary.get('evidence') not in ('scored', 'provisional'): return None
    return dict(point=summary['point'], lower=summary['lower'], upper=summary['upper'])


def _evaluation(index, verdict, summary, **extra):
    summary = summary if isinstance(summary, dict) else {}
    return dict(evaluation_id=f'evaluation-{index}', validity=verdict, evidence=summary.get('evidence'), vacuity=summary.get('vacuity'),
                trials=summary.get('trials'), score=_score(summary), **extra,
                **({'regressed_areas': summary['regressed_areas']} if summary.get('regressed_areas') else {}))


def _paired(guard):
    """The paired delta against the best and its interval, only for a run whose frozen inputs matched."""
    delta = (guard or {}).get('delta')
    if not isinstance(delta, dict) or not all(number(delta.get(name)) for name in ('mean', 'low', 'high')): return None
    return dict(delta=delta['mean'], lower=delta['low'], upper=delta['high'])


def _projection(events, guards):
    rounds, before, current = [], None, None
    for kind, payload in events:
        if kind == 'baseline_evaluated':
            before = _evaluation(0, payload.get('value'), payload.get('evaluation'), snapshot=payload.get('snapshot'))
        elif kind == 'round_started':
            current = dict(round=payload.get('round'), mechanism=payload.get('mechanism'), base=payload.get('base'), candidate=None, decision=None, disposition=None)
            rounds.append(current)
        elif kind == 'candidate_frozen' and current is not None:
            current['candidate'] = payload.get('snapshot')
        elif kind == 'candidate_evaluated' and current is not None:
            index = len(rounds)
            current.update(_evaluation(index, payload.get('value'), payload.get('evaluation')), paired=_paired(guards[index] if index < len(guards) else None))
        elif kind == 'decided' and current is not None:
            current.update(decision=payload.get('value'), disposition=payload.get('disposition'))
            if payload.get('abandoned') is True: current['abandoned'] = True  # an interrupted round: no evaluation completed, so no score
    return before, rounds


def build(spec, result, events, guards, *, budget=None, deferrals=()):
    """Build the receipt from the controller result, ledger events and paired guards."""
    before, rounds = _projection(events, guards)
    best = result['best']
    best_round = next((item['round'] for item in rounds if item['candidate'] == best['snapshot'] and item['decision'] in ('accepted', 'improved')), 0)
    after = None if before is None else dict(round=best_round, snapshot=best['snapshot'],
                                              **(_evaluation(best_round, 'valid', best['evaluation']) if best_round else
                                                 {key: before[key] for key in ('evaluation_id', 'validity', 'evidence', 'vacuity', 'trials', 'score')}))
    stop = result['stop']
    return dict(profile=PROFILE, schema_version=1, spec=spec.to_dict(), frozen_inputs_sha256=result['frozen_inputs_sha256'], mode=result['mode'],
                acceptance_source=result['acceptance_source'], looks=result['looks'], alpha_per_look=result['per_look_alpha'],
                before=before, after=after, rounds=rounds, best_round=best_round, stop_criterion=stop, stop_reason=result['stop_reason'],
                mechanisms=result['mechanisms'], deferrals=list(deferrals), usage=result['usage'], events=result['ledger']['events'],
                ledger_head_sha256=result['ledger']['head_sha256'], budget_stages=budget,
                recommendation_only=True, automatic_promotion=False, certification='unverified',
                remediation=RECONCILE if result['stop_reason'] == 'unresolved_request' else AREA_REGRESSION if stop == 'regression' and rounds and rounds[-1].get('regressed_areas') else STEPS[stop],
                **({'resumed': result['resumed']} if 'resumed' in result else {}))


def verify_projection(block, events):
    """Check receipt values that the verified ledger can establish."""
    from agent_harness_lab.private_journal import encoded
    before, rounds = _projection(events, ())
    # Paired statistics are not recorded in the ledger and cannot be checked here.
    saved_rounds = [{key: value for key, value in item.items() if key != 'paired'} for item in block['rounds']]
    expected_rounds = [{key: value for key, value in item.items() if key != 'paired'} for item in rounds]
    first, last = events[0][1], events[-1][1]
    best_snapshot = last.get('best')
    best_round = next((item['round'] for item in rounds if item['candidate'] == best_snapshot and item['decision'] in ('accepted', 'improved')), 0)
    best_evaluation, candidate_evaluation = None, None
    for kind, payload in events:
        if kind == 'candidate_evaluated': candidate_evaluation = payload.get('evaluation')
        elif kind == 'decided' and payload.get('value') in ('accepted', 'improved'):
            best_evaluation = candidate_evaluation
    after = None if before is None else dict(round=best_round, snapshot=best_snapshot,
        **(_evaluation(best_round, 'valid', best_evaluation) if best_round else
           {key: before[key] for key in ('evaluation_id', 'validity', 'evidence', 'vacuity', 'trials', 'score')}))
    expected = dict(before=before, after=after, rounds=expected_rounds, best_round=best_round,
                    frozen_inputs_sha256=first.get('frozen_inputs_sha256'), mode=first.get('mode'),
                    looks=first.get('looks'), alpha_per_look=first.get('per_look_alpha'),
                    stop_reason=last.get('reason'), mechanisms=last.get('mechanisms'))
    actual = {key: saved_rounds if key == 'rounds' else block.get(key) for key in expected}
    if encoded(actual) != encoded(expected): _fail('ledger_projection')


def _fail(field):
    raise LoopError('loop_receipt_invalid', field, 'declared')


def _check_numbers(item, field):
    """A score has an evaluation id; its interval is finite numbers in [0, 1] ordered lower <= point <= upper."""
    if not isinstance(item, dict): _fail(field)
    score = item.get('score')
    if score is None: return
    if not isinstance(item.get('evaluation_id'), str) or not item['evaluation_id']: _fail(f'{field}.evaluation_id')
    if (not isinstance(score, dict) or not all(number(score.get(name)) for name in ('point', 'lower', 'upper'))
            or not 0 <= score['lower'] <= score['point'] <= score['upper'] <= 1): _fail(f'{field}.score')
    paired = item.get('paired')
    if paired is not None and (not isinstance(paired, dict) or not all(number(paired.get(name)) for name in ('delta', 'lower', 'upper'))):
        _fail(f'{field}.paired')


def _sound(item):
    """The evaluation behind an accepted stop: valid, scored and with no vacuity label and a score."""
    return (isinstance(item, dict) and item.get('validity') == 'valid' and item.get('evidence') == 'scored' and item.get('vacuity') is None
            and item.get('score') is not None)


def _check_carry(carry):
    """What an earlier run handed over is bounded, labelled, and states no authority; a historical score is never labelled fresh."""
    if (not isinstance(carry, dict) or carry.get('profile') != CARRY_PROFILE or carry.get('recommendation_only') is not True
            or carry.get('automatic_promotion') is not False): _fail('carry_over')
    held, history = carry.get('mechanisms') or {}, carry.get('history') or []
    if (not isinstance(held, dict) or not isinstance(history, list) or len(history) > MAX_HISTORY
            or any(len(held.get(name) or ()) > MAX_CARRIED for name in ('carried', 'reopened'))): _fail('carry_over.size')
    if carry.get('historical') is not None and (not isinstance(carry['historical'], dict) or carry['historical'].get('label') != 'historical_observation'): _fail('carry_over.historical')
    if carry.get('fresh') is not None and (not isinstance(carry['fresh'], dict) or carry['fresh'].get('label') != 'fresh_evaluation'): _fail('carry_over.fresh')
    if carry.get('comparable') is False and 'fresh_baseline_minus_historical_best' in carry: _fail('carry_over.delta')  # no delta across other frozen inputs


def validate_receipt(value):
    """Return the block when it is complete and honest; otherwise raise loop_receipt_invalid naming the first field that is not.

    Fails closed: a missing or unknown stop criterion, an accepted stop with no post-improvement evaluation (except
    accepted_without_change, which has no improvement), a number with no evaluation id, a vacuous or unscored evaluation behind an accepted stop,
    an authority field other than recommendation only, and a receipt over MAX_BYTES.
    """
    if not isinstance(value, dict) or value.get('profile') != PROFILE or type(value.get('schema_version')) is not int or value.get('schema_version') != 1: _fail('profile')
    stop = value.get('stop_criterion')
    if stop not in CRITERIA: _fail('stop_criterion')
    if (value.get('recommendation_only') is not True or value.get('automatic_promotion') is not False
            or value.get('certification') != 'unverified'): _fail('authority')
    if not isinstance(value.get('remediation'), str) or not value['remediation']: _fail('remediation')
    if not isinstance(value.get('spec'), dict) or not isinstance(value['spec'].get('digest'), str): _fail('spec')
    if not isinstance(value.get('frozen_inputs_sha256'), str) or len(value['frozen_inputs_sha256']) != 64: _fail('frozen_inputs_sha256')
    rounds = value.get('rounds')
    if not isinstance(rounds, list) or len(rounds) > 10: _fail('rounds')
    before = value.get('before')
    if before is not None: _check_numbers(before, 'before')
    after = value.get('after')
    if after is not None: _check_numbers(after, 'after')
    for index, item in enumerate(rounds):
        _check_numbers(item, f'rounds.{index}')
        if item.get('decision') is not None and item['decision'] not in OUTCOMES: _fail(f'rounds.{index}.decision')
    if stop == 'accepted_without_change':
        if rounds or not _sound(before): _fail('before')
    elif stop == 'acceptance_score_reached':
        last = rounds[-1] if rounds else None  # an accepted result needs the evaluation that followed an improvement
        if last is None or last.get('decision') != 'accepted' or not _sound(last) or not _sound(before): _fail('rounds')
    carry = value.get('carry_over')
    if carry is not None: _check_carry(carry)
    resumed = value.get('resumed')
    if resumed is not None and (not isinstance(resumed, dict) or not all(type(resumed.get(name)) is int and resumed[name] >= 0 for name in ('count', 'abandoned_rounds'))): _fail('resumed')
    try: size = len(json.dumps(value, allow_nan=False).encode())
    except (TypeError, ValueError): _fail('value')
    if size > MAX_BYTES: _fail('size')
    return value
