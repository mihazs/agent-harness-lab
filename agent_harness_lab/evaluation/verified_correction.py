"""The verified-correction lane: a candidate that only corrects the harness lines a static contradiction cites.

Agents often behave the same with and without a dead reference, so a paired evaluation has no gap to measure, yet the harness
keeps stating something the project contradicts. A candidate is installed on that basis only when every gate holds: the target
is a freshly derived profile finding of class `contradiction`, the candidate replaces or removes exactly the cited lines and
nothing else, the profile check shows the finding resolved and nothing introduced, the paired evaluation lost nothing, and the
install review is consistent. This is never a measured improvement: the behavioural result stays as it was. Standard library only.
"""
import difflib
from pathlib import Path
import re

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


_REFERENCE = re.compile(r'`([^`\n]+)`|\]\(([^)\s]+)\)')


def adds_no_reference(baseline, candidate, original, proposed):
    """True when every replaced line names only commands, paths and links its original line already named. A correction removes
    or rewords a dead reference: a new one (an existing command that does not do what the line now says) is a fresh claim no
    gate can verify."""
    def named(lines): return {a or b for line in lines for a, b in _REFERENCE.findall(line)}
    for name in (n for n in original.keys() | proposed.keys() if original.get(n) != proposed.get(n)):
        before, after = _lines(baseline, name) or [], _lines(candidate, name) or []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes():
            if tag == 'replace' and not named(after[j1:j2]) <= named(before[i1:i2]): return False
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


GATES = ('edits_beyond_cited_lines', 'correction_adds_a_reference', 'finding_not_resolved_or_introduced', 'evaluation_regressed')


def choose(result, args, baseline, original, attempts):
    """The first evaluated attempt (a later one may have drifted to another weakness) that passes gates 1 to 4, or None.
    Records `verified_correction` on the result: the refusal that got furthest, or the chosen attempt.

    A run aimed at a profile finding (--from-audit) is judged on that finding. Any other installing run is judged on the current
    contradictions each candidate resolves: a plain `ahl improve` whose proposer corrected a dead reference measures no gap
    either, and must not drop a correction the same gates prove. A run whose candidates resolve none says nothing."""
    from agent_harness_lab.project_profile import report
    finding, refusal = target(args)
    if finding is None and refusal:
        result['verified_correction'] = dict(status='refused', reason=refusal)
        return None
    current = {row['id']: row for row in report.build(args.project)['findings']}
    contradictions = {name for name, row in current.items() if row['finding_class'] == 'contradiction'}
    if finding is None and not contradictions: return None
    refused, named = [], finding['id'] if finding else None
    # A candidate that lost a task in any evaluation is never a correction, even when a later evaluation of the same files was clean.
    lossy = [item['proposed'] for item in attempts if not lost_nothing(item['trials'], item['inputs_unchanged'])]
    for item in attempts:
        check = report.check_candidate(args.project, baseline, item['frozen'])
        resolved = [name for name in check.get('resolved', ()) if name in contradictions]
        primary = finding['id'] if finding else (resolved[0] if resolved else None)
        if primary is None: continue  # an untargeted candidate that corrects nothing is not a correction at all
        named = named or primary
        # Lines of every contradiction the same candidate resolves may change: correcting two dead references at once is still
        # only a correction, and the profile check below proves nothing new was introduced.
        where = (finding['where'] if finding else []) + [row for name in resolved for row in current[name]['where']]
        failed = ('edits_beyond_cited_lines' if not only_cited_lines(baseline, item['frozen'], original, item['proposed'], where)
                  else 'correction_adds_a_reference' if not adds_no_reference(baseline, item['frozen'], original, item['proposed'])
                  else 'finding_not_resolved_or_introduced' if not resolves(check, primary)
                  else 'evaluation_regressed' if item['proposed'] in lossy else None)
        if failed is None:
            result['verified_correction'] = dict(status='candidate', finding=primary, attempt=item['attempt'])
            return item
        refused.append(GATES.index(failed))
    if named is None: return None
    result['verified_correction'] = dict(status='refused', finding=named, reason=GATES[max(refused)] if refused else 'no_evaluated_candidate')
    return None
