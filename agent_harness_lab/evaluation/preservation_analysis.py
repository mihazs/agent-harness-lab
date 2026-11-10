"""Conditional preservation math; frozen membership is not qualification authority."""
from collections import defaultdict

from agent_harness_lab.private_journal import encoded, record_bytes
from .cohorts import MAX_BYTES, _manifest
from .plan import schedule
from .statistics import bound, incomplete_interval

ARMS = ('baseline', 'candidate')
TRANSITIONS = ('pass_pass', 'fail_pass', 'pass_fail', 'fail_fail', 'missing', 'invalid', 'unknown')
ASSUMPTIONS = ['Membership, margins and weights were fixed before comparison outcomes.',
               'Original roots are independent; root labels do not establish that assumption.',
               'Root differences lie in [-1, 1]; related tasks and repetitions remain within their original root.',
               'Bonferroni permits dependence between overlapping endpoints; overlap adds no independent roots.']


def _group(pairs, outcomes):
    transitions = dict.fromkeys(TRANSITIONS, 0)
    arms = {arm: dict(planned=len(pairs), passed=0, failed=0, missing=0, invalid=0, unknown=0) for arm in ARMS}
    for task, repetition in pairs:
        states = [outcomes[(task, repetition, arm)] for arm in ARMS]
        if any(state == 'missing' for state in states):
            transition = 'missing'
        elif any(state == 'invalid' for state in states):
            transition = 'invalid'
        elif any(state == 'unknown' for state in states):
            transition = 'unknown'
        else:
            transition = '_'.join('pass' if state is True else 'fail' for state in states)
        transitions[transition] += 1
        for arm, state in zip(ARMS, states):
            key = 'passed' if state is True else 'failed' if state is False else state
            arms[arm][key] += 1
    for counts in arms.values():
        counts['success_rate'] = None if any(counts[k] for k in ('missing', 'invalid', 'unknown')) else counts['passed'] / counts['planned']
    return dict(planned_pairs=len(pairs), transitions=transitions, arms=arms)


def _observed(spec, records, expected):
    indexed = defaultdict(list)
    invalid_records = 0
    for record in records:
        ident = getattr(record, 'trial_id', None)
        if not isinstance(ident, str):
            invalid_records += 1
        else:
            indexed[ident].append(record)
    outcomes = {}
    provenance_bad, invalid, unknown, critical = 0, 0, 0, False
    for ident, trial in expected.items():
        rows = indexed.get(ident, [])
        state = 'missing' if not rows else 'invalid'
        for record in rows:
            hashes = tuple(getattr(record, key, None) for key in ('baseline_hash', 'candidate_hash', 'grader_hash', 'runtime_hash'))
            matched = hashes == (spec.baseline_hash, spec.candidate_hash, spec.grader_hash, spec.runtime_hash)
            provenance_bad += int(not matched)
            critical |= (matched and getattr(record, 'status', None) == 'graded'
                         and getattr(record, 'provenance', None) == 'executable_grader'
                         and getattr(record, 'critical_regression', None) is True)
        if len(rows) == 1:
            record = rows[0]
            if not matched or getattr(record, 'status', None) != 'graded' or getattr(record, 'provenance', None) != 'executable_grader':
                invalid += 1
            elif record.outcome is None:
                state, unknown = 'unknown', unknown + 1
            elif type(record.outcome) is not bool or type(record.critical_regression) is not bool:
                invalid += 1
            else:
                state = record.outcome
        elif rows:
            invalid += 1
        outcomes[(trial['task'], trial['repetition'], trial['version'])] = state
    coverage = dict(planned_trials=len(expected), received_records=len(records),
        missing_trials=sum(ident not in indexed for ident in expected),
        duplicate_trial_ids=sum(len(rows) > 1 for rows in indexed.values()),
        duplicate_records=sum(max(0, len(rows) - 1) for rows in indexed.values()),
        unexpected_trial_ids=sorted(set(indexed) - set(expected)),
        invalid_records=invalid_records, invalid_grades=invalid, unknown_grades=unknown,
        provenance_mismatches=provenance_bad)
    return outcomes, coverage, critical


def _root_values(tasks, pairs, outcomes):
    roots = defaultdict(list)
    for task, repetition in pairs:
        roots[tasks[task][0]].append(int(outcomes[(task, repetition, 'candidate')]) - int(outcomes[(task, repetition, 'baseline')]))
    return {root: sum(values) / len(values) for root, values in sorted(roots.items())}


