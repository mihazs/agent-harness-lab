"""Candidate mechanisms and repair classes for ignored instructions, and a recorded-pair screen for one repair (part of #656).

`diagnose` reads an instruction-adherence report and the harness text and lists, per finding, the mechanisms the evidence
leaves open, each with the repair class it would call for and its competing explanations. Every entry is a hypothesis and a
recommendation: nothing here names a cause, authorizes a change or edits a user's prompt. `judge` screens one repair against
recorded baseline and candidate arms with the adherence grader: it rejects a repair that deletes a checkable rule or loses a
preserved task, and otherwise says only whether the recorded pair separates the arms. It is not the controlled experiment gate,
so it never reports a validated improvement. Unknown capture, loading and lineage never become a success, a failure or an
independent root.
"""
import argparse
from collections import Counter
from pathlib import Path

from ..evaluation.power import wilson
from . import instruction_adherence as adherence
from . import instruction_rules as rules

PLAN_PROFILE = 'ahl-instruction-repair-plan-v1'
VERDICT_PROFILE = 'ahl-instruction-repair-verdict-v1'
ARTIFACT = 'instruction-repair-plan.json'
# Documented defaults, not measured limits: the instruction budget belongs to its own check, and a hypothesis only names a fact.
LONG_SESSION_TURNS = 200
LONG_SOURCE_LINES = 200
ORDER = ('not_in_context', 'conflicting_instructions', 'buried_or_duplicated', 'unobservable_wording', 'prose_only_rule', 'context_loss',
         'no_self_check_for_user_instruction')
REPAIR = dict(not_in_context='restore_loading', conflicting_instructions='resolve_conflict', buried_or_duplicated='shorten_dedupe_reorder',
              unobservable_wording='reword_observable', prose_only_rule='hook_or_feedback_sensor', context_loss='re_ground_after_compaction',
              no_self_check_for_user_instruction='pre_action_checklist_or_echo')
ROUTE = dict(prose_only_rule='hook', context_loss='state')
ENFORCEABLE = ('forbidden_action_taken', 'approval_not_sought', 'scope_exceeded', 'required_step_skipped', 'order_violated')
CLAIMED = ('observed_violation', 'possible_violation')
# What a finding says about recorded compactions, and the support fact it adds to context_loss.
COMPACTION = dict(in_window='compaction_in_window', none_in_window='no_compaction_in_window', not_recorded='compaction_not_recorded')
UNASSESSED = (dict(mechanism='skill_not_activated', reason='the adherence report carries no skill activation evidence'),)
GUARDS = ('never_weaken_delete_or_bypass_a_safety_rule', 'never_edit_a_protected_or_out_of_scope_surface')
SORT = ('violated', 'unknown', 'satisfied')
LIMITATIONS = (
    'Every hypothesis is a candidate explanation from structural facts of one finding and the harness text; none is a diagnosis.',
    'A repair class is a recommendation: only a fresh controlled paired experiment can support an improvement, and applying it needs the existing application gate.',
    'A compaction is read only from a session that records it, and then only that one fell after the instruction came into view; whether it removed the instruction from view, skill activation and whether a hook already enforces a rule are not read, so a hypothesis that depends on them stays not_excluded or unassessed.')
VERDICT_LIMITATIONS = (
    'The screen grades recorded arms with the adherence detector: lexical rules over tool calls, three-valued, with a rule credited only when it was loaded.',
    'An unknown root is counted in the direction that makes the comparison harder to pass; a session with no declared root is excluded, never an independent root.',
    'Supporting the recorded pair is not a validated improvement: the controlled paired experiment gate and the application gate still decide.')


def _hypothesis(mechanism, basis, support, repair=None):
    return dict(mechanism=mechanism, basis=basis, support=support, repair_class=repair or REPAIR[mechanism], route=ROUTE.get(mechanism, 'instruction'), status='hypothesis')


