"""Each reviewed skill bundle carries its operation, its before/after identity and an explicit placement; synthetic only."""
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from agent_harness_lab.workflow import improve
from tests.integration.workflow.test_skill_bundle_lifecycle import ALPHA, ENTRY, Stage, project as project, write  # noqa: F401
from tests.integration.workflow.test_workflow import wf as wf  # noqa: F401

BARE = ENTRY.replace('Read [the guide](references/guide.md).', '')


def row(stage, bundle=ALPHA):
    return next(finding for finding in stage.review().findings if finding.bundle == bundle)


def membership(root, bundle):
    """Independent recomputation of the documented identity: sorted [relative path, content sha256, mode] rows."""
    rows = sorted([str(path.relative_to(root / bundle)), hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mode & 0o7777]
                  for path in (root / bundle).rglob('*') if path.is_file())
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


@pytest.mark.parametrize('edit, action, has_before, has_after', [
    pytest.param(lambda s: s.write('.claude/skills/beta/SKILL.md', BARE.format(name='beta')), 'create', False, True, id='create'),
    pytest.param(lambda s: s.write(f'{ALPHA}/references/guide.md', 'revised\n'), 'update', True, True, id='update'),
    pytest.param(lambda s: shutil.rmtree(s.candidate / ALPHA), 'remove', True, False, id='remove'),
])
def test_identity_exists_only_for_the_sides_that_hold_files(project, edit, action, has_before, has_after):
    stage = Stage(project)
    edit(stage)
    bundle = ALPHA if action != 'create' else '.claude/skills/beta'
    finding = row(stage, bundle)
    assert (finding.action, finding.before_sha256 is not None, finding.after_sha256 is not None) == (action, has_before, has_after)


def test_identity_matches_an_independent_recomputation_of_the_files(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    finding = row(stage)
    assert (finding.before_sha256, finding.after_sha256) == (membership(stage.root / 'baseline', ALPHA), membership(stage.candidate, ALPHA))


@pytest.mark.parametrize('edit', [
    pytest.param(lambda s: s.write(f'{ALPHA}/references/guide.md', 'guidf\n'), id='one-byte-of-content'),
    pytest.param(lambda s: os.chmod(s.candidate / ALPHA / 'references/guide.md', 0o600), id='file-mode-only'),
    pytest.param(lambda s: (s.write(f'{ALPHA}/references/more.md', 'more\n'),
                            s.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '[m](references/more.md)\n')), id='added-asset'),
])
def test_any_change_inside_the_bundle_changes_the_after_identity(project, edit):
    stage = Stage(project)
    edit(stage)
    finding = row(stage)
    assert finding.after_sha256 != finding.before_sha256


def test_identity_depends_on_the_resulting_files_not_on_the_edit_history(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    first = row(stage).after_sha256
    stage.write(f'{ALPHA}/references/guide.md', 'detour\n')
    stage.write(f'{ALPHA}/references/guide.md', 'revised\n')
    assert row(stage).after_sha256 == first != row(stage).before_sha256


@pytest.mark.parametrize('bundle, placement', [
    pytest.param('.claude/skills/new', 'claude-code', id='claude-root'),
    pytest.param('pkg/.claude/skills/new', 'claude-code', id='claude-nested'),
    pytest.param('.agents/skills/new', 'codex', id='codex-root'),
    pytest.param('pkg/.agents/skills/new', 'codex', id='codex-nested'),
    pytest.param('skills/new', 'unverified_location', id='bare-root-skills-folder'),
])
def test_placement_names_the_runtime_whose_documented_root_holds_the_bundle(project, bundle, placement):
    write(project, 'skills/seed/SKILL.md', BARE.format(name='seed'))  # the project owns a bare skills/ tree
    stage = Stage(project)
    stage.write(f'{bundle}/SKILL.md', BARE.format(name='new'))
    assert row(stage, bundle).placement == placement


def test_scoped_review_leaves_the_identity_unknown_rather_than_empty(project):
    from tests.integration.workflow.test_skill_bundle_lifecycle import scoped_review, scoped_stage
    stage = scoped_stage(project, [f'{ALPHA}/SKILL.md'])
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha').replace('Summarize', 'Digest'))
    finding = scoped_review(stage).findings[0]
    assert (finding.action, finding.before_sha256, finding.after_sha256) == ('scoped', None, None)


def test_blocked_outcome_carries_placement_and_identity_without_an_activation_claim(project):
    stage = Stage(project)
    stage.write(f'{ALPHA}/SKILL.md', ENTRY.format(name='alpha') + '[x](references/gone.md)\n')
    review = stage.review().outcome()['skill_review']
    assert review['findings'][0]['placement'] == 'claude-code' and len(review['findings'][0]['after_sha256']) == 64
    assert review['loading_evidence'] is False and 'activation' not in json.dumps(review)


def test_inconclusive_behavioral_plan_lists_the_staged_skill_bundles(wf):
    for name in ('alpha', 'beta'):
        write(wf.project, f'.agents/skills/{name}/SKILL.md', f'---\nname: {name}\ndescription: Extract PDF documents\n---\n\nInstructions.\n')
    (wf.project / '.git').mkdir()
    (wf.project / 'mode').write_text('skill_route')
    result = improve(wf.args())
    plan = json.loads((Path(result['output']) / 'change-plan.json').read_text())
    assert (result['status'], result['changed_files']) == ('inconclusive', [])
    (bundle,) = plan['skill_bundles']
    assert (bundle['bundle'], bundle['action'], bundle['placement']) == ('.agents/skills/alpha', 'update', 'codex')
    assert bundle['before_sha256'] != bundle['after_sha256']


def test_plan_of_a_run_without_skill_changes_has_no_skill_bundle_entry(wf):
    result = improve(wf.args())
    assert 'skill_bundles' not in json.loads((Path(result['output']) / 'change-plan.json').read_text())
