"""Which same-name skill a runtime documents as the winner across the user and project layers. Synthetic folders only."""
import json

import pytest

from agent_harness_lab import scope

CLAUDE_SKILLS = 'https://code.claude.com/docs/en/skills'
CODEX_SKILLS = 'https://developers.openai.com/codex/skills'


def skill(root, location, name):
    folder = root / location / name
    folder.mkdir(parents=True)
    (folder / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Use for synthetic checks.\n---\n')


def layers(tmp_path, user=(), project=()):
    """Layers from (location, name) pairs placed under a synthetic user root and project root."""
    selection = []
    for label, entries in (('global', user), ('project', project)):
        root = tmp_path / label
        root.mkdir()
        for location, name in entries:
            skill(root, location, name)
        selection.append(scope.Layer(label, root))
    return selection


def row(result, name='shared'):
    return next(item for item in result['shared_skills'] if item['name'] == name)


def test_claude_documents_the_user_skill_over_a_project_skill_of_the_same_name(tmp_path):
    result = scope.precedence('claude', layers(tmp_path, user=[('skills', 'shared')], project=[('.claude/skills', 'shared')]))
    assert row(result) == dict(name='shared', resolution='user_over_project', effective_layer='global', shadowed_layer='project',
                               project_locations=['.claude/skills'], source=CLAUDE_SKILLS)
    assert (result['same_name_skills'], result['shared_skill_names']) == ('documented_rule', ['shared'])


def test_codex_documents_that_both_same_name_skills_stay_available(tmp_path):
    result = scope.precedence('codex', layers(tmp_path, user=[('skills', 'shared')], project=[('.agents/skills', 'shared')]))
    assert row(result) == dict(name='shared', resolution='both_kept', effective_layer='both', shadowed_layer=None,
                               project_locations=['.agents/skills'], source=CODEX_SKILLS)
    assert result['same_name_skills'] == 'documented_rule'


@pytest.mark.parametrize('runtime,location', [
    pytest.param('claude', '.agents/skills', id='claude-does-not-document-the-agents-folder'),
    pytest.param('claude', 'skills', id='claude-does-not-load-a-plain-skills-folder-as-a-project-skill'),
    pytest.param('codex', '.claude/skills', id='codex-does-not-document-the-claude-folder'),
    pytest.param('codex', 'skills', id='codex-does-not-load-a-plain-skills-folder-as-a-project-skill'),
])
def test_a_project_copy_outside_the_runtimes_documented_folder_keeps_the_winner_unknown(tmp_path, runtime, location):
    result = scope.precedence(runtime, layers(tmp_path, user=[('skills', 'shared')], project=[(location, 'shared')]))
    assert row(result) == dict(name='shared', resolution='unknown', effective_layer='unknown', shadowed_layer=None,
                               project_locations=[location], source=None)
    assert (result['same_name_skills'], result['shared_skill_names']) == ('effective_layer_unknown', ['shared'])


@pytest.mark.parametrize('runtime,location', [('claude', '.claude/skills'), ('codex', '.agents/skills')])
def test_a_name_held_by_one_layer_only_is_not_a_conflict(tmp_path, runtime, location):
    result = scope.precedence(runtime, layers(tmp_path, user=[('skills', 'user-only')], project=[(location, 'project-only')]))
    assert (result['same_name_skills'], result['shared_skill_names'], result['shared_skills']) == ('none_shared', [], [])


def test_layers_without_any_skill_report_no_shared_names(tmp_path):
    result = scope.precedence('claude', layers(tmp_path))
    assert (result['same_name_skills'], result['shared_skill_names'], result['shared_skills']) == ('none_shared', [], [])


def test_one_unresolved_name_keeps_the_whole_summary_unknown_and_each_row_honest(tmp_path):
    selection = layers(tmp_path, user=[('skills', 'documented'), ('skills', 'undocumented')],
                       project=[('.claude/skills', 'documented'), ('skills', 'undocumented')])
    result = scope.precedence('claude', selection)
    assert [(item['name'], item['resolution']) for item in result['shared_skills']] == [('documented', 'user_over_project'), ('undocumented', 'unknown')]
    assert result['same_name_skills'] == 'effective_layer_unknown'


def test_a_user_layer_copy_outside_the_runtimes_user_skill_directory_is_not_counted(tmp_path):
    result = scope.precedence('claude', layers(tmp_path, user=[('.claude/skills', 'shared')], project=[('.claude/skills', 'shared')]))
    assert (result['same_name_skills'], result['shared_skill_names']) == ('none_shared', [])


def test_the_same_name_in_two_project_folders_lists_both_and_resolves_only_by_the_documented_one(tmp_path):
    selection = layers(tmp_path, user=[('skills', 'shared')], project=[('.agents/skills', 'shared'), ('.claude/skills', 'shared')])
    result = scope.precedence('claude', selection)
    assert (row(result)['resolution'], row(result)['project_locations']) == ('user_over_project', ['.agents/skills', '.claude/skills'])


def test_the_receipt_part_is_plain_json(tmp_path):
    result = scope.precedence('codex', layers(tmp_path, user=[('skills', 'shared')], project=[('.agents/skills', 'shared')]))
    assert json.loads(json.dumps(result)) == result