def _placement(ref, rows, texts):
    """Structural facts about where a rule sits in the harness: duplicated, or late in a long file."""
    mine = [row for row in rows if row['ref'] == ref]
    if not mine:
        return []
    key = rules.key(mine[0]['rule'])
    support = ['duplicated'] if sum(1 for row in rows if row['rule'] and rules.key(row['rule']) == key) > 1 else []
    for row in mine:
        size = texts[row['source']].count('\n') + 1
        if size >= LONG_SOURCE_LINES and row['line'] > size / 2:
            support.append('late_in_long_source')
            break
    return support


def _hypotheses(finding, rows, texts):
    status, direction, loading = finding['status'], finding['direction'], finding['loading']
    if status == 'conflict':
        return [_hypothesis('conflicting_instructions', 'observed', ['status=conflict'])]
    if status == 'not_loaded' or loading == 'not_loaded':
        return [_hypothesis('not_in_context', 'observed', ['status=not_loaded'])]
    found = []
    if direction == 'harness':
        if loading == 'truncated':
            found.append(_hypothesis('not_in_context', 'observed', ['loading=truncated'], 'shorten_dedupe_reorder'))
        elif loading == 'unknown':
            found.append(_hypothesis('not_in_context', 'not_excluded', ['loading_unrecorded']))
        if placement := _placement(finding['instruction_ref'], rows, texts):
            found.append(_hypothesis('buried_or_duplicated', 'observed', placement))
        if any(row['ref'] == finding['instruction_ref'] and rules.hedged(row['sentence']) for row in rows):
            found.append(_hypothesis('unobservable_wording', 'observed', ['hedged_wording']))
        if finding['mechanism'] in ENFORCEABLE and loading in ('loaded', 'unknown'):
            # Only the broken rule is observed; whether a hook exists and failed is not read, so the hypothesis never gets the observed basis.
            found.append(_hypothesis('prose_only_rule', 'not_excluded', ['mechanism=' + finding['mechanism'], 'loaded_rule_broken' if loading == 'loaded' else 'loading_unrecorded']))
    if direction == 'user_prompt':
        found.append(_hypothesis('no_self_check_for_user_instruction', 'not_excluded', ['direction=user_prompt', 'mechanism=' + finding['mechanism']]))
    # A report made before compactions were recorded has no field: that is not recorded, never none.
    compaction = finding.get('compaction', 'not_recorded')
    late = finding['span']['last_turn'] >= LONG_SESSION_TURNS
    if late or compaction == 'in_window':
        # A recorded compaction is a fact beside the hypothesis, not its basis: whether the instruction left the context is not read.
        found.append(_hypothesis('context_loss', 'not_excluded', [*(['late_in_session'] if late else []), COMPACTION[compaction]]))
    found.sort(key=lambda row: ORDER.index(row['mechanism']))
    return found


def _check(report):
    fields = ('finding_id', 'instruction_ref', 'direction', 'status', 'mechanism', 'loading', 'source_layer', 'span', 'root_ref', 'session_ref')
    if not isinstance(report, dict) or report.get('profile') != adherence.PROFILE or not all(isinstance(report.get(name), list) for name in ('findings', 'abstentions', 'recurrence')):
        raise ValueError('report must be an %s report' % adherence.PROFILE)
    if not all(isinstance(row, dict) and set(fields) <= set(row) and isinstance(row['span'], dict) and type(row['span'].get('last_turn')) is int
               and row.get('compaction', 'not_recorded') in list(COMPACTION) for row in report['findings']):
        raise ValueError('report findings differ from the %s schema' % adherence.PROFILE)


