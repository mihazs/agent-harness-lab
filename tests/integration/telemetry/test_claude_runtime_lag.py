"""A Claude Code release newer than the newest verified one is surfaced once by doctor, the history receipt and the brief."""
from argparse import Namespace
from copy import deepcopy
import json
import os
from unittest.mock import patch

import pytest

from agent_harness_lab.diagnostics import brief
from agent_harness_lab.evidence import collect
from agent_harness_lab.history import runtime_lag, summary
from agent_harness_lab.onboarding import doctor
from agent_harness_lab.telemetry.claude_discovery import directory_names, recorded_versions
from agent_harness_lab.telemetry.claude_native import NEWEST_VERIFIED
from tests.integration.telemetry.test_claude_native import ClaudeNativeTests

SESSIONS = tuple('00000000-0000-4000-8000-00000000000%d' % n for n in range(1, 5))


class TestRuntimeLag:
    @pytest.fixture(autouse=True)
    def _store(self, tmp_path):
        self.f = ClaudeNativeTests(); self.f.setUp(tmp_path)
        self.project = tmp_path/'proj'; self.project.mkdir()
        self.config = tmp_path/'claude'; self.projects = self.config/'projects'
        self.name = directory_names(self.project.resolve())[0]
        self.sessions = SESSIONS

    def store(self, *versions):
        for session, version in zip(self.sessions, versions):
            rows = deepcopy(self.f.rows)
            for row in rows:
                if 'sessionId' in row: row.update(sessionId=session, version=version, cwd=str(self.project.resolve()))
            path = self.projects/self.name/(session+'.jsonl'); path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(''.join(json.dumps(row)+'\n' for row in rows))

    def env(self):
        return patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(self.config), 'CODEX_HOME': str(self.config/'no-codex')})

    def history(self):
        with self.env():
            data = collect(self.project, source_format='claude')
            return summary(Namespace(history_source=None, since=None, session=None), data)

    def test_recorded_versions_are_counts_of_dotted_versions_only(self):
        self.store('2.1.318', '2.1.399', 'SYNTH SECRET 1')
        found = recorded_versions([self.projects], self.project.resolve())
        assert found == {'2.1.318': 1, '2.1.399': 1, 'unrecognized': 1}

    def test_doctor_warns_once_when_recorded_versions_exceed_the_newest_verified(self):
        self.store('2.1.318', '2.1.399', '2.1.400')
        with self.env(): checks = [c for c in doctor(self.project)['checks'] if c['check_id'] == 'claude_runtime_lag']
        assert len(checks) == 1 and checks[0]['status'] == 'warn' and checks[0]['required_for_profile'] is False
        assert 'recorded 2.1.400' in checks[0]['observed_value'] and NEWEST_VERIFIED in checks[0]['observed_value']
        assert 'ahl sources claude pin-check --events' in checks[0]['remediation']

    @pytest.mark.parametrize('versions', [('2.1.316', '2.1.318'), ('2.1.317',), ()])
    def test_doctor_stays_silent_for_verified_older_or_absent_captures(self, versions):
        self.store(*versions)
        with self.env(): assert not [c for c in doctor(self.project)['checks'] if c['check_id'] == 'claude_runtime_lag']

    def test_the_improve_history_receipt_carries_the_lag_once_with_counts_only(self):
        self.store('2.1.318', '2.1.399', '2.1.399')
        lag = self.history()['runtime_lag']
        assert lag['status'] == 'recorded_runtime_newer_than_newest_verified' and lag['captures_by_version'] == {'2.1.399': 2}
        assert lag['newest_verified'] == NEWEST_VERIFIED and lag['newest_recorded'] == '2.1.399' and 'pin-check' in lag['next_step']
        assert str(self.project) not in json.dumps(lag)

    def test_no_lag_entry_when_every_recorded_version_is_verified(self):
        self.store('2.1.316', '2.1.318')
        assert 'runtime_lag' not in self.history()
        assert runtime_lag([dict(format='codex', recorded_versions=['9.9.9'])]) is None

    def test_the_receipt_reports_declared_range_and_unverified_sources_separately(self):
        self.store('2.1.318', '2.1.399')
        assert self.history()['pin_compatibility'] == dict(declared_range=1, unverified=1)

    def test_the_brief_names_each_captures_adapter_and_what_the_descriptive_one_lacks(self):
        self.store('2.1.318', '2.1.399')
        with self.env(): data = collect(self.project, source_format='claude')
        evidence = brief.adapter_evidence(data['sources'])
        assert evidence['captures_by_adapter'] == dict(descriptive_unverified=1, verified_native=1)
        lacking = evidence['attributable_to_descriptive_adapter']
        assert lacking['cases_without_declared_order'] > 0 and 'pin-check' in evidence['unlock']
        data = dict(data, analysis=dict(data['analysis'], method_inputs={}), caveats=[], evaluation_outcomes=[])
        rows = brief._missing(data, [], 'codex', dict(fingerprinted_messages=1), None)
        step = next(row for row in rows if row['id'] == 'verified_adapter')
        assert 'descriptive, unverified adapter' in step['what'] and step['command'].startswith('ahl sources claude pin-check')

    def test_descriptive_codex_captures_get_no_claude_attribution_or_command(self):
        codex = dict(format='codex', adapter='descriptive_unverified', adapter_evidence=dict(cases=3, cases_without_declared_order=3, tool_events=2, tool_events_without_name=2))
        evidence = brief.adapter_evidence([codex])
        assert evidence['captures_by_adapter'] == dict(descriptive_unverified=1)
        assert evidence['attributable_to_descriptive_adapter'] is None and evidence['unlock'] is None
        data = dict(sources=[codex], analysis=dict(method_inputs={}), caveats=[], evaluation_outcomes=[])
        assert not [row for row in brief._missing(data, [], 'codex', dict(fingerprinted_messages=1), None) if row['id'] == 'verified_adapter']

    def test_a_brief_over_only_verified_captures_makes_no_adapter_attribution(self):
        self.store('2.1.318', '2.1.316')
        with self.env(): data = collect(self.project, source_format='claude')
        evidence = brief.adapter_evidence(data['sources'])
        assert evidence['captures_by_adapter'] == dict(verified_native=2) and evidence['attributable_to_descriptive_adapter'] is None
        assert brief.adapter_evidence([dict(format='normalized')]) is None
