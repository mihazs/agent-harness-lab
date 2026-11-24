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
    except (OSError, ValueError, RecursionError):
        return dict(profile=SECTION, status='unavailable', findings=[], project=None, publication_allowed=False)
    return dict(profile=SECTION, status=status, project=profile, findings=found[:MAX_FINDINGS], instruction_files=len(entries),
                instruction_files_unread=unread, findings_total=len(found), publication_allowed=False, authority='none',
                notice='Hypotheses that a harness line is wrong or missing; not a diagnosis and not evidence of performance.')