def diagnose(report, harness):
    """The recommendation-only plan for the findings of one adherence report; abstentions are counted and not diagnosed."""
    _check(report)
    rows, _ = adherence.inventory(harness)
    texts = {f"{source['layer']}:{source['name']}": source['text'] for source in harness}
    recurrence = {(row['instruction_ref'], row['mechanism'], row['status'], row['direction']): row for row in report['recurrence']}
    candidates, by_class, by_mechanism = [], Counter(), Counter()
    for finding in sorted(report['findings'], key=lambda row: row['finding_id']):
        found = _hypotheses(finding, rows, texts)
        names = [row['mechanism'] for row in found]
        for row in found:
            row['competing'] = [name for name in names if name != row['mechanism']] + ['agent_error_unrelated_to_harness']
        seen = recurrence.get((finding['instruction_ref'], finding['mechanism'], finding['status'], finding['direction']))
        seen = {name: seen[name] for name in ('sessions', 'independent_roots', 'unknown_lineage_sessions')} if seen else dict(
            sessions=1, independent_roots=int(bool(finding['root_ref'])), unknown_lineage_sessions=int(not finding['root_ref']))
        limits = (['lineage_unknown'] if seen['unknown_lineage_sessions'] else []) + (['single_independent_root'] if seen['independent_roots'] < 2 else []) + (
            [] if found else ['no_supported_mechanism'])
        user = finding['direction'] in ('user_prompt', 'mixed')
        guards = [*GUARDS, *(['never_edit_the_users_prompt'] if user else []), *(['global_scope_must_be_explicit'] if finding['source_layer'] in ('global', 'mixed') else [])]
        candidates.append(dict(finding_id=finding['finding_id'], instruction_ref=finding['instruction_ref'], direction=finding['direction'],
                               source_layer=finding['source_layer'], status=finding['status'], finding_mechanism=finding['mechanism'], recurrence=seen,
                               hypotheses=found, disposition='recommendation_only', requires=['reproduction_task', 'paired_evaluation'],
                               limits=limits, guards=guards))
        by_class.update({row['repair_class'] for row in found})
        by_mechanism.update(set(names))
    return dict(profile=PLAN_PROFILE, authority='recommendation_only', causal_diagnosis=False, change_authorized=False, promotion_authority=False,
                publication_allowed=False, redaction='digest_only',
                source=dict(profile=report['profile'], findings=len(report['findings']), abstentions=len(report['abstentions'])), candidates=candidates,
                counts=dict(by_repair_class=dict(sorted(by_class.items())), by_mechanism=dict(sorted(by_mechanism.items()))),
                unassessed=[dict(row) for row in UNASSESSED], limitations=list(LIMITATIONS))


def section(plan):
    """Counts only, for the analysis brief; the digest-only plan stays in the private artifact."""
    return dict(status='completed', profile=plan['profile'], authority=plan['authority'], artifact=ARTIFACT, candidates=len(plan['candidates']),
                abstentions_not_diagnosed=plan['source']['abstentions'], by_repair_class=plan['counts']['by_repair_class'],
                by_mechanism=plan['counts']['by_mechanism'], unassessed=[row['mechanism'] for row in plan['unassessed']])


def _roots(raw, rows):
    """({root ref: {rule key: outcome}}, sessions without a declared root): a root's outcome is the worst over its sessions."""
    roots, excluded = {}, 0
    for session in raw:
        if session['root_ref'] is None:
            excluded += 1
            continue
        into = roots.setdefault(session['root_ref'], {})
        for key, outcome in adherence.outcomes(session, rows).items():
            into[key] = min(into.get(key, outcome), outcome, key=SORT.index)
    return roots, excluded


def _arm(roots, excluded, key):
    outcome = Counter(row.get(key, 'unknown') for row in roots.values())
    return dict(roots=len(roots), followed=outcome['satisfied'], violated=outcome['violated'], unknown=outcome['unknown'], unknown_lineage_excluded=excluded)


def _pairs(before, after, skip=None):
    """Lost, recovered, undecided, unresolved and unproven roots of two arms paired by declared root, over every rule but skip.

    A root is unresolved when a rule satisfied before is unknown after: the candidate may have lost it. It is unproven when the baseline
    could not decide a rule and the candidate neither follows it nor is decided either: a loss cannot be excluded. Neither can support a repair.
    """
    lost = recovered = undecided = unresolved = unproven = 0
    for root, outcomes in before.items():
        if root not in after:
            continue
        moves = [(outcome, after[root].get(key, 'unknown')) for key, outcome in outcomes.items() if key != skip]
        lost += ('satisfied', 'violated') in moves
        recovered += ('violated', 'satisfied') in moves
        undecided += any('unknown' in move for move in moves)
        unresolved += ('satisfied', 'unknown') in moves
        unproven += ('unknown', 'unknown') in moves or ('unknown', 'violated') in moves
    return dict(paired_roots=len(before.keys() & after.keys()), missing_in_candidate=len(before.keys() - after.keys()), lost=lost, recovered=recovered,
                undecided=undecided, unresolved=unresolved, unproven=unproven)


