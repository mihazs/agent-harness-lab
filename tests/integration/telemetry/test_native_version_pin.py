"""Synthetic stores: a release inside the fixture-verified range parses as declared_range; any other is typed and never parsed as verified."""
# fixed-in: 943ddb01 -k declared_range_selects_only_verified_captures or explicit_and_default_discovery_agree
from argparse import Namespace
from copy import deepcopy
import json
import os
from unittest.mock import patch

import pytest

from agent_harness_lab.evidence import collect
from agent_harness_lab.exit_guidance import REMEDIATION
from agent_harness_lab.history import prepare
from agent_harness_lab.telemetry.claude_discovery import directory_names
from agent_harness_lab.telemetry.native import observed_version
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests, VERSION

PINNED, PATCH, MINOR, RANGE, MIDDLE = ('00000000-0000-4000-8000-00000000000%d' % n for n in (1, 2, 3, 4, 5))
VERIFIED = ['2.1.316', '2.1.317', '2.1.318', '2.1.322']
REASON = 'capture_version_differs_from_pin'


class TestNativeVersionPin:
    @pytest.fixture(autouse=True)
    def _store(self, tmp_path):
        """One project whose path needs sanitizing; sessions at the first verified release, the newest verified one, one between them, an unverified patch above the range and one minor above."""
        self.f = ClaudeNativeTests(); self.f.setUp(tmp_path)
        self.project = tmp_path/'my.project_x y'; self.project.mkdir()
        self.projects = tmp_path/'claude'/'projects'
        name = directory_names(self.project.resolve())[0]
        for session, version in ((PINNED, '2.1.316'), (PATCH, '2.1.319'), (MINOR, '2.2.0'), (RANGE, '2.1.318'), (MIDDLE, '2.1.317')):
            rows = deepcopy(self.f.rows)
            for row in rows:
                if 'sessionId' in row: row.update(sessionId=session, version=version, cwd=str(self.project.resolve()))
            path = self.projects/name/(session+'.jsonl'); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.name = name

    def explicit(self, **options):
        return collect(self.project, source_root=self.projects, source_format='claude', source_version=VERSION, **options)

    def default(self, **options):
        env = {'CLAUDE_CONFIG_DIR': str(self.projects.parent), 'CODEX_HOME': str(self.projects.parent/'no-codex')}
        with patch.dict(os.environ, env): return collect(self.project, source_format='claude', **options)

    def test_declared_range_selects_only_verified_captures_and_types_the_others(self):
        result = self.explicit()
        discovery = result['sources'][0]['native_coverage']['discovery']
        assert discovery['capture_counts'] == dict(selected=5, parsed=3, skipped=2, skipped_reasons={REASON: 2},
            pin_compatibility=dict(exact=0, declared_range=3, unverified=2))
        assert discovery['observed_versions'] == {'2.1.316': 1, '2.1.317': 1, '2.1.318': 1, '2.1.319': 1, '2.2.0': 1}
        assert discovery['unverified_versions'] == {'2.1.319': 1, '2.2.0': 1} and discovery['verified_versions'] == VERIFIED
        assert result['capture_reasons'] == [dict(reason=REASON, captures=2, observed_versions={'2.1.319': 1, '2.2.0': 1}, verified_versions=VERIFIED)]
        assert result['sources'][0]['pin_compatibility'] == 'declared_range'
        assert result['analysis']['events'] == 36 and any(c.startswith(REASON) for c in result['caveats'])
        text = json.dumps(result)
        for private in (PINNED, PATCH, MINOR, RANGE, MIDDLE, str(self.project), 'SYNTHETIC_SECRET'): assert private not in text

    def add(self, session, version):
        rows = deepcopy(self.f.rows)
        for row in rows:
            if 'sessionId' in row: row.update(sessionId=session, version=version, cwd=str(self.project.resolve()))
        (self.projects/self.name/(session+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def test_the_current_release_is_read_as_declared_range_and_the_releases_before_it_stay_typed(self):
        current, gap = '00000000-0000-4000-8000-0000000000c1', '00000000-0000-4000-8000-0000000000c2'
        self.add(current, '2.1.322'); self.add(gap, '2.1.320')
        explicit = self.explicit(); discovery = explicit['sources'][0]['native_coverage']['discovery']
        assert discovery['capture_counts'] == dict(selected=7, parsed=4, skipped=3, skipped_reasons={REASON: 3},
            pin_compatibility=dict(exact=0, declared_range=4, unverified=3))
        assert discovery['unverified_versions'] == {'2.1.319': 1, '2.1.320': 1, '2.2.0': 1} and discovery['verified_versions'] == VERIFIED
        by_version = {tuple(s['recorded_versions']): (s['pin_compatibility'], s['adapter']) for s in self.default()['sources']}
        assert by_version[('2.1.322',)] == ('declared_range', 'verified_native') and by_version[('2.1.320',)] == ('unverified', 'descriptive_unverified')
        assert self.explicit(session=current)['analysis']['events'] > 0 and self.explicit(session=gap)['analysis']['events'] == 0

    def test_a_store_with_only_newer_captures_names_the_cause_not_a_missing_capture(self):
        for verified in (PINNED, RANGE, MIDDLE): (self.projects/self.name/(verified+'.jsonl')).unlink()
        result = self.explicit()
        assert result['analysis']['events'] == 0 and result['sources'][0]['pin_compatibility'] == 'unverified'
        assert any(c.startswith(REASON) and '2.1.316, 2.1.317, 2.1.318, 2.1.322' in c for c in result['caveats'])
        assert 'No matching native capture was found; completeness remains unknown.' not in result['caveats']

    def test_explicit_and_default_discovery_agree_on_the_verified_range(self):
        explicit, default = self.explicit(), self.default()
        discovery = explicit['sources'][0]['native_coverage']['discovery']
        assert discovery['capture_counts']['selected'] == len(default['sources']) == 5
        assert discovery['capture_counts']['parsed'] == 3 and discovery['version_mismatch_files'] == 2
        # Default discovery reads exactly the releases the explicit range accepts with the verified adapter; the others stay descriptive.
        by_version = {tuple(s['recorded_versions']): (s['pin_compatibility'], s['adapter']) for s in default['sources']}
        assert by_version == {('2.1.316',): ('declared_range', 'verified_native'), ('2.1.317',): ('declared_range', 'verified_native'), ('2.1.318',): ('declared_range', 'verified_native'),
                        ('2.1.319',): ('unverified', 'descriptive_unverified'), ('2.2.0',): ('unverified', 'descriptive_unverified')}
        assert sum(s['pin_compatibility'] == 'declared_range' for s in default['sources']) == discovery['capture_counts']['pin_compatibility']['declared_range']
        for session in (PINNED, PATCH, RANGE, MIDDLE):
            narrowed = self.explicit(session=session)['sources'][0]['native_coverage']['discovery']['capture_counts']['selected']
            assert narrowed == len(self.default(history_session=session)['sources']) == 1

    def test_an_unverified_default_parse_states_which_adapter_lacks_what(self):
        sources = {tuple(s['recorded_versions']): s for s in self.default()['sources']}
        assert sources[('2.1.316',)]['adapter_evidence']['cases_without_declared_order'] == 0
        assert sources[('2.1.319',)]['adapter_evidence']['cases_without_declared_order'] == sources[('2.1.319',)]['adapter_evidence']['cases'] > 0

    def test_a_foreign_project_capture_in_a_selected_directory_is_foreign_whatever_its_version(self):
        foreign = '00000000-0000-4000-8000-0000000000aa'
        rows = deepcopy(self.f.rows)
        for row in rows:
            if 'sessionId' in row: row.update(sessionId=foreign, version='2.2.0', cwd=str(self.project.resolve())+'-other')
        (self.projects/self.name/(foreign+'.jsonl')).write_text(''.join(json.dumps(row)+'\n' for row in rows))
        discovery = self.explicit()['sources'][0]['native_coverage']['discovery']
        assert discovery['capture_counts']['selected'] == 5 and discovery['foreign_files'] == 1
        assert discovery['unverified_versions'] == {'2.1.319': 1, '2.2.0': 1}
        assert len(self.default()['sources']) == 5

    def test_history_session_selects_exactly_one_uuid(self):
        sub = self.projects/self.name/PINNED/'subagents'; sub.mkdir(parents=True)
        (sub/'agent-x.jsonl').write_text('')
        from agent_harness_lab.evidence import _in_session
        top = self.projects/self.name/(PINNED+'.jsonl')
        assert _in_session(top, 'claude', PINNED) and _in_session(sub/'agent-x.jsonl', 'claude', PINNED)
        for bad in ('projects', 'subagents', self.name, PINNED[-12:], PATCH):
            assert not _in_session(top, 'claude', bad) and not _in_session(sub/'agent-x.jsonl', 'claude', bad)
        rollout = self.projects/('rollout-2026-01-01T00-00-00-'+PINNED+'.jsonl')
        assert _in_session(rollout, 'codex', PINNED) and not _in_session(rollout, 'codex', PINNED[-12:]) and not _in_session(rollout, 'codex', PATCH)
        for bad in ('projects', PINNED[-12:]):
            with pytest.raises(ValueError): prepare(Namespace(history=True, since=None, history_source=None, events=None, source_root=None, session=bad))

    def test_history_session_reports_the_same_typed_reason_and_unmatched_sessions_select_nothing(self):
        assert self.default(history_session=PINNED)['capture_reasons'] == []
        patched = self.default(history_session=PATCH)
        assert patched['capture_reasons'] == [dict(reason=REASON, captures=1, observed_versions={'2.1.319': 1}, verified_versions=VERIFIED)]
        assert patched['analysis']['events'] > 0 and any(c.startswith(REASON) for c in patched['caveats'])
        assert self.default(history_session='00000000-0000-4000-8000-0000000000ff')['sources'] == []
        with pytest.raises(ValueError): self.default(history_session=PINNED, session=PINNED)

    def test_recorded_versions_are_reduced_to_dotted_digits_and_history_validates_the_session(self):
        assert observed_version('2.1.319') == '2.1.319' and observed_version('SYNTHETIC_SECRET 1.2') == 'unrecognized'
        args = Namespace(history=True, since=None, history_source=None, events=None, source_root=None, session='../x')
        with pytest.raises(ValueError): prepare(args)
        prepare(Namespace(**dict(vars(args), session=PINNED)))
        assert REASON in REMEDIATION
