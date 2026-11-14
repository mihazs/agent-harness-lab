"""Synthetic JUnit XML, pytest JSON and TAP reports; CI results describe a declared commit, never a task grade."""
import contextlib
import io
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import claude_mem, hardened, test_results
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import HostileReport, TestResultsExport as ResultsExport
from tests.support.fresh_peak import fresh_peak

SHA = '3c'*20
TAP = b'''TAP version 13
1..8
ok 1 - passes
not ok 2 - fails
  ---
  message: SYNTHETIC_SECRET
  ...
ok 3 - skip directive # SKIP no network
not ok 4 - expected failure # TODO not done
ok 5 - unexpected pass # TODO
not ok 6 - skip on a failure # SKIP
ok 7 - odd directive # skipped later
ok 8 - lower case # skip
'''
JUNIT = b'''<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="synthetic-suite" tests="5">
    <properties><property name="k" value="v"/></properties>
    <testcase classname="pkg.mod" name="test_pass" time="0.25"/>
    <testcase classname="pkg.mod" name="test_fail" time="1.5"><failure message="SYNTHETIC_SECRET">trace</failure></testcase>
    <testcase classname="pkg.mod" name="test_error"><error message="boom"/></testcase>
    <testcase classname="pkg.mod" name="test_skip" time="bogus"><skipped/></testcase>
    <testcase classname="pkg.mod" name="test_flaky"><flakyFailure/><system-out>x</system-out></testcase>
  </testsuite>
</testsuites>
'''
PYTEST = dict(created=1.0, duration=2.0, exitcode=1, root='/synthetic', environment={}, summary=dict(total=6),
    collectors=[dict(nodeid='', outcome='passed', result=[])], tests=[
        dict(nodeid='tests/test_a.py::test_ok', lineno=1, keywords=[], outcome='passed',
             setup=dict(duration=0.1, outcome='passed'), call=dict(duration=0.2, outcome='passed'), teardown=dict(duration=0.1, outcome='passed')),
        dict(nodeid='tests/test_a.py::test_bad', outcome='failed', call=dict(duration=0.5, outcome='failed', longrepr='SYNTHETIC_SECRET')),
        dict(nodeid='tests/test_a.py::test_err', outcome='error'),
        dict(nodeid='tests/test_a.py::test_skip', outcome='skipped'),
        dict(nodeid='tests/test_a.py::test_xfail', outcome='xfailed'),
        dict(nodeid='tests/test_a.py::test_odd', outcome='mystery')])


# Every tracemalloc bound below is measured by fresh_peak in a fresh interpreter: these workloads run in that child, build
# their input untraced and return the one call to measure. A mutant installs a deliberate regression there, so a test can
# show that its bound still catches it.
def mutate(mutant):
    if mutant == 'greedy':  # the string loop as a greedy repeat keeps one backtrack frame per escape (about 60x)
        hardened.JSON_STRUCTURE = re.compile(hardened.JSON_STRUCTURE.pattern.replace(b'*+', b'*'), re.S)
    elif mutant == 'materialized':  # the screen keeps every match alive instead of one at a time
        finditer = hardened.JSON_STRUCTURE.finditer
        hardened.JSON_STRUCTURE = SimpleNamespace(finditer=lambda raw: iter(list(finditer(raw))))
    elif mutant == 'unscreened':  # the pre-decode screen removed
        hardened.screen_json = lambda raw, bounds, name='JSON': (0, 0)
    elif mutant == 'retained':  # finished elements are kept alive while parsing
        class Retaining(test_results.ElementTree.XMLPullParser):
            kept = []
            def read_events(self, *args):
                for event in super().read_events(*args): self.kept.append(event); yield event
        test_results.ElementTree.XMLPullParser = Retaining
    else: assert mutant is None


def refused_or_loaded(load, raw, bounds):
    def run():
        try: load(raw, bounds, 'probe')
        except HostileReport: return len(raw), True
        return len(raw), False
    return run


def escaped_string(escape, closing, mutant):
    mutate(mutant)
    # One string is one value, so no count stops it; a greedy loop kept a backtrack frame per escape (about 60x).
    raw = b'{"tests": [], "x": "'+escape.encode()*1000000+closing.encode()
    return refused_or_loaded(hardened.screen_json, raw, dict(max_json_depth=8, max_json_containers=8, max_json_values=8))


def scalar_array(item, count, bound, mutant):
    mutate(mutant)
    raw = b'['+b','.join([item.encode()]*count)+b']'
    return refused_or_loaded(hardened.load_json, raw, dict(max_json_depth=8, max_json_containers=8, max_json_values=bound))


def budgets_array(bound, containers, mutant):
    mutate(mutant)
    raw = b'{"tests": ['+b','.join(['"\u0100"'.encode()]*bound+[b'[]']*(containers-2))+b']}'
    return refused_or_loaded(hardened.load_json, raw, dict(max_json_depth=8, max_json_containers=containers, max_json_values=bound))


def unique_keys(bound, mutant):
    mutate(mutant)
    raw = b'{"tests": [], "x": {'+b','.join(b'"%x": 0' % i for i in range(bound-1))+b'}}'
    return refused_or_loaded(hardened.load_json, raw, dict(max_json_depth=8, max_json_containers=8, max_json_values=bound))