def _target_key(target, rows, session_list):
    for row in rows:
        if row['ref'] == target and row['rule']:
            return rules.key(row['rule'])
    for session in session_list:
        if target in (known := adherence.prompt_rules(session)):
            return known[target]
    raise ValueError('target is not a machine-checkable instruction of the baseline harness or of a baseline reproduction prompt')


def _interval(count, total):
    return [round(x, 4) for x in wilson(count, total)] if total else None


def judge(target, baseline, candidate, *, reproduction, preserved=None):
    """Screen one repair: harness texts before and after, normalized reproduction (baseline, candidate) sessions, optional preserved pair."""
    before, after = adherence.inventory(baseline)[0], adherence.inventory(candidate)[0]
    key = _target_key(target, before, reproduction[0])
    removed = {rules.key(row['rule']) for row in before if row['rule']} - {rules.key(row['rule']) for row in after if row['rule']}
    base, base_out = _roots(reproduction[0], before)
    cand, cand_out = _roots(reproduction[1], after)
    arms = dict(baseline=_arm(base, base_out, key), candidate=_arm(cand, cand_out, key))
    controls = _pairs(base, cand, skip=key)
    kept = None
    if preserved is not None:
        (kept_before, excluded), (kept_after, _) = _roots(preserved[0], before), _roots(preserved[1], after)
        kept = dict(status='supplied', unknown_lineage_excluded=excluded, **_pairs(kept_before, kept_after))
    # Unknown roots move each bound against the repair, so unknown never reads as a pass or a miss.
    b, c = arms['baseline'], arms['candidate']
    arms['baseline']['followed_interval95'] = _interval(b['followed'] + b['unknown'], b['roots'])
    arms['candidate']['followed_interval95'] = _interval(c['followed'], c['roots'])
    reasons, verdict = [], 'supported_by_recorded_pair'
    if removed:
        reasons.append('rule_removed')
    if controls['lost'] or kept and kept['lost']:
        reasons.append('regression')
    decided = lambda arm: arm['followed'] + arm['violated']
    # A control or preserved arm holds only when every baseline root is paired and no rule it could have lost is left unresolved or unproven.
    incomplete = lambda arm: arm['missing_in_candidate'] or not arm['paired_roots']
    not_proven = lambda arm: arm['unresolved'] or arm['unproven']
    if reasons:
        verdict = 'rejected'
    elif not decided(b):
        verdict, reasons = 'inconclusive', ['no_decided_baseline_roots']
    elif not b['violated']:
        verdict, reasons = 'recommendation_only', ['not_reproduced']
    elif not decided(c):
        verdict, reasons = 'inconclusive', ['no_decided_candidate_roots']
    elif kept is None:
        verdict, reasons = 'inconclusive', ['preserved_not_supplied']
    elif any(incomplete(arm) for arm in (controls, kept)):
        verdict, reasons = 'inconclusive', [name for name, arm in (('control_incomplete', controls), ('preserved_incomplete', kept)) if incomplete(arm)]
    elif any(not_proven(arm) for arm in (controls, kept)):
        verdict, reasons = 'inconclusive', [name for name, arm in (('control_undecided', controls), ('preserved_undecided', kept)) if not_proven(arm)]
    elif not arms['candidate']['followed_interval95'][0] > arms['baseline']['followed_interval95'][1]:
        verdict, reasons = 'inconclusive', ['interval_overlap']
    return dict(profile=VERDICT_PROFILE, authority='screen_only', causal_diagnosis=False, validated_improvement=False, change_authorized=False,
                promotion_authority=False, publication_allowed=False, redaction='digest_only', verdict=verdict, reasons=reasons,
                target=dict(instruction_ref=target), rules=dict(baseline=len({rules.key(row['rule']) for row in before if row['rule']}),
                candidate=len({rules.key(row['rule']) for row in after if row['rule']}), removed=len(removed)),
                reproduction=arms, controls=controls, preserved=kept or dict(status='not_supplied'), limitations=list(VERDICT_LIMITATIONS))