def _root_ranges(tasks, pairs, outcomes):
    roots = defaultdict(list)
    for task, repetition in pairs:
        baseline, candidate = (outcomes[(task, repetition, arm)] for arm in ARMS)
        lo = (int(candidate) if type(candidate) is bool else 0) - (int(baseline) if type(baseline) is bool else 1)
        hi = (int(candidate) if type(candidate) is bool else 1) - (int(baseline) if type(baseline) is bool else 0)
        roots[tasks[task][0]].append((lo, hi))
    return [(root, sum(v[0] for v in values) / len(values), sum(v[1] for v in values) / len(values))
            for root, values in sorted(roots.items())]


def analyze(spec, records, binding, *, root_similarity_evidence=None, root_similarity_bank=None):
    """Analyze complete paired data; callers independently verify live qualification."""
    from .root_similarity import binding as similarity_binding
    try:
        groups = similarity_binding(spec, root_similarity_evidence, root_similarity_bank)
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        return dict(status='blocked', reason='root_similarity_binding_missing_or_invalid', automatic_promotion=False)
    expected = {row['trial_id']: row for row in schedule(spec)}
    records = list(records)
    outcomes, coverage, critical = _observed(spec, records, expected)
    tasks = {task: (root, family) for task, root, family in spec.tasks}
    root_families = {root: family for root, family in tasks.values()}
    pairs = sorted((task, repetition) for task in tasks for repetition in range(spec.repeats))
    by_root = {root: [pair for pair in pairs if tasks[pair[0]][0] == root] for root in sorted({r for r, _ in tasks.values()})}
    descriptive = dict(overall=_group(pairs, outcomes),
        tasks={task: _group([pair for pair in pairs if pair[0] == task], outcomes) for task in sorted(tasks)},
        roots={root: _group(selected, outcomes) for root, selected in by_root.items()}, coverage=coverage)
    preservation = dict(manifest_sha256=spec.cohort_manifest_sha256, cohorts={}, descriptive=descriptive,
        assumptions=ASSUMPTIONS.copy(), qualification_authority='not_validated_by_conditional_math',
        independence='unverified', publication_allowed=False, automatic_promotion=False)
    result = dict(preservation=preservation, roots=len(by_root), repetitions=spec.repeats, trials=len(records),
                  publication_allowed=False, automatic_promotion=False)
    problem = None
    try:
        if not isinstance(binding, dict) or binding.get('manifest_sha256') != spec.cohort_manifest_sha256:
            raise ValueError('missing or mismatched cohort binding')
        raw = binding['manifest_json']
        if isinstance(raw, str):
            if len(raw) > MAX_BYTES:
                raise ValueError('raw preservation manifest exceeds byte bound')
            raw = raw.encode('utf-8')
        parsed, actual_sha256 = record_bytes(raw, MAX_BYTES)
        if actual_sha256 != spec.cohort_manifest_sha256:
            raise ValueError('raw preservation manifest differs from frozen plan')
        manifest = _manifest(parsed, spec.tasks)
        embedded = _manifest(binding['manifest'], spec.tasks)
        if encoded(manifest) != encoded(embedded):
            raise ValueError('embedded preservation manifest differs from raw bytes')
        for cohort in manifest['cohorts']:
            if cohort['qualification']['plan_sha256'] == spec.digest:
                raise ValueError('same-analysis qualification cannot establish preservation membership')
        if any(set(saved.get('trial_ids', [])) & set(expected) for saved in binding.get('qualification_bindings', {}).values()):
            raise ValueError('qualification trials overlap the current comparison')
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        problem = 'preservation_binding_missing_or_invalid'
    if problem is None:
        families = sorted({family for _, family in tasks.values()})
        required = sum(cohort['required'] for cohort in manifest['cohorts'])
        adjusted = spec.alpha / (1 + len(families) + required)
        preservation['error_allocation'] = dict(method='Bonferroni', total_alpha=spec.alpha,
            primary_endpoints=1, family_endpoints=len(families), required_cohort_endpoints=required,
            per_endpoint_alpha=adjusted, optional_cohort_endpoints=0)
        for cohort in sorted(manifest['cohorts'], key=lambda c: c['cohort_id']):
            members = {m['task_id'] for m in cohort['members']}
            selected = [pair for pair in pairs if pair[0] in members]
            preservation['cohorts'][cohort['cohort_id']] = dict(required=cohort['required'], role=cohort['role'],
                margin=cohort['margin'], exposure=cohort['exposure'], severity_policy=cohort['severity_policy'],
                members=sorted(m['task_id'] for m in cohort['members']), descriptive=_group(selected, outcomes),
                endpoint=None, disposition='incomplete' if cohort['required'] else 'descriptive_only')
    if critical:
        return dict(result, status='rejected', reason='critical_regression')
    if problem:
        return dict(result, status='blocked', reason=problem)
    if coverage['provenance_mismatches']:
        return dict(result, status='blocked', reason='provenance_mismatch')
    if (coverage['missing_trials'] or coverage['duplicate_records'] or coverage['unexpected_trial_ids']
            or coverage['invalid_records'] or coverage['invalid_grades'] or coverage['unknown_grades']):
        if not (coverage['duplicate_records'] or coverage['unexpected_trial_ids'] or coverage['invalid_records']):
            root_ranges = _root_ranges(tasks, pairs, outcomes)
            family_rows = {family: [row for row in root_ranges if root_families[row[0]] == family] for family in families}
            family_bounds = {family: incomplete_interval(rows, [1 / len(rows)] * len(rows), adjusted, groups)
                             for family, rows in family_rows.items()}
            weighted, weights = [], []
            for family, weight in sorted(spec.family_weights):
                weighted.extend(family_rows[family]); weights.extend([weight / len(family_rows[family])] * len(family_rows[family]))
            cohort_bounds = {}
            for cohort in manifest['cohorts']:
                if cohort['required']:
                    selected = {member['task_id'] for member in cohort['members']}
                    rows = _root_ranges(tasks, [pair for pair in pairs if pair[0] in selected], outcomes)
                    cohort_bounds[cohort['cohort_id']] = incomplete_interval(rows, [1 / len(rows)] * len(rows), adjusted, groups)
            result['missing_outcome_bounds'] = dict(
                estimand='frozen_family_weighted_mean_of_selected_root_mean_paired_correctness_differences',
                interpretation='worst_and_best_possible_outcomes_not_imputed',
                unknown_arms=coverage['missing_trials'] + coverage['invalid_grades'] + coverage['unknown_grades'],
                primary=incomplete_interval(weighted, weights, adjusted, groups), families=family_bounds,
                required_cohorts=cohort_bounds, cohort_estimand='equal_selected_cohort_root_mean_paired_difference',
                method='weighted_root_Hoeffding', assumptions='independent_root_means_unverified',
                multiplicity='Bonferroni_primary_families_required_cohorts', automatic_promotion=False)
        return dict(result, status='incomplete', reason='missing_duplicate_unknown_or_invalid_trials')
    root_values = _root_values(tasks, pairs, outcomes)
    family_values = {family: {root: value for root, value in root_values.items() if root_families[root] == family}
                     for family in families}
    family_results = {family: bound(list(values.values()), [1 / len(values)] * len(values), adjusted,
                                    roots=list(values), groups=groups) for family, values in family_values.items()}
    values, weights, root_ids = [], [], []
    for family, weight in sorted(spec.family_weights):
        values.extend(family_values[family].values())
        root_ids.extend(family_values[family])
        weights.extend([weight / len(family_values[family])] * len(family_values[family]))
    primary = bound(values, weights, adjusted, roots=root_ids, groups=groups)
    dispositions = []
    for cohort in manifest['cohorts']:
        if not cohort['required']:
            continue
        item = preservation['cohorts'][cohort['cohort_id']]
        members = set(item['members'])
        selected = [pair for pair in pairs if pair[0] in members]
        selected_roots = _root_values(tasks, selected, outcomes)
        endpoint = bound(list(selected_roots.values()), [1 / len(selected_roots)] * len(selected_roots), adjusted,
                         roots=list(selected_roots), groups=groups)
        endpoint.update(weighting='equal_original_roots', root_ids=sorted(selected_roots),
                        assumptions=ASSUMPTIONS.copy())
        disposition = ('rejected' if endpoint['high'] < -cohort['margin'] else
                       'supported' if endpoint['independent_roots'] >= spec.min_roots and endpoint['low'] >= -cohort['margin'] else 'inconclusive')
        item.update(endpoint=endpoint, disposition=disposition)
        dispositions.append(disposition)
    harmful = (primary['high'] < -spec.family_margin or any(v['high'] < -spec.family_margin for v in family_results.values())
               or 'rejected' in dispositions)
    passes = (primary['independent_roots'] >= spec.min_roots and primary['low'] > spec.effect_margin
              and all(v['low'] >= -spec.family_margin for v in family_results.values())
              and all(d == 'supported' for d in dispositions))
    return dict(result, status='rejected' if harmful else 'supported' if passes else 'inconclusive',
        reason='preservation_regression' if 'rejected' in dispositions else 'correctness_regression' if harmful else
               'fixed_analysis_criteria_met' if passes else 'uncertainty_or_margin_not_met',
        primary=primary, families=family_results, multiplicity='Bonferroni_primary_families_required_cohorts',
        rare_harm_risk='not_established_by_zero_incidents')
