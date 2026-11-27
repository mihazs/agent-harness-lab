"""Old run folders in AHL's state are deleted automatically; recent, pinned and protected ones stay."""
import os

from agent_harness_lab import run_retention


def make(store, name, age_days, now):
    folder = store / name; folder.mkdir(parents=True); (folder / 'workflow.json').write_text('{}')
    os.utime(folder, (now - age_days * 86400,) * 2)
    return folder


# fixed-in: run outputs accumulated without bound in the state directory (about 180 MB each)
def test_only_old_unprotected_runs_beyond_the_newest_are_deleted(tmp_path, monkeypatch):
    now = 1_000_000_000.0
    monkeypatch.setenv('AHL_RUN_KEEP', '2'); monkeypatch.delenv('AHL_RUN_RETENTION_DAYS', raising=False)
    store = tmp_path / 'runs'
    for i in range(6): make(store, f'run-old{i}', 40 + i, now)
    make(store, 'run-new', 1, now); make(store, 'run-recent', 5, now)
    (store / 'latest-run.json').write_text('{"run": "run-old5"}')
    (store / 'harness-states.json').write_text('{"regressions": [{"run": "run-old4"}]}')
    (store / 'not-a-run').mkdir(); os.utime(store / 'not-a-run', (now - 400 * 86400,) * 2)
    current = make(store, 'run-current', 90, now)
    deleted = run_retention.prune(store, current=current, now=now)
    assert sorted(deleted) == ['run-old0', 'run-old1', 'run-old2', 'run-old3']
    assert sorted(p.name for p in store.iterdir()) == ['harness-states.json', 'latest-run.json', 'not-a-run', 'run-current',
                                                       'run-new', 'run-old4', 'run-old5', 'run-recent']


def test_retention_can_be_switched_off_and_never_raises(tmp_path, monkeypatch):
    now = 1_000_000_000.0
    make(tmp_path, 'run-a', 400, now)
    monkeypatch.setenv('AHL_RUN_KEEP', '0'); monkeypatch.setenv('AHL_RUN_RETENTION_DAYS', 'off')
    assert run_retention.prune(tmp_path, now=now) == [] and (tmp_path / 'run-a').is_dir()
    monkeypatch.setenv('AHL_RUN_RETENTION_DAYS', '30')
    assert run_retention.prune(tmp_path / 'missing', now=now) == []
    assert run_retention.prune(tmp_path, now=now) == ['run-a']
