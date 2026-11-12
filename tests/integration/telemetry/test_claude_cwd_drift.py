"""Synthetic Claude stores: a session launched in the project that later records other working directories stays attributed."""
from argparse import Namespace
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.diagnostics import brief
from agent_harness_lab.evidence import collect
from agent_harness_lab.history import summary
from agent_harness_lab.telemetry.claude_cwd import CLASSES, Places, registered_worktrees
from agent_harness_lab.telemetry.claude_discovery import directory_names
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests, VERSION

SESSION = '00000000-0000-4000-8000-0000000000c1'
OTHER = '00000000-0000-4000-8000-0000000000c2'
# The nine native rows (u1 ... answer) get these directories in order; class counts follow from the layout below.
LAYOUT = ('root', 'root', 'subdirectory', 'worktree', 'elsewhere', 'subdirectory', 'worktree', 'root')
EXPECTED = dict(project_root=3, project_subdirectory=2, registered_worktree_of_project=2, other_location=1)
ENVIRONMENT = {'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_TERMINAL_PROMPT': '0'}


def git(cwd, *arguments):
    subprocess.run(['git', '-c', 'user.name=Synthetic', '-c', 'user.email=synthetic@example.invalid', '-c', 'commit.gpgsign=false', *arguments],
        cwd=cwd, check=True, capture_output=True, env={**os.environ, **ENVIRONMENT})