def _summary(result):
    if result['profile'] == PLAN_PROFILE:
        counts = result['counts']['by_repair_class']
        return '\n'.join(('ahl instruction-repair plan: findings=%d candidates=%d abstentions_not_diagnosed=%d' % (
            result['source']['findings'], len(result['candidates']), result['source']['abstentions']),
            '  repair classes: ' + (' '.join(f'{name}={count}' for name, count in counts.items()) or 'none'),
            '  recommendation only: no cause is claimed and no change is authorized'))
    arms = result['reproduction']
    return '\n'.join(('ahl instruction-repair judge: verdict=%s reasons=%s' % (result['verdict'], ','.join(result['reasons']) or 'none'),
                      '  target followed: baseline %d/%d roots, candidate %d/%d; preserved lost=%s recovered=%s' % (
                          arms['baseline']['followed'], arms['baseline']['roots'], arms['candidate']['followed'], arms['candidate']['roots'],
                          result['preserved'].get('lost', 'n/a'), result['preserved'].get('recovered', 'n/a')),
                      '  screen over recorded arms: not a validated improvement; the controlled experiment and application gates decide'))


def main(argv=None):
    parser = argparse.ArgumentParser(prog='ahl instruction-repair', description='Name the candidate mechanisms and repair classes behind ignored instructions, and screen one repair against recorded arms. '
        'Deterministic, offline and read-only toward its inputs; every output is a recommendation, never a cause, a validated improvement or a change.',
        epilog='Exit: 0 report printed (a rejected or inconclusive verdict is still a result); 2 invalid input. Output is private digest-only JSON; a short summary goes to stderr.')
    commands = parser.add_subparsers(dest='action', required=True, help='plan lists candidate repairs for a report; judge screens one repair against recorded arms')
    for name, text in (('plan', 'List candidate mechanisms and repair classes for the findings of an instruction-adherence report'),
                       ('judge', 'Screen one repair against recorded baseline and candidate sessions')):
        command = commands.add_parser(name, help=text, description=text)
        command.add_argument('--scope', choices=('project', 'global', 'project+global'), default='project', help='Layers whose rule files may be read (default: project)')
        command.add_argument('--output', type=Path, help='New private file for the JSON instead of stdout')
    plan, judge_command = commands.choices['plan'], commands.choices['judge']
    plan.add_argument('report', type=Path, help='Existing instruction-adherence JSON report')
    plan.add_argument('--rules', action='append', default=[], metavar='LAYER=FILE', help='The harness instruction file the report was made with; repeatable')
    judge_command.add_argument('--target', required=True, help='instruction_ref of the finding the repair addresses')
    judge_command.add_argument('--baseline-rules', action='append', default=[], metavar='LAYER=FILE', help='Harness instruction file before the repair; repeatable')
    judge_command.add_argument('--candidate-rules', action='append', default=[], metavar='LAYER=FILE', help='Harness instruction file after the repair; repeatable')
    judge_command.add_argument('--reproduction', nargs=2, type=Path, required=True, metavar=('BASELINE', 'CANDIDATE'), help='Sessions files of the reproduction task, one per arm')
    judge_command.add_argument('--preserved', nargs=2, type=Path, metavar=('BASELINE', 'CANDIDATE'), help='Sessions files of tasks that worked before, one per arm')
    args = parser.parse_args(argv)
    from ..private_journal import record
    read = lambda path: adherence.load_sessions(record(path, adherence.MAX_BYTES)[0])
    work = (lambda: diagnose(record(args.report, adherence.MAX_BYTES)[0], adherence.read_harness(args.rules, args.scope))) if args.action == 'plan' else (
        lambda: judge(args.target, adherence.read_harness(args.baseline_rules, args.scope), adherence.read_harness(args.candidate_rules, args.scope),
                      reproduction=tuple(map(read, args.reproduction)), preserved=tuple(map(read, args.preserved)) if args.preserved else None))
    return adherence.finish('ahl instruction-repair', work, _summary, args.output)