def junit_properties(mutant):
    mutate(mutant)
    raw = b'<testsuite name="s">'+b'<properties/>'*300000+b'<testcase name="t"/>'*10+b'</testsuite>'
    return lambda: sum(1 for _ in test_results.parse_junit(raw, test_results.limits(None), set()))


def against(*mutants):
    """Run a bound test as shipped, then once per named regression, each of which its memory bound must catch."""
    return pytest.mark.parametrize('mutant', [pytest.param(None, id='shipped'), *(pytest.param(name, id=name+'-mutant') for name in mutants)])


@contextlib.contextmanager
def caught_if(mutant):
    if mutant is None: yield
    else:
        with pytest.raises(AssertionError, match='is not below'): yield


class TestResultSource:
    @pytest.fixture(autouse=True)
    def _reports(self, tmp_path):
        self.root = tmp_path
        (self.root/'reports').mkdir()
        (self.root/'reports/junit.xml').write_bytes(JUNIT)
        (self.root/'reports/pytest.json').write_text(json.dumps(PYTEST))
        self.declare([dict(path='reports/junit.xml', format='junit-xml'), dict(path='reports/pytest.json', format='pytest-json')])

    def declare(self, reports, sha=SHA, **extra):
        self.selection = dict(schema_version=1, commit_sha=sha, reports=reports, **extra)
        (self.root/'selection.json').write_text(json.dumps(self.selection))
        self.source = SourceSpec('local_file', 'test-results', 'v1', digest(str(self.root.resolve())), digest('synthetic-project'),
            digest(self.selection), identity_provenance='source_declared_id', enabled=True)
        self.manifest = self.root/'source.json'
        self.manifest.write_text(json.dumps(dict(schema_version=1, source=self.source.to_dict(), data='selection.json')))
        self.connector = ResultsExport(self.manifest)

    def junit(self, raw):
        (self.root/'reports/junit.xml').write_bytes(raw)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')])

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        assert 'SYNTHETIC_SECRET' not in output.getvalue()+error.getvalue()
        return code, output.getvalue()

    def statuses(self, fmt):
        return {self.connector.declared_cases[r.case_sha256][1]: (r.status, r.reported_outcome, r.duration_seconds)
                for r in self.connector.results if r.format == fmt}

    def test_junit_and_pytest_statuses_keep_unknowns_explicit(self):
        self.connector.read(self.source)
        assert (self.statuses('junit-xml') == {
            'test_pass': ('passed', 'no_status_element', 0.25), 'test_fail': ('failed', 'failure', 1.5),
            'test_error': ('error', 'error', None), 'test_skip': ('skipped', 'skipped', None),
            'test_flaky': ('unknown', 'unrecognized', None)})
        reported = self.statuses('pytest-json')
        assert reported['tests/test_a.py::test_ok'][:2] == ('passed', 'passed')
        assert reported['tests/test_a.py::test_ok'][2] == pytest.approx(0.4)
        assert reported['tests/test_a.py::test_bad'] == ('failed', 'failed', 0.5)
        assert reported['tests/test_a.py::test_err'] == ('error', 'error', None)
        assert reported['tests/test_a.py::test_xfail'] == ('unknown', 'xfailed', None)
        assert reported['tests/test_a.py::test_odd'] == ('unknown', 'unrecognized', None)
        summary = self.connector.summary
        assert summary['statuses'] == dict(passed=2, failed=2, error=2, skipped=2, unknown=3)
        assert ({'invalid_duration', 'unrecognized_case_element', 'unrecognized_outcome',
            'commit_binding_declared', 'reruns_not_independent'} <= set(summary['warnings']))

    def test_overflowing_pytest_phase_sum_is_an_invalid_duration_not_a_refusal(self):
        report = dict(PYTEST, tests=[dict(nodeid='tests/test_a.py::test_huge', outcome='passed',
            setup=dict(duration=1e308, outcome='passed'), call=dict(duration=1e308, outcome='passed'))])
        (self.root/'reports/pytest.json').write_text(json.dumps(report))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        self.connector.read(self.source)
        assert self.statuses('pytest-json') == {'tests/test_a.py::test_huge': ('passed', 'passed', None)}
        assert 'invalid_duration' in self.connector.summary['warnings']

    def test_results_are_declared_commit_evidence_never_task_outcomes_or_public(self):
        snapshot = self.connector.read(self.source)
        summary = self.connector.summary
        assert (summary['commit_sha256'], summary['commit_binding'], summary['commit_verified']) == (digest(SHA), 'caller_declared', False)
        for key in ('task_outcomes_verified', 'verification_authorized', 'execution_authorized', 'scripts_executed',
                    'publication_allowed', 'network_used'):
            assert summary[key] is False
        assert summary['independent_trials'] is None
        assert not public_export_allowed([summary])
        assert (not public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))
        for result in self.connector.results:
            assert (result.commit_binding, result.commit_verified, result.outcome_scope) == ('caller_declared', False, 'declared_commit_ci_evidence')
        for record in snapshot.records:
            assert (record.plane, record.causal_status, record.independence, record.capture_completeness) == ('development_artifact', 'unknown', 'unknown', 'partial')
        assert sum(r.record_type == 'test_report' for r in snapshot.records) == 2

    @pytest.mark.parametrize('sha', [
        pytest.param('3c'*10, id='short'), pytest.param('3C'*20, id='uppercase'), pytest.param(' '+'3c'*20, id='padded'),
        pytest.param('3c'*21, id='too-long'), pytest.param(42, id='integer'), pytest.param(None, id='missing')])
    def test_commit_identity_must_be_full_lowercase(self, sha):
        self.declare(self.selection['reports'], sha=sha)
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_commit_identity_is_bound_to_scope(self):
        self.declare(self.selection['reports'], sha='4d'*32)
        self.connector.read(self.source)
        assert self.connector.summary['commit_sha256'] == digest('4d'*32)
        forged = dict(self.selection, commit_sha='5e'*20)
        (self.root/'selection.json').write_text(json.dumps(forged))
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_same_test_at_other_commit_or_report_is_a_distinct_record(self):
        first = {r.native_id_sha256 for r in self.connector.read(self.source).records}
        self.declare(self.selection['reports'], sha='6f'*20)
        second = {r.native_id_sha256 for r in self.connector.read(self.source).records}
        assert not first & second

    def test_duplicate_case_names_are_kept_as_flagged_occurrences(self):
        self.junit(b'<testsuite name="s"><testcase classname="c" name="t"/><testcase classname="c" name="t"><failure/></testcase></testsuite>')
        snapshot = self.connector.read(self.source)
        assert [(r.occurrence, r.status) for r in self.connector.results] == [(0, 'passed'), (1, 'failed')]
        assert 'duplicate_case_identity' in self.connector.summary['warnings']
        assert len({r.native_id_sha256 for r in snapshot.records}) == 3

    def test_conflicting_and_suite_level_statuses_are_not_attributed(self):
        self.junit(b'<testsuites><testsuite name="s"><error message="setup"/><testcase name="t"><failure/><skipped/></testcase></testsuite></testsuites>')
        self.connector.read(self.source)
        result, = self.connector.results
        assert (result.status, result.reported_outcome) == ('unknown', 'conflicting')
        assert {'conflicting_case_status', 'suite_level_status_unattributed'} <= set(self.connector.summary['warnings'])

    @pytest.mark.parametrize('raw', [
        pytest.param(b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
                     b'<testsuite name="s"><testcase name="&lol2;"/></testsuite>', id='entity-expansion'),
        pytest.param(b'<!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuite><testcase name="&x;"/></testsuite>', id='external-entity'),
        pytest.param(b'<!doctype testsuite><testsuite/>', id='lowercase-doctype'),
        pytest.param(b'<testsuite><!ELEMENT x ANY></testsuite>', id='element-declaration'),
        pytest.param('<?xml version="1.0" encoding="UTF-16"?><testsuite/>'.encode('utf-16'), id='utf-16'),
        pytest.param(b'<?xml version="1.0" encoding="cp037"?><testsuite/>', id='ebcdic'),
        pytest.param(b'<testsuite>\0</testsuite>', id='nul'),
        pytest.param(b'<testsuite>\xff</testsuite>', id='invalid-utf-8')])
    def test_doctype_entities_and_hidden_encodings_are_refused_before_parsing(self, raw):
        self.junit(raw)
        with patch.object(test_results.ElementTree, 'XMLPullParser', side_effect=AssertionError('parser ran')):
            with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('raw', [
        pytest.param(b'<testsuite><testcase name="&lol;"/></testsuite>', id='undefined-entity'),
        pytest.param(b'<testsuite><testcase name="t">', id='unclosed'), pytest.param(b'', id='empty'),
        pytest.param(b'<html/>', id='not-junit'), pytest.param(b'<testsuites><testcase name="t"/></testsuites>', id='case-outside-suite'),
        pytest.param(b'<testsuite><testcase/></testsuite>', id='unnamed-case')])
    def test_undefined_entities_and_malformed_xml_are_rejected(self, raw):
        self.junit(raw)
        with pytest.raises(ValueError): self.connector.read(self.source)

    @pytest.mark.parametrize('raw,bound', [
        pytest.param(b'<testsuite>'+b'<testcase name="t"/>'*50+b'</testsuite>', dict(max_elements=10), id='elements'),
        pytest.param(b'<testsuite>'+b'<testsuite>'*40+b'</testsuite>'*40+b'</testsuite>', {}, id='depth'),
        pytest.param(b'<testsuite name="'+b'a'*5000+b'"/>', {}, id='attribute-length'),
        pytest.param(b'<testsuite '+b' '.join(b'a%d="1"' % i for i in range(70))+b'/>', {}, id='attribute-count'),
        pytest.param(b'<testsuite>'+b'<testcase name="t"/>'*20+b'</testsuite>', dict(max_cases=5), id='cases'),
        pytest.param(b'<testsuite>'+b' '*2048+b'</testsuite>', dict(max_report_bytes=1024), id='report-bytes')])
    def test_xml_bounds_abort_early(self, raw, bound):
        (self.root/'reports/junit.xml').write_bytes(raw)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')], **({'limits': bound} if bound else {}))
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_xml_parsing_stops_feeding_once_a_bound_is_exceeded(self):
        feeds = []
        actual = test_results.ElementTree.XMLPullParser
        class Counting(actual):
            def feed(self, data): feeds.append(len(data)); return super().feed(data)
        (self.root/'reports/junit.xml').write_bytes(b'<testsuite>'+b'<testcase name="t"/>'*20000+b'</testsuite>')
        self.declare([dict(path='reports/junit.xml', format='junit-xml')], limits=dict(max_elements=100))
        with patch.object(test_results.ElementTree, 'XMLPullParser', Counting):
            with pytest.raises(HostileReport): self.connector.read(self.source)
        assert len(feeds) == 1

    @pytest.mark.parametrize('raw,bound', [
        pytest.param(b'<testsuite '+b' '.join(b'a%d="1"' % i for i in range(65))+b'/>', None, id='attribute-count'),
        pytest.param(b'<testsuite a="1" b="2" c="3"/>', 2, id='lowered-attribute-bound'),
        pytest.param(b'<testsuite><!-- open', None, id='open-comment'),
        pytest.param(b'<testsuite><![CDATA[ open', None, id='open-cdata'),
        pytest.param(b'<testsuite a="unterminated/>', None, id='open-attribute')])
    def test_oversized_start_tags_are_refused_before_expat_builds_them(self, raw, bound):
        (self.root/'reports/junit.xml').write_bytes(raw)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')],
            **({'limits': dict(max_attributes=bound)} if bound else {}))
        with patch.object(test_results.ElementTree, 'XMLPullParser', side_effect=AssertionError('parser ran')):
            with pytest.raises(HostileReport): self.connector.read(self.source)

    def test_markup_characters_inside_attributes_cdata_comments_and_instructions_are_not_start_tags(self):
        self.junit(b'<?xml version="1.0"?><testsuite name="s"><testcase name="t" message=\'a > b\'>'
                   b'<system-out><![CDATA[<Foo object at 0x1> </x>]]></system-out><!-- <y z --></testcase><?pi <a ?></testsuite>')
        self.connector.read(self.source)
        assert self.statuses('junit-xml') == {'t': ('passed', 'no_status_element', None)}

    def test_status_attributes_and_contradictory_phases_never_pass(self):
        self.junit(b'<testsuite name="s"><testcase name="a" status="failed"/><testcase name="b" skipped="true"/>'
                   b'<testcase name="c" file="x.py" line="3"/></testsuite>')
        self.connector.read(self.source)
        assert (self.statuses('junit-xml') == {'a': ('unknown', 'status_attribute', None),
            'b': ('unknown', 'status_attribute', None), 'c': ('passed', 'no_status_element', None)})
        assert 'status_attribute_unrecognized' in self.connector.summary['warnings']
        (self.root/'reports/pytest.json').write_text(json.dumps(dict(tests=[
            dict(nodeid='t::a', outcome='passed', teardown=dict(outcome='failed')),
            dict(nodeid='t::b', outcome='passed', setup=dict(outcome='passed'), call=dict(outcome='passed'))])))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        self.connector.read(self.source)
        assert {k: v[:2] for k, v in self.statuses('pytest-json').items()} == {'t::a': ('unknown', 'conflicting'), 't::b': ('passed', 'passed')}

    @pytest.mark.parametrize('body,bound', [
        pytest.param('{"tests": ['+','.join(['{}']*20)+']}', dict(max_json_containers=10), id='containers'),
        # Deeper than the default bound yet shallow enough that json.loads would decode it.
        pytest.param('{"tests": [], "x": '+'['*100+']'*100+'}', None, id='default-depth'),
        pytest.param('{"tests": [], "x": '+'['*10+']'*10+'}', dict(max_json_depth=5), id='lowered-depth'),
        pytest.param('{"tests": [], "s": "'+'['*200+'\\"'+'"}', dict(max_json_depth=2), id='brackets-in-strings')])
    def test_pytest_json_containers_and_depth_are_bounded_before_decoding(self, body, bound):
        (self.root/'reports/pytest.json').write_text(body)
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], **({'limits': bound} if bound else {}))
        with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
            if bound == dict(max_json_depth=2):
                # Brackets inside strings are not structure; screening passes and decoding is reached.
                with pytest.raises(AssertionError, match='decoded'): self.connector.read(self.source)
            else:
                with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('raw', [
        pytest.param(b'"'+b'\\"'*(512*1024), id='bare-string'),
        pytest.param(b'{"tests": [], "x": "'+b'\\"'*(512*1024), id='nested-string')])
    def test_pytest_json_unterminated_string_is_refused_in_linear_time(self, raw):
        started = time.monotonic()
        with pytest.raises(HostileReport, match='unterminated'):
            test_results.screen_json(raw, test_results.limits(None))
        # A rescan from every quote took nearly a minute at 80 KB; a single pass over 1 MiB takes milliseconds.
        assert time.monotonic()-started < 2

    def test_pytest_json_unterminated_string_in_a_report_is_refused_before_decoding(self):
        (self.root/'reports/pytest.json').write_bytes(b'{"tests": [], "x": "'+b'\\"'*(512*1024))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
            with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('bound,outcome', [pytest.param(11, HostileReport, id='refused'), pytest.param(12, AssertionError, id='reaches-decoding')])
    def test_pytest_json_scalar_values_are_bounded_before_decoding(self, bound, outcome):
        # Two values in the test entry plus ten floats; the entry's object closes early, so decoding would reach the hook.
        body = '{"tests": [{"nodeid": "a", "outcome": "passed"}], "x": ['+', '.join(['1.5']*10)+']}'
        (self.root/'reports/pytest.json').write_text(body)
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_json_values=bound))
        with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
            with pytest.raises(outcome): self.connector.read(self.source)

    def test_scalar_value_limit_only_lowers_the_hard_ceiling(self):
        (self.root/'reports/pytest.json').write_text('{"tests": [{"nodeid": "a", "outcome": "passed"}]}')
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_json_values=10**9))
        with pytest.raises(ValueError, match='only lower'): self.connector.read(self.source)
        # The ceilings admit 100k tests of about 25 values, and 100k Claude-Mem rows of about 40 fields.
        assert test_results.LIMITS['max_json_values'] >= test_results.LIMITS['max_cases']*25
        assert claude_mem.LIMITS['max_json_values'] >= claude_mem.LIMITS['max_rows']*40

    @pytest.mark.parametrize('raw,values', [
        pytest.param(b'[]', 0, id='empty-array'), pytest.param(b'{}', 0, id='empty-object'), pytest.param(b'[1]', 1, id='one-number'),
        pytest.param(b'[[], {}, [[{}]]]', 0, id='nested-empty'), pytest.param(b'{"a": [], "b": {}}', 0, id='keys-not-counted'),
        pytest.param(b'{"a" : 1, "b": "x,]"}', 2, id='delimiters-in-strings'),
        pytest.param(b'["a,b", "[{", "}]:,", 1.5e3, true, false, null]', 7, id='scalar-kinds'),
        pytest.param(b'"\\"],[\\\\"', 1, id='escaped-quote'),
        pytest.param(b'{"k": {"k2": [1, 2, {"z": "q\\"],"}]}}', 3, id='deep-scalars'), pytest.param(b' -0.5 ', 1, id='bare-number')])
    def test_scalar_count_skips_keys_containers_and_string_contents(self, raw, values):
        def admitted(raw, bound):
            try: hardened.load_json(raw, dict(max_json_depth=8, max_json_containers=64, max_json_values=bound), 'probe')
            except HostileReport: return False
            return True
        assert admitted(raw, values)
        if values: assert not admitted(raw, values-1)

    def test_unterminated_string_is_refused_in_one_pass(self):
        # An unterminated string is refused in one pass instead of rescanned from every later quote.
        bounds = dict(max_json_depth=8, max_json_containers=64, max_json_values=10)
        with patch.object(hardened, 'unique_object', side_effect=AssertionError('decoded')):
            started = time.perf_counter()
            with pytest.raises(HostileReport, match='probe string is unterminated'):
                hardened.load_json(b'{"tests": ["'+b'\\"'*40000, bounds, 'probe')
            assert time.perf_counter()-started < 2

    @against('greedy')
    @pytest.mark.parametrize('closing', [pytest.param('"}', id='closed'), pytest.param('', id='unterminated')])
    @pytest.mark.parametrize('escape', [pytest.param('\\\\', id='backslash'), pytest.param('\\"', id='quote'), pytest.param('\\n', id='newline')])
    def test_long_escaped_string_is_screened_in_constant_memory(self, escape, closing, mutant):
        with caught_if(mutant):
            measured = fresh_peak(escaped_string, escape, closing, mutant)
            size, refused = measured.value
            measured.below(size//100)
            assert refused == (not closing)

    # Measured: "ab" 11.2x, a non-Latin-1 character 15.2x (about 76 B a value, the worst per input byte), an astral
    # character 12.6x (about 88 B, the worst per value) and 1.5 9.0x. The documented bounds are 16x and 90 B a value.
    @against('materialized', 'unscreened')
    @pytest.mark.parametrize('item', [pytest.param('"ab"', id='ascii'), pytest.param('"\u0100"', id='non-latin-1'),
                                      pytest.param('"\U0001F600"', id='astral'), pytest.param('1.5', id='float')])
    def test_scalar_heavy_json_peak_stays_a_bounded_multiple_of_input(self, item, mutant):
        bound = 100000
        with caught_if(mutant):
            measured = fresh_peak(scalar_array, item, bound, bound, mutant)
            size, refused = measured.value
            measured.below(16*size)
            measured.below(90*bound)
            assert not refused
            # Twice the bound is refused by the screen, which holds one match at a time.
            measured = fresh_peak(scalar_array, item, 2*bound, bound, mutant)
            size, refused = measured.value
            measured.below(size//100)
            assert refused

    @against('materialized')
    def test_value_and_container_budgets_fill_in_one_document(self, mutant):
        bound, containers = 100000, 40000
        # Both budgets fill in one document, so the documented peak is their sum: about 90 B a value, 150 B a container.
        with caught_if(mutant):
            measured = fresh_peak(budgets_array, bound, containers, mutant)
            measured.below(90*bound+150*containers)
            assert not measured.value[1]

    @against('materialized')
    def test_unique_keys_are_bounded_only_by_the_byte_ceiling(self, mutant):
        bound = 100000
        # Residual: keys are not counted, so an object of unique keys passes the value bound with each key and value
        # costing well over 90 B; only the byte ceiling bounds it, at about 20 to 24 times the input.
        with caught_if(mutant):
            measured = fresh_peak(unique_keys, bound, mutant)
            size, refused = measured.value
            assert not refused
            assert measured.peak > 150*bound
            measured.below(25*size)

    @against('retained')
    def test_finished_elements_are_released_while_parsing(self, mutant):
        # Retaining the 300k finished elements peaked near 60 MiB; released elements stay a few MiB.
        with caught_if(mutant):
            measured = fresh_peak(junit_properties, mutant)
            measured.below(16*1024*1024)
            assert measured.value == 10

    @pytest.mark.parametrize('bound', [pytest.param(dict(max_cases=0), id='zero'), pytest.param(dict(max_cases=10**9), id='above-ceiling'),
        pytest.param(dict(unknown=1), id='unknown-limit'), pytest.param(dict(max_depth=True), id='boolean'), pytest.param([], id='not-a-mapping')])
    def test_limits_only_lower_hard_ceilings(self, bound):
        self.declare(self.selection['reports'], limits=bound)
        with pytest.raises(ValueError): self.connector.read(self.source)

    @pytest.mark.parametrize('body', [
        pytest.param('{"tests": [], "tests": []}', id='duplicate-key'),
        pytest.param('{"tests": [{"nodeid": "a", "outcome": "passed", "call": {"duration": NaN}}]}', id='constant'),
        pytest.param('{"tests": [], "x": '+'['*100+']'*100+'}', id='depth'),
        pytest.param('{"tests": {}}', id='tests-not-a-list'), pytest.param('[]', id='root-not-an-object'),
        pytest.param('{"tests": [{"outcome": "passed"}]}', id='missing-nodeid'),
        pytest.param('{"tests": [{"nodeid": "a", "outcome": "passed", "call": []}]}', id='phase-not-an-object'),
        pytest.param('{"tests": [], "collectors": {}}', id='collectors-not-a-list'),
        pytest.param('{"tests": [], "x": '+'['*100000+']'*100000+'}', id='extreme-depth'),
        pytest.param('\ufeff{"tests": []}', id='byte-order-mark')])
    def test_pytest_json_rejects_duplicates_constants_depth_and_schema(self, body):
        (self.root/'reports/pytest.json').write_text(body)
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_pytest_json_case_count_is_bounded(self):
        (self.root/'reports/pytest.json').write_text(json.dumps(dict(tests=[dict(nodeid=str(i), outcome='passed') for i in range(9)])))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_cases=8))
        with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('reports', [
        pytest.param([], id='empty'), pytest.param([dict(path='../x.xml', format='junit-xml')], id='escaping-path'),
        pytest.param([dict(path='reports/junit.xml', format='subunit')], id='unknown-format'),
        pytest.param([dict(path='reports/junit.xml', format='junit-xml')]*2, id='duplicate'),
        pytest.param([dict(path='/etc/passwd', format='junit-xml')], id='absolute-path'),
        pytest.param([dict(path='reports/junit.xml', format='junit-xml', extra=1)], id='extra-field')])
    def test_selection_paths_and_formats_are_checked(self, reports):
        self.declare(reports)
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_selection_links_and_changes_are_checked(self):
        (self.root/'reports/link.xml').symlink_to(self.root/'reports/junit.xml')
        self.declare([dict(path='reports/link.xml', format='junit-xml')])
        with pytest.raises(ValueError): self.connector.read(self.source)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')])
        actual = test_results.regular_sha256
        with patch.object(test_results, 'regular_sha256', lambda path, limit: '0'*64):
            with pytest.raises(ValueError): self.connector.read(self.source)
        assert actual is test_results.regular_sha256

    def tap(self, raw, **extra):
        (self.root/'reports/report.tap').write_bytes(raw)
        self.declare([dict(path='reports/report.tap', format='tap')], **extra)

    def tap_cases(self):
        return [(self.connector.declared_cases[r.case_sha256][1], r.status, r.reported_outcome) for r in self.connector.results]

    def test_tap_statuses_follow_directives_and_keep_unknowns_explicit(self):
        self.tap(TAP)
        snapshot = self.connector.read(self.source)
        assert self.tap_cases() == [
            ('passes', 'passed', 'ok'), ('fails', 'failed', 'not_ok'), ('skip directive', 'skipped', 'skip'),
            # TODO inverts the meaning of the result, and a SKIP on a failing line contradicts itself: neither is a verdict.
            ('expected failure', 'unknown', 'todo'), ('unexpected pass', 'unknown', 'todo'),
            ('skip on a failure', 'unknown', 'conflicting'), ('odd directive', 'unknown', 'unrecognized_directive'),
            ('lower case', 'skipped', 'skip')]
        assert {'conflicting_case_status', 'unrecognized_directive'} <= set(self.connector.summary['warnings'])
        observed = [r.outcome['value'] for r in snapshot.records if r.record_type == 'test_case_result']
        assert observed == ['success', 'failure', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown', 'unknown']

    def test_tap_diagnostic_blocks_and_messages_are_not_stored(self):
        self.tap(TAP)
        snapshot = self.connector.read(self.source)
        assert 'SYNTHETIC_SECRET' not in repr(self.connector.results) + repr(snapshot.records) + repr(self.connector.summary)
        assert self.connector.summary['cases'] == 8

    @pytest.mark.parametrize('raw,cases,warnings', [
        pytest.param(b'1..2\nok 1 - a\nok 2 - b\n', 2, set(), id='plan-first'),
        pytest.param(b'ok 1 - a\nok 2 - b\n1..2\n', 2, set(), id='plan-last'),
        pytest.param(b'1..3\nok 1 - a\nok 2 - b\n', 2, {'tap_plan_mismatch'}, id='truncated'),
        pytest.param(b'1..1\nok 1 - a\nok 2 - b\n', 2, {'tap_plan_mismatch'}, id='more-than-planned'),
        pytest.param(b'ok 1 - a\n', 1, {'tap_plan_absent'}, id='no-plan'),
        pytest.param(b'1..0 # SKIP nothing to run\n', 0, {'tap_plan_skipped'}, id='skipped-plan')])
    def test_tap_plan_that_disagrees_with_the_test_points_is_flagged_never_filled_in(self, raw, cases, warnings):
        self.tap(raw)
        self.connector.read(self.source)
        found = set(self.connector.summary['warnings']) & {'tap_plan_mismatch', 'tap_plan_absent', 'tap_plan_skipped'}
        assert (len(self.connector.results), found) == (cases, warnings)

    @pytest.mark.parametrize('raw', [
        pytest.param(b'', id='empty'), pytest.param(b'# only a comment\n', id='comment-only'),
        pytest.param(b'TAP version 13\n', id='version-only'), pytest.param(b'not a tap report\n', id='unrelated-text'),
        pytest.param(b'1..2\n1..2\nok 1\nok 2\n', id='duplicate-plan'), pytest.param(b'2..3\nok 2\nok 3\n', id='plan-not-from-one'),
        pytest.param(b'TAP version 99\nok 1 - a\n', id='unsupported-version'),
        pytest.param(b'TAP version 12\nok 1 - a\n', id='version-twelve-is-versionless')])
    def test_tap_without_test_points_or_with_a_malformed_header_is_rejected(self, raw):
        self.tap(raw)
        with pytest.raises(ValueError): self.connector.read(self.source)

    def test_tap_bail_out_ends_the_import_and_never_invents_the_rest(self):
        self.tap(b'TAP version 14\n1..3\nok 1 - a\nBail out! database down\nok 2 - b\nnot ok 3 - c\n')
        self.connector.read(self.source)
        assert self.tap_cases() == [('a', 'passed', 'ok')]
        assert {'tap_bail_out_reported', 'tap_plan_mismatch'} <= set(self.connector.summary['warnings'])

    def test_tap_names_cases_by_description_and_falls_back_to_position(self):
        self.tap(b'ok 1 - a\nok 3 b\nok - c\nok\n1..4\n')
        self.connector.read(self.source)
        assert [name for name, _, _ in self.tap_cases()] == ['a', 'b', 'c', '#4']
        assert 'tap_test_number_out_of_sequence' in self.connector.summary['warnings']

    def test_tap_repeated_descriptions_are_kept_as_flagged_occurrences(self):
        self.tap(b'1..2\nok 1 - same\nnot ok 2 - same\n')
        self.connector.read(self.source)
        assert [(r.occurrence, r.status) for r in self.connector.results] == [(0, 'passed'), (1, 'failed')]
        assert 'duplicate_case_identity' in self.connector.summary['warnings']

    def test_tap_subtest_lines_are_not_counted_beside_their_parent(self):
        self.tap(b'TAP version 14\n1..1\n# Subtest: parent\n    1..2\n    ok 1 - inner a\n    not ok 2 - inner b\nnot ok 1 - parent\n')
        self.connector.read(self.source)
        assert self.tap_cases() == [('parent', 'failed', 'not_ok')]
        assert 'tap_subtest_lines_not_imported' in self.connector.summary['warnings']

    @pytest.mark.parametrize('line', [
        pytest.param(b'okay 1 - a', id='ok-prefix'), pytest.param(b'not okay', id='not-ok-prefix'),
        pytest.param(b'notok 1 - a', id='run-together'), pytest.param(b'Ok 1 - a', id='capitalized'),
        pytest.param(b'stray program output', id='stray-output'), pytest.param(b'TAP version 13', id='late-version')])
    def test_tap_lines_that_are_not_test_points_are_ignored_with_a_warning(self, line):
        self.tap(b'1..1\n'+line+b'\nok 1 - kept\n')
        self.connector.read(self.source)
        assert self.tap_cases() == [('kept', 'passed', 'ok')]
        assert 'tap_unrecognized_line' in self.connector.summary['warnings']

    def test_tap_comments_and_pragmas_are_not_warnings(self):
        self.tap(b'# a comment\npragma +strict\n\n1..1\nok 1 - kept\n')
        self.connector.read(self.source)
        assert 'tap_unrecognized_line' not in self.connector.summary['warnings']

    def test_tap_escaped_hash_is_part_of_the_description_not_a_directive(self):
        self.tap(b'1..2\nok 1 - issue \\# 5 # SKIP later\nok 2 - cost 5\\# todo\n')
        self.connector.read(self.source)
        assert self.tap_cases() == [('issue \\# 5', 'skipped', 'skip'), ('cost 5\\# todo', 'passed', 'ok')]

    @pytest.mark.parametrize('raw', [
        pytest.param(b'1..1\nok 1 - a\0\n', id='nul'), pytest.param(b'1..1\nok 1 - \xff\n', id='invalid-utf-8'),
        pytest.param('1..1\nok 1 - a\n'.encode('utf-16'), id='utf-16'),
        pytest.param(b'1..1\nok 1 - '+b'a'*5000+b'\n', id='long-line')])
    def test_tap_hidden_encodings_and_oversized_lines_are_refused(self, raw):
        self.tap(raw)
        with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('length,refused', [pytest.param(4096, False, id='at-the-ceiling'), pytest.param(4097, True, id='over-the-ceiling')])
    def test_tap_line_length_ceiling_is_exact(self, length, refused):
        self.tap(b'ok 1 - '+b'a'*(length-7)+b'\n')
        if refused:
            with pytest.raises(HostileReport): self.connector.read(self.source)
        else:
            self.connector.read(self.source)
            assert len(self.connector.results) == 1

    def test_tap_line_limit_only_lowers_the_ceiling(self):
        self.tap(b'ok 1 - a\n', limits=dict(max_line_length=4097))
        with pytest.raises(ValueError, match='only lower'): self.connector.read(self.source)
        self.tap(b'ok 1 - '+b'a'*20+b'\n', limits=dict(max_line_length=16))
        with pytest.raises(HostileReport): self.connector.read(self.source)

    @pytest.mark.parametrize('raw', [
        pytest.param(b'1..2\n'+b'ok - a\n'*6, id='more-points-than-the-bound'),
        pytest.param(b'1..6\n', id='plan-above-the-bound'), pytest.param(b'1..99999999999999999999\n', id='plan-beyond-any-integer-width')])
    def test_tap_case_count_is_bounded_by_points_and_by_the_declared_plan(self, raw):
        self.tap(raw, limits=dict(max_cases=5))
        with pytest.raises(HostileReport): self.connector.read(self.source)

    def test_tap_parsing_stops_at_the_case_bound_before_reading_later_lines(self):
        # Every later line is itself refusable; the bound is what ends the read, so its message is the one raised.
        self.tap(b'ok - a\n'*10+b'1..2\n1..2\n\xff\n', limits=dict(max_cases=5))
        with pytest.raises(HostileReport, match='case bound'): self.connector.read(self.source)

    def test_tap_cli_preview_and_import_keep_hashes_only(self):
        self.tap(TAP)
        code, preview = self.invoke('preview')
        assert code == 0
        summary = json.loads(preview)['test_results_summary']
        assert (summary['cases'], summary['statuses']['unknown'], summary['profile']) == (8, 4, 'ci-test-results-v1')
        code, receipt = self.invoke('import')
        assert code == 0
        raw = (self.root/'private/cache.json').read_text()
        for secret in ('SYNTHETIC_SECRET', 'skip directive', 'expected failure', 'reports/report.tap', SHA):
            assert secret not in raw

    def test_cli_preview_import_and_reimport_keep_hashes_only(self):
        code, preview = self.invoke('preview')
        assert code == 0
        summary = json.loads(preview)['test_results_summary']
        assert (summary['cases'], summary['profile']) == (11, 'ci-test-results-v1')
        assert not (self.root/'private').exists()
        code, receipt = self.invoke('import')
        assert code == 0
        assert json.loads(receipt)['imported'] == 13
        raw = (self.root/'private/cache.json').read_text()
        for secret in ('SYNTHETIC_SECRET', 'test_fail', 'pkg.mod', 'synthetic-suite', 'tests/test_a.py', SHA, 'reports/junit.xml'):
            assert secret not in raw
        assert json.loads(self.invoke('import')[1])['skipped'] == 13
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'reports/junit.xml').write_bytes(b'<!DOCTYPE x><testsuite/>')
        assert self.invoke('import')[0] == 2
        assert (self.root/'private/cache.json').read_bytes() == before

    def test_disabled_or_mismatched_sources_are_blocked_before_reports_are_read(self):
        opened = []
        actual = test_results.read_regular
        def read(path, limit): opened.append(path); return actual(path, limit)
        with patch.object(test_results, 'read_regular', side_effect=read):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = main(['sources', 'preview', '--manifest', str(self.manifest), '--project-sha256', digest('other'),
                    '--scope-sha256', self.source.scope_sha256])
        assert code == 2
        assert opened == []