class Store(ClaudeNativeTests):
    """A real temporary repository with a registered worktree, plus a project-directory session store."""
    def build(self, tmp_path):
        self.setUp(tmp_path)
        self.project = self.project.resolve()
        git(self.project, 'init', '-q'); git(self.project, 'commit', '-q', '--allow-empty', '-m', 'synthetic')
        self.worktree = (tmp_path/'wt-synthetic-private').resolve()
        git(self.project, 'worktree', 'add', '-q', str(self.worktree), '-b', 'synthetic-branch')
        (self.project/'sub-private-dir').mkdir(); self.elsewhere = (tmp_path/'elsewhere-private-dir'); self.elsewhere.mkdir()
        self.places = dict(root=self.project, subdirectory=self.project/'sub-private-dir', worktree=self.worktree/'nested-private-dir', elsewhere=self.elsewhere)
        self.projects = tmp_path/'claude'/'projects'
        self.directory = self.projects/directory_names(self.project)[0]
        self.directory.mkdir(parents=True)
        return self

    def session(self, layout=LAYOUT, session=SESSION, first=None):
        rows, places = deepcopy(self.rows), iter(layout)
        for row in rows:
            if 'sessionId' not in row: continue
            row.update(sessionId=session, cwd=str(self.places[next(places)]))
        if first is not None: next(row for row in rows if 'cwd' in row)['cwd'] = str(first)
        (self.directory/(session+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def explicit(self, **options):
        return collect(self.project, source_root=self.projects, source_format='claude', source_version=VERSION, session=SESSION, **options)

    def default(self, **options):
        env = {'CLAUDE_CONFIG_DIR': str(self.projects.parent), 'CODEX_HOME': str(self.projects.parent/'no-codex')}
        with patch.dict(os.environ, env): return collect(self.project, source_format='claude', **options)


@pytest.fixture
def store(tmp_path):
    return Store().build(tmp_path)


def classes(events): return dict(Counter(event['cwd_class'] for event in events if 'cwd_class' in event))


def test_explicit_and_default_discovery_read_a_drifting_session_with_the_verified_adapter(store):
    store.session()
    explicit_events, default_events = [], []
    explicit, default = store.explicit(event_sink=explicit_events.append), store.default(event_sink=default_events.append)
    assert explicit['analysis']['events'] == default['analysis']['events'] == len(explicit_events) > 0
    assert default['sources'][0]['adapter'] == 'verified_native' and default['sources'][0]['pin_compatibility'] == 'declared_range'
    assert explicit['sources'][0]['adapter'] == 'verified_native'
    counts = [source['native_coverage']['cwd_drift'] for source in (explicit['sources'][0], default['sources'][0])]
    assert counts[0] == counts[1] == dict(captures=1, rows=EXPECTED, worktrees='listed')
    assert classes(explicit_events) == classes(default_events)
    assert set(classes(explicit_events)) == set(CLASSES) and set(CLASSES) == set(EXPECTED)
    # No silent narrowing: every entry row of the capture is an event source, and the unrelated-directory rows are among them.
    assert sum(counts[0]['rows'].values()) == 8
    assert not any('cwd' in c or 'project' in c for r in (explicit, default) for c in r['caveats'])
    assert explicit['capture_reasons'] == [] and default['capture_reasons'] == []
    assert sum(1 for e in explicit_events if e.get('cwd_class') == 'other_location' and e['activity'] == 'tool_result' and e['execution_status'] == 'error') == 1


def test_a_registered_worktree_is_listed_from_the_repository_without_running_git(store):
    store.session()
    with patch('subprocess.Popen', side_effect=AssertionError('no repository code')), patch('subprocess.run', side_effect=AssertionError('no repository code')):
        listing, state = registered_worktrees(store.project)
        assert state == 'listed' and store.worktree in listing
        assert store.explicit()['sources'][0]['native_coverage']['cwd_drift']['rows'] == EXPECTED


def test_a_worktree_can_be_the_project_and_relative_gitdir_paths_resolve(store):
    linked, state = registered_worktrees(store.worktree)
    assert state == 'listed' and store.project in linked
    # Newer git can record worktree locations relative to the administrative directory.
    admin = next((store.project/'.git'/'worktrees').iterdir())
    (admin/'gitdir').write_text(os.path.relpath(store.worktree/'.git', admin)+'\n')
    assert store.worktree in registered_worktrees(store.project)[0]


def test_repository_that_cannot_list_worktrees_yields_other_location_with_a_caveat(store):
    store.session()
    shutil.rmtree(store.project/'.git'); (store.project/'.git').write_text('not a git pointer\n')
    for result in (store.explicit(), store.default()):
        drift = result['sources'][0]['native_coverage']['cwd_drift']
        assert drift['rows']['registered_worktree_of_project'] == 0 and drift['rows']['other_location'] == 3 and drift['worktrees'] == 'unavailable'
        assert any(c.startswith('cwd_worktrees_unavailable') for c in result['caveats'])
        assert str(store.worktree) not in json.dumps(result)


def test_listing_is_bounded_and_unreadable_pieces_are_unavailable_not_guessed(store, tmp_path):
    with patch('agent_harness_lab.telemetry.claude_cwd.MAX_WORKTREES', 0):
        assert registered_worktrees(store.project) == (frozenset(), 'unavailable')
    assert registered_worktrees(tmp_path/'missing') == (frozenset(), 'not_a_repository')
    admin = next((store.project/'.git'/'worktrees').iterdir())
    (admin/'gitdir').write_bytes(b'x'*10000)
    assert store.worktree not in registered_worktrees(store.project)[0]
    place = Places(store.project)
    assert place.classify(str(store.project/'..'/'elsewhere')) == 'other_location' and place.classify('relative/path') == 'other_location'
    assert place.classify(str(store.project)+'-sibling') == 'other_location'


def test_a_capture_whose_first_attributable_row_names_another_project_stays_foreign(store, tmp_path):
    sibling = tmp_path/'project-sibling'; sibling.mkdir()
    # Swapping the last separator for '-' sanitizes to the same directory name as the project's own.
    text = str(store.project); cut = text.rindex('/')
    for name, first in (('sibling', sibling), ('collision', Path(text[:cut]+'-'+text[cut+1:]))):
        store.session(first=first)
        explicit = store.explicit()
        discovery = explicit['sources'][0]['native_coverage']['discovery']
        assert discovery['foreign_files'] == 1 and explicit['analysis']['events'] == 0, name
        assert store.default()['sources'] == [], name


def test_a_capture_that_begins_elsewhere_and_later_enters_the_project_is_not_attributed(store):
    store.session(layout=('elsewhere', 'root', 'root', 'root', 'root', 'root', 'root', 'root'))
    explicit = store.explicit()
    assert explicit['sources'][0]['native_coverage']['discovery']['foreign_files'] == 1 and explicit['analysis']['events'] == 0
    assert store.default()['sources'] == []
    with pytest.raises(ValueError, match='project mismatch'):
        from agent_harness_lab.telemetry.claude_native import read
        read(store.directory/(SESSION+'.jsonl'), store.project)


def test_subagent_transcripts_with_drift_are_read_by_the_same_rules(store):
    store.session()
    nested = store.directory/SESSION/'subagents'; nested.mkdir(parents=True)
    rows = []
    for row in deepcopy(store.rows):
        if 'sessionId' in row: row.update(sessionId=SESSION, isSidechain=True, agentId='synthetic-agent', cwd=str(store.places['worktree']))
        rows.append(row)
    rows[1]['cwd'] = str(store.project)
    (nested/'agent-synthetic-agent.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    drift = store.explicit()['sources'][0]['native_coverage']['cwd_drift']
    assert drift['captures'] == 2 and sum(drift['rows'].values()) == 16 and drift['rows']['registered_worktree_of_project'] == 9


def test_receipt_brief_events_and_summaries_carry_counts_but_no_path_or_directory_name(store, tmp_path):
    store.session()
    out = tmp_path/'analysis'
    with patch.dict(os.environ, {'XDG_STATE_HOME': str(tmp_path/'state')}):
        main(['analyze', str(store.project), '--source-root', str(store.projects), '--session', SESSION, '--format', 'claude',
            '--source-version', VERSION, '--output', str(out)])
    data = json.loads((out/'analysis-brief.json').read_text())
    assert data['cwd_drift'] == dict(captures=1, rows=EXPECTED, worktrees='listed')
    mechanisms = json.loads((out/'mechanism-summaries.json').read_text())['mechanisms']
    assert [m['errors_by_cwd_class'] for m in mechanisms] == [dict(other_location=1)]
    workflow = json.loads((out/'workflow.json').read_text())
    assert workflow['cwd_drift'] == data['cwd_drift']
    ledger = [json.loads(line) for line in (out/'events.v2.jsonl').read_text().splitlines()]
    assert set(classes(ledger)) == set(CLASSES)
    private = [str(store.project), 'wt-synthetic-private', 'sub-private-dir', 'elsewhere-private-dir', 'nested-private-dir', 'synthetic-branch', 'SYNTHETIC_SECRET']
    for artifact in sorted(out.rglob('*')):
        if artifact.is_file():
            body = artifact.read_text()
            for name in private: assert name not in body, (artifact.name, name)


def test_history_receipt_summary_reports_drift_counts_only(store):
    store.session()
    result = store.default()
    receipt = summary(Namespace(history_source=None, since=None, session=None), result)
    assert receipt['cwd_drift'] == dict(captures=1, rows=EXPECTED, worktrees='listed')
    text = json.dumps(receipt)
    for name in (str(store.project), 'wt-synthetic-private', 'sub-private-dir', 'elsewhere-private-dir'): assert name not in text


def test_brief_groups_tool_errors_by_class_without_changing_mechanism_identity():
    scan = brief.Scan()
    base = dict(case_id='c', root_case_id='c')
    scan.observe(dict(base, event_id='1', activity='tool_call', tool_name='Edit', call_id='k'))
    scan.observe(dict(base, event_id='2', activity='tool_result', tool_name='Edit', call_id='k', execution_status='error', cwd_class='registered_worktree_of_project'))
    scan.observe(dict(base, event_id='3', activity='tool_call', tool_name='Edit', call_id='m'))
    scan.observe(dict(base, event_id='4', activity='tool_result', tool_name='Edit', call_id='m', execution_status='error'))
    (mechanism,) = brief.mechanism_summaries(scan.result())['mechanisms']
    assert mechanism['mechanism_id'] == 'Edit/tool_reported_error' and mechanism['errors'] == 2
    assert mechanism['errors_by_cwd_class'] == dict(registered_worktree_of_project=1, unknown=1)
