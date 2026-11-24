"""The private harness-profile section of a report: the project profile and the findings it supports."""
from agent_harness_lab.project_profile import detect, findings, harness_text, history, session_commands

SECTION = 'ahl-harness-profile-v1'
MAX_FINDINGS = 40


def build(project, sessions=None):
    """Profile one checkout and compare it with its instruction files; read-only, bounded, no model, never raises on content.

    `sessions` are the native sessions an analysis read (dicts of `session` and `turns`); when given, the commands they ran and
    the rules their user turns stated are compared with the harness too. `findings` is empty with a typed `status` when there is
    nothing to compare (no instruction file) or the walk failed, so a missing comparison is never read as a clean one, and the
    `history` block says `not_run` when no session ran a shell command."""
    try:
        repo = detect.Repo(project)
        profile = detect.detect(repo)
        entries = harness_text.files(repo)
        found = findings.compare(profile, repo, entries) if entries else []
        summary = session_commands.summarize(sessions, profile) if sessions else dict(
            profile=session_commands.PROFILE, status='not_run', reason='no_native_session_read', sessions_read=0, publication_allowed=False)
        if entries and sessions is not None:
            mentions = [row for entry in entries for row in harness_text.mentions(entry)]
            found = history.apply(found, profile, entries, mentions, summary, sessions)
        status = 'compared' if entries else 'no_instruction_file'
        unread = sum(item.startswith('unread:') for item in repo.unknown)
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError, NotImplementedError) as exc:
        # An advisory section never stops an analysis: whatever a file or a history made it trip on becomes a typed absence.
        return dict(profile=SECTION, status='unavailable', reason=type(exc).__name__, findings=[], project=None, history=None, publication_allowed=False)
    return dict(profile=SECTION, status=status, project=profile, history=summary, findings=found[:MAX_FINDINGS], instruction_files=len(entries),
                instruction_files_unread=unread, findings_total=len(found), publication_allowed=False, authority='none',
                notice='Hypotheses that a harness line is wrong or missing; not a diagnosis and not evidence of performance.')


BRIEF_PROFILE = 'ahl-harness-profile-brief-v1'
ARTIFACT = 'harness-profile.json'
MAX_BRIEF_FINDINGS = 12
MAX_TECHNOLOGIES = 24
TOP_KEYS = 10


def brief_section(result):
    """The bounded proposer-facing view of one report: ids, kinds, subjects, counts and proposed update actions.

    No instruction text and no history text is copied; a subject is a script or target name, a repository path or a tool
    name that was matched by a closed lexical shape. The full private report goes to `harness-profile.json`."""
    project = result.get('project') or {}
    counts = {}
    for row in project.get('commands', []):
        counts[row['category'] or 'other'] = counts.get(row['category'] or 'other', 0) + 1
    ranked = sorted(result['findings'], key=lambda row: (row['finding_class'] != 'contradiction', row['id']))
    listed = [dict(id=row['id'], kind=row['kind'], finding_class=row['finding_class'], subject=row['subject'][:120],
                   evidence=row['evidence'], where=row['where'], proposed_update=row['proposed_update'], source=row['source'])
              for row in ranked[:MAX_BRIEF_FINDINGS]]
    return dict(profile=BRIEF_PROFILE, authority='descriptive_only', causal_diagnosis=False, promotion_authority=False,
                status=result['status'], artifact=ARTIFACT, **({'reason': result['reason']} if 'reason' in result else {}),
                project=dict(technologies=[f"{row['kind']}:{row['name']}" for row in project.get('technologies', [])][:MAX_TECHNOLOGIES],
                             package_managers=[row['name'] for row in project.get('package_managers', [])],
                             declared_commands=dict(sorted(counts.items())), ci_commands=len(project.get('ci_commands', [])),
                             incomplete=project.get('incomplete', [])) if project else None,
                session_commands=_history_view(result.get('history')),
                instruction_files=result.get('instruction_files', 0), instruction_files_unread=result.get('instruction_files_unread', 0),
                findings=listed, findings_total=len(result['findings']), truncated=len(result['findings']) > len(listed))


def _history_view(summary):
    """Counts of what the read sessions ran, and the most used keys of the closed vocabulary; nothing else of the sessions."""
    if not summary: return None
    view = {name: summary[name] for name in ('status', 'reason', 'sessions_read', 'sessions_with_commands', 'lineage') if name in summary}
    if summary.get('status') == 'completed':
        view.update(categories=summary['categories'], top=[dict(key=row['key'], category=row['category'], sessions=row['sessions'])
                                                            for row in summary['commands'][:TOP_KEYS]])
    return view


def report_lines(section):
    """Markdown lines for report.md; empty when there is nothing to say."""
    if not section['findings']: return ''
    lines = ['\n## Harness profile findings\n', f"{section['findings_total']} hypothesis(es) from comparing the repository with the instruction files; "
             'none is a diagnosis. Details in `harness-profile.json`.\n']
    lines += [f"- `{row['id']}` {row['finding_class']}: {row['kind']} `{row['subject']}` -> {row['proposed_update']['action']}" for row in section['findings']]
    return '\n'.join(lines) + '\n'


CHECK = 'ahl-harness-profile-check-v1'


class _Overlay(detect.Repo):
    """The project, plus the files a candidate adds: a path the candidate itself creates is not a missing path."""

    def __init__(self, project, extra):
        super().__init__(project)
        self.extra = extra

    def kind(self, rel):
        return super().kind(rel) or detect.Repo(self.extra).kind(rel)


def _classes(profile, repo, folder):
    """{finding id: class} of the harness files in a folder, compared with the project's profile."""
    entries = harness_text.files(detect.Repo(folder))
    return {row['id']: row['finding_class'] for row in findings.compare(profile, repo, entries)} if entries else {}


def check_candidate(project, baseline, candidate, proposals=()):
    """Which profile findings the candidate harness resolves or introduces, over the project's own manifests; read-only and
    deterministic. It says the finding set changed, never that behavior improved: a resolved finding is not a success label and an
    introduced contradiction is a reason to read the change again, not a verdict. The ids of findings a proposal cited as evidence
    are split by whether the candidate resolved them."""
    try:
        repo = _Overlay(project, candidate)
        profile = detect.detect(repo)
        before, after = _classes(profile, repo, baseline), _classes(profile, repo, candidate)
    except (OSError, ValueError, RecursionError, NotImplementedError):
        return dict(profile=CHECK, status='unavailable', authority='none', publication_allowed=False)
    cited = sorted({ref for proposal in proposals if isinstance(proposal, dict) for item in proposal.get('mechanisms') or []
                    if isinstance(item, dict) for ref in item.get('evidence_refs') or [] if ref in before})
    return dict(profile=CHECK, status='compared', authority='none', publication_allowed=False,
                introduced_contradictions=sorted(name for name, kind in after.items() if name not in before and kind == 'contradiction'),
                resolved=sorted(name for name in before if name not in after), unchanged=len(before.keys() & after.keys()),
                cited_resolved=[name for name in cited if name not in after], cited_unresolved=[name for name in cited if name in after])
