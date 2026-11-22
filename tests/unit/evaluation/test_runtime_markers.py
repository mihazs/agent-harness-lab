"""Claude Code's empty write markers are removed from a trial workspace before grading; nothing else is."""
from agent_harness_lab.evaluation.trial_surfaces import strip_runtime_markers


def test_empty_write_markers_are_removed_and_real_content_is_kept(tmp_path):
    # Regression (fixed-in: 0.233.5): live, an empty .claude/.cc-writes left inside a migrations folder failed a grader that
    # requires every new file there to be a migration, in both arms.
    (tmp_path / 'migrations/.claude/.cc-writes').mkdir(parents=True)
    (tmp_path / '.claude/.cc-writes').mkdir(parents=True)
    (tmp_path / '.claude/settings.json').write_text('{}')
    (tmp_path / 'kept/.claude/.cc-writes').mkdir(parents=True)
    (tmp_path / 'kept/.claude/.cc-writes/note').write_text('x')
    (tmp_path / '.git/x/.claude/.cc-writes').mkdir(parents=True)
    removed = strip_runtime_markers(tmp_path)
    assert removed == ['migrations/.claude/.cc-writes', 'migrations/.claude', '.claude/.cc-writes']
    assert not (tmp_path / 'migrations/.claude').exists() and (tmp_path / '.claude/settings.json').exists()
    assert (tmp_path / 'kept/.claude/.cc-writes/note').exists() and (tmp_path / '.git/x/.claude/.cc-writes').exists()
