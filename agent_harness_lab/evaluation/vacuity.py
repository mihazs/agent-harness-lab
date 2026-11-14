"""Vacuity labels: why an evaluation proves nothing, derived from recorded trials only (#805).

The one closed set shared by every verdict surface and by the loop validity gate. A labelled evaluation never produces an
improvement, a pass or a recommendation. Labels read recorded rows and digests; no field carries task, grader or captured
text, and an unknown value never becomes a success, failure or independence label.
"""
NO_ELIGIBLE_CASES = 'no_eligible_cases'
ALL_OUTCOMES_UNKNOWN = 'all_outcomes_unknown'
ARMS_IDENTICAL = 'arms_identical'
GRADER_NOT_EXECUTED = 'grader_not_executed'
ZERO_TRIALS = 'zero_trials'
LABELS = (NO_ELIGIBLE_CASES, ALL_OUTCOMES_UNKNOWN, ARMS_IDENTICAL, GRADER_NOT_EXECUTED, ZERO_TRIALS)
LABEL_SET = frozenset(LABELS)
PROFILE = 'ahl-vacuity-v1'
REASON = 'evaluation_vacuous'
GRADED = ('passed', 'failed')


def _rows(trials):
    return [trial for trial in trials if isinstance(trial, dict)] if isinstance(trials, list) else []


def _no_eligible_cases(tasks, trials, baseline, candidate):
    """No declared case with a usable identifier: nothing could have been measured."""
    return not any(isinstance(task, dict) and isinstance(task.get('id'), str) and task['id'] for task in tasks or [])


def _all_outcomes_unknown(tasks, trials, baseline, candidate):
    """Trials exist but none ended passed or failed: every outcome is an infrastructure or grading unknown."""
    rows = _rows(trials)
    return bool(rows) and not any(row.get('status') in GRADED for row in rows)


def _arms_identical(tasks, trials, baseline, candidate):
    """Both arms are the same frozen harness state, so any difference between them is noise."""
    return isinstance(baseline, str) and bool(baseline) and baseline == candidate


def _grader_not_executed(tasks, trials, baseline, candidate):
    """Trials exist but none carries a recorded grader execution, so no verdict traces to a grader run."""
    rows = _rows(trials)
    return bool(rows) and not any(isinstance(row.get('grader'), dict) for row in rows)


def _zero_trials(tasks, trials, baseline, candidate):
    return not _rows(trials)


# One predicate per label, in LABELS order; a test replaces one to prove the matching case notices.
RULES = {NO_ELIGIBLE_CASES: _no_eligible_cases, ALL_OUTCOMES_UNKNOWN: _all_outcomes_unknown,
         ARMS_IDENTICAL: _arms_identical, GRADER_NOT_EXECUTED: _grader_not_executed, ZERO_TRIALS: _zero_trials}


def classify(tasks, trials, *, baseline_sha256=None, candidate_sha256=None):
    """The vacuity labels that apply, in closed-set order; [] means the recorded trials are not vacuous.

    ``tasks`` are the declared cases, ``trials`` the recorded trial rows and the digests the frozen arm identities
    (None when unknown, which never labels the arms identical).
    """
    return [label for label in LABELS if RULES[label](tasks, trials, baseline_sha256, candidate_sha256)]


def classify_run(run):
    """Labels for one recorded paired run: a dict with manifest, trials and optional bindings of arm digests."""
    bindings = run.get('bindings') or {}
    return classify((run.get('manifest') or {}).get('tasks'), run.get('trials'),
                    baseline_sha256=bindings.get('baseline_sha256'), candidate_sha256=bindings.get('candidate_sha256'))


def validate(labels):
    """The labels as a list when every one belongs to the closed set; anything else is a defect, not a label."""
    labels = list(labels)
    if not labels or any(label not in LABEL_SET for label in labels) or len(set(labels)) != len(labels):
        raise ValueError(f'vacuity labels must be a non-empty subset of {", ".join(LABELS)}')
    return labels


def receipt(labels):
    """The receipt block of a vacuous evaluation; it grants no authority and names the labels only."""
    return dict(profile=PROFILE, vacuous=True, labels=validate(labels), improvement_allowed=False)


def refuse_noise(spec, result):
    """``result`` unless it claims support for arms with one harness hash, which is blocked: that difference is noise.

    A control that runs one harness against itself and finds no effect keeps its analysis; only a positive claim is refused.
    The plan still schedules and executes, so mechanics checks and A/A controls can run.
    """
    if result.get('status') == 'supported' and RULES[ARMS_IDENTICAL](None, None, spec.baseline_hash, spec.candidate_hash):
        return dict(result, status='blocked', reason=ARMS_IDENTICAL, vacuity=receipt([ARMS_IDENTICAL]), automatic_promotion=False)
    return result
