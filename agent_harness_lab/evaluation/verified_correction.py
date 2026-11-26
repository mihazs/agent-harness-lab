"""The verified-correction lane: a candidate that only corrects the harness lines a static contradiction cites.

Agents often behave the same with and without a dead reference, so a paired evaluation has no gap to measure, yet the harness
keeps stating something the project contradicts. A candidate is installed on that basis only when every gate holds: the target
is a freshly derived profile finding of class `contradiction`, the candidate replaces or removes exactly the cited lines and
nothing else, the profile check shows the finding resolved and nothing introduced, the paired evaluation lost nothing, and the
install review is consistent. This is never a measured improvement: the behavioural result stays as it was. Standard library only.
"""
import difflib
from pathlib import Path

BASIS = 'verified_correction'
MEASURED = 'measured_improvement'
NOT_WRITABLE = 'target_not_writable'


def target(args):
    """(finding, refusal): the fresh profile finding this run targets when it is a contradiction. (None, None) when the run
    targets no profile finding at all, so the lane does not apply and says nothing."""
    hypothesis = getattr(args, '_audit_hypothesis', None) or {}
    reason = str(hypothesis.get('reason', ''))
    from agent_harness_lab.project_profile import findings, report
    if not reason.startswith('harness_profile_'): return None, None
    if findings.KINDS.get(reason.removeprefix('harness_profile_'), ('',))[0] != 'contradiction': return None, 'target_not_a_contradiction'
    row = next((row for row in report.build(args.project)['findings'] if row['id'] == hypothesis.get('finding_id')), None)
    return (row, None) if row is not None else (None, 'target_finding_not_current')


def _lines(folder, name):
    try: return (Path(folder) / name).read_text().splitlines(keepends=True)
    except (OSError, UnicodeError): return None


def only_cited_lines(baseline, candidate, original, proposed, where):
    """True when the candidate differs from the baseline only by replacing or removing lines `where` cites, in the files it
    cites: no other file, no added line, no mode change."""
    cited = {}
    for row in where: cited.setdefault(row['file'], set()).add(row['line'])
    changed = [name for name in original.keys() | proposed.keys() if original.get(name) != proposed.get(name)]
    if not changed: return False
    for name in changed:
        before, after = _lines(baseline, name), _lines(candidate, name)
        if name not in cited or before is None or after is None or not (original.get(name) and proposed.get(name)) or original[name][1] != proposed[name][1]:
            return False
        for tag, start, end, *_ in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag == 'insert' or tag in ('replace', 'delete') and not set(range(start + 1, end + 1)) <= cited[name]: return False
    return True


def resolves(check, finding_id):
    """True when the profile check compared the harness, resolved the target and introduced no finding of any class."""
    return check.get('status') == 'compared' and finding_id in check['resolved'] and not check['introduced']


def lost_nothing(trials, inputs_unchanged):
    """True when no task the baseline passed was not passed by the candidate and every regression task passed for it."""
    status = {(t['task'], t['version']): t['status'] for t in trials}
    return bool(inputs_unchanged and trials and not any(
        status.get((t['task'], 'baseline')) == 'passed' and status.get((t['task'], 'candidate')) != 'passed' for t in trials)
        and all(t['status'] == 'passed' for t in trials if t['version'] == 'candidate' and t['split'] == 'regression'))


GATES = ('edits_beyond_cited_lines', 'finding_not_resolved_or_introduced', 'evaluation_regressed')


def choose(result, args, baseline, original, attempts):
    """The first evaluated attempt (a later one may have drifted to another weakness) that passes gates 1 to 4, or None.
    Records `verified_correction` on the result: the refusal that got furthest, or the chosen attempt."""
    from agent_harness_lab.project_profile import report
    finding, refusal = target(args)
    if finding is None:
        if refusal: result['verified_correction'] = dict(status='refused', reason=refusal)
        return None
    refused = []
    for item in attempts:
        failed = ('edits_beyond_cited_lines' if not only_cited_lines(baseline, item['frozen'], original, item['proposed'], finding['where'])
                  else 'finding_not_resolved_or_introduced' if not resolves(report.check_candidate(args.project, baseline, item['frozen']), finding['id'])
                  else 'evaluation_regressed' if not lost_nothing(item['trials'], item['inputs_unchanged']) else None)
        if failed is None:
            result['verified_correction'] = dict(status='candidate', finding=finding['id'], attempt=item['attempt'])
            return item
        refused.append(GATES.index(failed))
    result['verified_correction'] = dict(status='refused', finding=finding['id'], reason=GATES[max(refused)] if refused else 'no_evaluated_candidate')
    return None
