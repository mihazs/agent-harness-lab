"""A Codex run output under /dev is refused before any session: its sandbox mounts a private /dev and hides the run's inputs."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_harness_lab import workflow


# fixed-in: a Codex proposer under /dev/shm saw an empty run folder and wrote no proposal, ending the run inconclusive
def test_a_codex_output_under_dev_is_refused_and_other_runtimes_or_places_are_not(tmp_path):
    shm = Path('/dev/shm')
    args = SimpleNamespace(project=tmp_path / 'project', output=shm / 'ahl-never-created', agent='codex')
    with pytest.raises(ValueError, match='under /dev'):
        workflow.output_dir(args)
    assert not args.output.exists()
    (tmp_path / 'project').mkdir()
    for agent, output in (('claude', tmp_path / 'claude-out'), ('codex', tmp_path / 'codex-out')):
        made = workflow.output_dir(SimpleNamespace(project=tmp_path / 'project', output=output, agent=agent))
        assert made == output.resolve() and made.is_dir()
