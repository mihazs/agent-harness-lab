"""The private harness-profile section of a report: the project profile and the findings it supports."""
from agent_harness_lab.project_profile import detect, findings, harness_text

SECTION = 'ahl-harness-profile-v1'
MAX_FINDINGS = 40


def build(project):
    """Profile one checkout and compare it with its instruction files; read-only, bounded, no model, never raises on content.

    `findings` is empty with a typed `status` when there is nothing to compare (no instruction file) or the walk failed, so a
    missing comparison is never read as a clean one."""
    try:
        repo = detect.Repo(project)
        profile = detect.detect(repo)
        entries = harness_text.files(repo)
        found = findings.compare(profile, repo, entries) if entries else []
        status = 'compared' if entries else 'no_instruction_file'
        unread = sum(item.startswith('unread:') for item in repo.unknown)
    except (OSError, ValueError, RecursionError, NotImplementedError):
        return dict(profile=SECTION, status='unavailable', findings=[], project=None, publication_allowed=False)
    return dict(profile=SECTION, status=status, project=profile, findings=found[:MAX_FINDINGS], instruction_files=len(entries),
                instruction_files_unread=unread, findings_total=len(found), publication_allowed=False, authority='none',
                notice='Hypotheses that a harness line is wrong or missing; not a diagnosis and not evidence of performance.')


BRIEF_PROFILE = 'ahl-harness-profile-brief-v1'
ARTIFACT = 'harness-profile.json'
MAX_BRIEF_FINDINGS = 12
MAX_TECHNOLOGIES = 24


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
                status=result['status'], artifact=ARTIFACT,
                project=dict(technologies=[f"{row['kind']}:{row['name']}" for row in project.get('technologies', [])][:MAX_TECHNOLOGIES],
                             package_managers=[row['name'] for row in project.get('package_managers', [])],
                             declared_commands=dict(sorted(counts.items())), ci_commands=len(project.get('ci_commands', [])),
                             incomplete=project.get('incomplete', [])) if project else None,
                instruction_files=result.get('instruction_files', 0), instruction_files_unread=result.get('instruction_files_unread', 0),
                findings=listed, findings_total=len(result['findings']), truncated=len(result['findings']) > len(listed))


def report_lines(section):
    """Markdown lines for report.md; empty when there is nothing to say."""
    if not section['findings']: return ''
    lines = ['\n## Harness profile findings\n', f"{section['findings_total']} hypothesis(es) from comparing the repository with the instruction files; "
             'none is a diagnosis. Details in `harness-profile.json`.\n']
    lines += [f"- `{row['id']}` {row['finding_class']}: {row['kind']} `{row['subject']}` -> {row['proposed_update']['action']}" for row in section['findings']]
    return '\n'.join(lines) + '\n'
