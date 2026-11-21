"""A proposer cannot know the pinned runtime version, so the controller completes a partial instruction profile."""
from agent_harness_lab.sources import instruction_validation as validation, instructions


def test_a_profile_naming_only_the_task_directory_is_completed_from_the_pinned_runtime():
    # Regression (fixed-in: 0.232.3): a live proposer wrote profile={"task_cwd": "."} and the run ended blocked as
    # instruction_plan_invalid although the change was a plain root instruction edit.
    plan = dict(schema_version=1, profile=dict(task_cwd='.'), task_paths=['src'], required=[dict(id='rule', text='one line')])
    claude = validation._completed_profile(plan, 'claude-code')['profile']
    assert (claude['runtime'], claude['version'], claude['schema_version'], claude['task_cwd']) == (*instructions.CLAUDE, 1, '.')
    instructions.validate(claude)
    codex = validation._completed_profile(plan, 'codex')['profile']
    assert (codex['runtime'], codex['version'], codex['fallback_filenames']) == (*instructions.CODEX, [])
    instructions.validate(codex)
    validation.parse_plan(validation._completed_profile(plan, 'claude-code'))


def test_a_profile_for_another_runtime_or_a_malformed_plan_is_left_for_validation_to_refuse():
    other = dict(schema_version=1, profile=dict(task_cwd='.', runtime='codex'))
    assert validation._completed_profile(other, 'claude-code') is other
    for plan in (None, 'text', dict(schema_version=1, profile='x')):
        assert validation._completed_profile(plan, 'claude-code') is plan


def test_an_unusable_plan_beside_a_root_only_change_is_ignored(tmp_path):
    # Regression (fixed-in: 0.232.3): the live plan also declared task_paths=["."], which the parser refuses.
    from types import SimpleNamespace
    args = SimpleNamespace(project=tmp_path)
    plan = dict(schema_version=1, profile=dict(task_cwd='.'), task_paths=['.'], required=[dict(id='rule', text='one line')])
    original, proposed = {'AGENTS.md': ('a', 0o644)}, {'AGENTS.md': ('b', 0o644)}
    assert validation.hook(args, tmp_path, tmp_path, original, proposed, dict(instruction_plan=plan), 'claude', lenient=True) is None
    assert validation.hook(args, tmp_path, tmp_path, original, proposed, dict(instruction_plan=plan), 'claude')['status'] == 'blocked'  # a recommending run keeps the block
