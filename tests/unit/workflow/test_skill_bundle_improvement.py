"""The improve loop stops a blocked skill bundle before evaluation; synthetic files only."""
from pathlib import Path

from tests.unit.workflow.test_agents_improvement import workspace  # noqa: F401 (fixture)

ENTRY = '---\nname: alpha\ndescription: Summarize release notes\n---\n{}\n'


def test_dangling_skill_reference_stops_improve_before_evaluation(workspace):  # noqa: F811
    result = workspace.improve({'.claude/skills/alpha/SKILL.md': ENTRY.format('[x](references/gone.md)')})
    assert (result['status'], result['reason']) == ('blocked', 'skill_bundle_validation_failed')
    assert result['skill_review']['findings'][0]['reasons'] == ('dangling_reference',)
    assert not (Path(result['output']) / 'preparation/evaluation-0').exists()
    assert not (workspace.project / '.claude').exists()
