"""Deterministic report-integrity fields for an improvement receipt (#696).

Every value derives from receipt facts: digests, trial outcomes, splits and a read-only
Git status. No field carries hint, candidate or fixture text, and none grants authority.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess

PROVENANCE_PROFILE = 'ahl-seeded-run-provenance-v1'
STRENGTH_PROFILE = 'ahl-evidence-strength-v1'
PRE_EDIT_PROFILE = 'ahl-pre-edit-check-v1'
# A single failing baseline trial can be chance; the label needs repeated failure of the targeted mechanism.
MIN_BASELINE_FAILURES = 2
LABELS = ('behavioral_proof', 'source_correction_without_behavioral_proof', 'insufficient')
# Split -> control the report may name. Development trials are the repair target, not a control.
CONTROLS = {'regression': 'adjacent_regression', 'heldout': 'transfer'}


def seeded_provenance(hint, source):
    """Digest-only record of a hint passed into the run.

    The proposer saw the hint, so no candidate of a seeded run is independent corroboration of it,
    whether or not the candidate resembles it. The hint text is never stored.
    """
    if hint is None:
        return dict(profile=PROVENANCE_PROFILE, agent_supplied_hypothesis=[])
    return dict(profile=PROVENANCE_PROFILE,
                agent_supplied_hypothesis=[dict(source=source, sha256=hashlib.sha256(hint.encode()).hexdigest())],
                candidate=dict(independent_corroboration=False, reason='run_seeded_by_agent_supplied_hypothesis'))


def evidence_strength(manifest, trials, *, inputs_unchanged, grader_changed=False, final_attempt=True,
                      minimum=MIN_BASELINE_FAILURES, vacuity=()):
    """Label one proposed change from paired trial outcomes alone; ``manifest`` and ``trials`` may be None.

    ``vacuity`` holds the closed-set labels of a run that proves nothing; such a run is never better than ``insufficient``.
    """
    splits = {task['id']: task.get('split') for task in (manifest or {}).get('tasks', [])}
    outcome = {(trial.get('version'), trial.get('task')): trial.get('status')
               for trial in trials or []
               if trial.get('status') in ('passed', 'failed') and trial.get('split') == splits.get(trial.get('task'))}
    def pairs(split):
        return [(outcome.get(('baseline', task)), outcome.get(('candidate', task)))
                for task, value in splits.items() if value == split]
    development = pairs('development')
    failures = sum(base == 'failed' for base, _ in development)
    exercised = bool(development) and all(base is not None for base, _ in development)
    ran = {CONTROLS[split]: all(None not in pair for pair in rows) for split in CONTROLS if (rows := pairs(split))}
    # A void comparison (changed grader/inputs, non-final attempt) validates no control, so none counts as run.
    void = grader_changed or not inputs_unchanged or not final_attempt
    run = [] if void else sorted(name for name, ok in ran.items() if ok)
    not_run = sorted(set(CONTROLS.values()) - set(run))
    everything = [(outcome.get(('baseline', task)), outcome.get(('candidate', task))) for task in splits]
    candidate_unsupported = bool(everything) and any(cand != 'passed' for _, cand in everything)
    regressed = any(pair == ('passed', 'failed') for pair in everything)
    paired_win = (not candidate_unsupported and any(pair == ('failed', 'passed') for pair in development))
    reasons = ['evaluation_vacuous', *vacuity] if vacuity else []
    if grader_changed: reasons.append('grader_changed_after_trials')
    if not inputs_unchanged: reasons.append('frozen_inputs_changed')
    if not final_attempt: reasons.append('evaluation_not_for_final_attempt')
    if not everything: reasons.append('no_covered_evaluation')
    if candidate_unsupported: reasons.append('candidate_arm_not_all_passed')
    if regressed: reasons.append('candidate_regression_observed')
    invalid = bool(reasons)
    if not invalid:
        if not exercised: reasons.append('baseline_did_not_exercise_target')
        if failures < minimum: reasons.append('baseline_failures_below_minimum')
        if not paired_win: reasons.append('no_paired_repair_observed')
        if not run: reasons.append('no_regression_or_transfer_control_ran')
    label = ('insufficient' if invalid else 'source_correction_without_behavioral_proof' if reasons
             else 'behavioral_proof')
    return dict(profile=STRENGTH_PROFILE, label=label, reasons=reasons,
                criteria=dict(baseline_exercised_target=exercised, baseline_failures=failures,
                              minimum_baseline_failures=minimum, paired_comparison_supports_repair=paired_win,
                              regression_or_transfer_control_ran=bool(run)),
                controls_run=run, controls_not_run=not_run,
                regression_statement=('regression observed' if regressed else
                    'no regression observed in the adjacent-regression control that ran' if 'adjacent_regression' in run
                    else 'regressions not tested'),
                adoption_support=label == 'behavioral_proof' and not grader_changed,
                comparison_valid_for_adoption=not (grader_changed or not inputs_unchanged),
                authority='descriptive_only', model_judgment=False)


def grader_fingerprint(manifest_path):
    """Digest of the acceptance boundary: grader argv, grader files, bundles and self-test declarations.

    ``None`` when the manifest or a declared bundle cannot be read, which compares unequal to any digest.
    """
    from .evaluation.integrity import contained, digest
    base = Path(manifest_path).parent
    try:
        manifest = json.loads(Path(manifest_path).read_text())
        files = {}
        for task in manifest['tasks']:
            for argument in task.get('grader', []):
                path = (base/argument).resolve()
                if '{' not in argument and path.is_file():
                    files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        bundles = {name: digest(contained(base, bundle['path'])) for name, bundle in manifest.get('grader_bundles', {}).items()}
        boundary = dict(graders={t['id']: t.get('grader') for t in manifest['tasks']}, files=files, bundles=bundles,
                        self_tests=manifest.get('grader_self_tests', []))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return hashlib.sha256(json.dumps(boundary, sort_keys=True).encode()).hexdigest()


def pre_edit_check(project, paths):
    """Read-only look at local uncommitted changes to the files an edit touches.

    The controller never queries a forge, so open pull requests are always ``not_checked`` here.
    """
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
    try:
        run = subprocess.run(['git', '-C', str(project), 'status', '--porcelain=v1', '-z', '--', *sorted(paths)],
                             capture_output=True, timeout=10, env=environment)
        touched = [row for row in run.stdout.split(b'\0') if row] if run.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        touched = None
    return dict(profile=PRE_EDIT_PROFILE, read_only=True,
                uncommitted_changes='not_checked' if touched is None else 'touching' if touched else 'clean',
                uncommitted_entries=len(touched or []), open_pull_requests='not_checked')
