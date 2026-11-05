"""Synthetic JUnit XML and pytest JSON reports; CI results describe a declared commit, never a task grade."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.sources import claude_mem, hardened, test_results
from agent_harness_lab.sources.schema import SourceSpec, digest
from agent_harness_lab.sources.test_results import HostileReport, TestResultsExport

SHA = '3c'*20
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


class TestResultSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
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
        self.connector = TestResultsExport(self.manifest)

    def junit(self, raw):
        (self.root/'reports/junit.xml').write_bytes(raw)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')])

    def invoke(self, operation):
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            code = main(['sources', operation, '--manifest', str(self.manifest), '--project-sha256', self.source.project_sha256,
                '--scope-sha256', self.source.scope_sha256, '--cache', str(self.root/'private/cache.json')])
        self.assertNotIn('SYNTHETIC_SECRET', output.getvalue()+error.getvalue())
        return code, output.getvalue()

    def statuses(self, fmt):
        return {self.connector.declared_cases[r.case_sha256][1]: (r.status, r.reported_outcome, r.duration_seconds)
                for r in self.connector.results if r.format == fmt}

    def test_junit_and_pytest_statuses_keep_unknowns_explicit(self):
        self.connector.read(self.source)
        self.assertEqual(self.statuses('junit-xml'), {
            'test_pass': ('passed', 'no_status_element', 0.25), 'test_fail': ('failed', 'failure', 1.5),
            'test_error': ('error', 'error', None), 'test_skip': ('skipped', 'skipped', None),
            'test_flaky': ('unknown', 'unrecognized', None)})
        pytest = self.statuses('pytest-json')
        self.assertEqual(pytest['tests/test_a.py::test_ok'][:2], ('passed', 'passed'))
        self.assertAlmostEqual(pytest['tests/test_a.py::test_ok'][2], 0.4)
        self.assertEqual(pytest['tests/test_a.py::test_bad'], ('failed', 'failed', 0.5))
        self.assertEqual(pytest['tests/test_a.py::test_err'], ('error', 'error', None))
        self.assertEqual(pytest['tests/test_a.py::test_xfail'], ('unknown', 'xfailed', None))
        self.assertEqual(pytest['tests/test_a.py::test_odd'], ('unknown', 'unrecognized', None))
        summary = self.connector.summary
        self.assertEqual(summary['statuses'], dict(passed=2, failed=2, error=2, skipped=2, unknown=3))
        self.assertTrue({'invalid_duration', 'unrecognized_case_element', 'unrecognized_outcome',
            'commit_binding_declared', 'reruns_not_independent'} <= set(summary['warnings']))

    def test_results_are_declared_commit_evidence_never_task_outcomes_or_public(self):
        snapshot = self.connector.read(self.source)
        summary = self.connector.summary
        self.assertEqual((summary['commit_sha256'], summary['commit_binding'], summary['commit_verified']),
                         (digest(SHA), 'caller_declared', False))
        for key in ('task_outcomes_verified', 'verification_authorized', 'execution_authorized', 'scripts_executed',
                    'publication_allowed', 'network_used'):
            self.assertIs(summary[key], False)
        self.assertIsNone(summary['independent_trials'])
        self.assertFalse(public_export_allowed([summary]))
        self.assertFalse(public_export_allowed([dict(summary, publication_allowed=True,
            provenance=dict(source_kind='synthetic', approved_publication=True, source_ref='x', derived_from_private=False))]))
        for result in self.connector.results:
            self.assertEqual((result.commit_binding, result.commit_verified, result.outcome_scope),
                             ('caller_declared', False, 'declared_commit_ci_evidence'))
        for record in snapshot.records:
            self.assertEqual((record.plane, record.causal_status, record.independence, record.capture_completeness),
                             ('development_artifact', 'unknown', 'unknown', 'partial'))
        self.assertEqual(sum(r.record_type == 'test_report' for r in snapshot.records), 2)

    def test_commit_identity_must_be_full_lowercase_and_bound_to_scope(self):
        for sha in ('3c'*10, '3C'*20, ' '+'3c'*20, '3c'*21, 42, None):
            with self.subTest(sha=sha):
                self.declare(self.selection['reports'], sha=sha)
                with self.assertRaises(ValueError): self.connector.read(self.source)
        self.declare(self.selection['reports'], sha='4d'*32)
        self.connector.read(self.source)
        self.assertEqual(self.connector.summary['commit_sha256'], digest('4d'*32))
        forged = dict(self.selection, commit_sha='5e'*20)
        (self.root/'selection.json').write_text(json.dumps(forged))
        with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_same_test_at_other_commit_or_report_is_a_distinct_record(self):
        first = {r.native_id_sha256 for r in self.connector.read(self.source).records}
        self.declare(self.selection['reports'], sha='6f'*20)
        second = {r.native_id_sha256 for r in self.connector.read(self.source).records}
        self.assertFalse(first & second)

    def test_duplicate_case_names_are_kept_as_flagged_occurrences(self):
        self.junit(b'<testsuite name="s"><testcase classname="c" name="t"/><testcase classname="c" name="t"><failure/></testcase></testsuite>')
        snapshot = self.connector.read(self.source)
        self.assertEqual([(r.occurrence, r.status) for r in self.connector.results], [(0, 'passed'), (1, 'failed')])
        self.assertIn('duplicate_case_identity', self.connector.summary['warnings'])
        self.assertEqual(len({r.native_id_sha256 for r in snapshot.records}), 3)

    def test_conflicting_and_suite_level_statuses_are_not_attributed(self):
        self.junit(b'<testsuites><testsuite name="s"><error message="setup"/><testcase name="t"><failure/><skipped/></testcase></testsuite></testsuites>')
        self.connector.read(self.source)
        result, = self.connector.results
        self.assertEqual((result.status, result.reported_outcome), ('unknown', 'conflicting'))
        self.assertTrue({'conflicting_case_status', 'suite_level_status_unattributed'} <= set(self.connector.summary['warnings']))

    def test_doctype_entities_and_hidden_encodings_are_refused_before_parsing(self):
        laughs = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;">]>'
                  b'<testsuite name="s"><testcase name="&lol2;"/></testsuite>')
        external = b'<!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuite><testcase name="&x;"/></testsuite>'
        hostile = (laughs, external, b'<!doctype testsuite><testsuite/>', b'<testsuite><!ELEMENT x ANY></testsuite>',
            '<?xml version="1.0" encoding="UTF-16"?><testsuite/>'.encode('utf-16'),
            b'<?xml version="1.0" encoding="cp037"?><testsuite/>', b'<testsuite>\0</testsuite>', b'<testsuite>\xff</testsuite>')
        for raw in hostile:
            with self.subTest(raw=raw[:40]):
                self.junit(raw)
                with patch.object(test_results.ElementTree, 'XMLPullParser', side_effect=AssertionError('parser ran')):
                    with self.assertRaises(HostileReport): self.connector.read(self.source)

    def test_undefined_entities_and_malformed_xml_are_rejected(self):
        for raw in (b'<testsuite><testcase name="&lol;"/></testsuite>', b'<testsuite><testcase name="t">', b'',
                    b'<html/>', b'<testsuites><testcase name="t"/></testsuites>', b'<testsuite><testcase/></testsuite>'):
            with self.subTest(raw=raw):
                self.junit(raw)
                with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_xml_bounds_abort_early(self):
        cases = [
            (b'<testsuite>'+b'<testcase name="t"/>'*50+b'</testsuite>', dict(max_elements=10)),
            (b'<testsuite>'+b'<testsuite>'*40+b'</testsuite>'*40+b'</testsuite>', {}),
            (b'<testsuite name="'+b'a'*5000+b'"/>', {}),
            (b'<testsuite '+b' '.join(b'a%d="1"' % i for i in range(70))+b'/>', {}),
            (b'<testsuite>'+b'<testcase name="t"/>'*20+b'</testsuite>', dict(max_cases=5)),
            (b'<testsuite>'+b' '*2048+b'</testsuite>', dict(max_report_bytes=1024))]
        for raw, bound in cases:
            with self.subTest(bound=bound, size=len(raw)):
                (self.root/'reports/junit.xml').write_bytes(raw)
                self.declare([dict(path='reports/junit.xml', format='junit-xml')], **({'limits': bound} if bound else {}))
                with self.assertRaises(ValueError): self.connector.read(self.source)
        feeds = []
        actual = test_results.ElementTree.XMLPullParser
        class Counting(actual):
            def feed(self, data): feeds.append(len(data)); return super().feed(data)
        (self.root/'reports/junit.xml').write_bytes(b'<testsuite>'+b'<testcase name="t"/>'*20000+b'</testsuite>')
        self.declare([dict(path='reports/junit.xml', format='junit-xml')], limits=dict(max_elements=100))
        with patch.object(test_results.ElementTree, 'XMLPullParser', Counting):
            with self.assertRaises(HostileReport): self.connector.read(self.source)
        self.assertEqual(len(feeds), 1)

    def test_oversized_start_tags_are_refused_before_expat_builds_them(self):
        for raw, bound in ((b'<testsuite '+b' '.join(b'a%d="1"' % i for i in range(65))+b'/>', None),
                           (b'<testsuite a="1" b="2" c="3"/>', 2), (b'<testsuite><!-- open', None),
                           (b'<testsuite><![CDATA[ open', None), (b'<testsuite a="unterminated/>', None)):
            with self.subTest(raw=raw[:30]):
                (self.root/'reports/junit.xml').write_bytes(raw)
                self.declare([dict(path='reports/junit.xml', format='junit-xml')],
                    **({'limits': dict(max_attributes=bound)} if bound else {}))
                with patch.object(test_results.ElementTree, 'XMLPullParser', side_effect=AssertionError('parser ran')):
                    with self.assertRaises(HostileReport): self.connector.read(self.source)
        self.junit(b'<?xml version="1.0"?><testsuite name="s"><testcase name="t" message=\'a > b\'>'
                   b'<system-out><![CDATA[<Foo object at 0x1> </x>]]></system-out><!-- <y z --></testcase><?pi <a ?></testsuite>')
        self.connector.read(self.source)
        self.assertEqual(self.statuses('junit-xml'), {'t': ('passed', 'no_status_element', None)})

    def test_status_attributes_and_contradictory_phases_never_pass(self):
        self.junit(b'<testsuite name="s"><testcase name="a" status="failed"/><testcase name="b" skipped="true"/>'
                   b'<testcase name="c" file="x.py" line="3"/></testsuite>')
        self.connector.read(self.source)
        self.assertEqual(self.statuses('junit-xml'), {'a': ('unknown', 'status_attribute', None),
            'b': ('unknown', 'status_attribute', None), 'c': ('passed', 'no_status_element', None)})
        self.assertIn('status_attribute_unrecognized', self.connector.summary['warnings'])
        (self.root/'reports/pytest.json').write_text(json.dumps(dict(tests=[
            dict(nodeid='t::a', outcome='passed', teardown=dict(outcome='failed')),
            dict(nodeid='t::b', outcome='passed', setup=dict(outcome='passed'), call=dict(outcome='passed'))])))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        self.connector.read(self.source)
        self.assertEqual({k: v[:2] for k, v in self.statuses('pytest-json').items()},
            {'t::a': ('unknown', 'conflicting'), 't::b': ('passed', 'passed')})

    def test_pytest_json_containers_and_depth_are_bounded_before_decoding(self):
        for body, bound in (('{"tests": ['+','.join(['{}']*20)+']}', dict(max_json_containers=10)),
                            # Deeper than the default bound yet shallow enough that json.loads would decode it.
                            ('{"tests": [], "x": '+'['*100+']'*100+'}', None),
                            ('{"tests": [], "x": '+'['*10+']'*10+'}', dict(max_json_depth=5)),
                            ('{"tests": [], "s": "'+'['*200+'\\"'+'"}', dict(max_json_depth=2))):
            with self.subTest(body=body[:40]):
                (self.root/'reports/pytest.json').write_text(body)
                self.declare([dict(path='reports/pytest.json', format='pytest-json')], **({'limits': bound} if bound else {}))
                with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
                    if bound == dict(max_json_depth=2):
                        # Brackets inside strings are not structure; screening passes and decoding is reached.
                        with self.assertRaisesRegex(AssertionError, 'decoded'): self.connector.read(self.source)
                    else:
                        with self.assertRaises(HostileReport): self.connector.read(self.source)

    def test_pytest_json_unterminated_string_is_refused_in_linear_time(self):
        import time
        for raw in (b'"'+b'\\"'*(512*1024), b'{"tests": [], "x": "'+b'\\"'*(512*1024)):
            with self.subTest(prefix=raw[:12]):
                started = time.monotonic()
                with self.assertRaisesRegex(HostileReport, 'unterminated'):
                    test_results.screen_json(raw, test_results.limits(None))
                # A rescan from every quote took nearly a minute at 80 KB; a single pass over 1 MiB takes milliseconds.
                self.assertLess(time.monotonic()-started, 2)
        (self.root/'reports/pytest.json').write_bytes(b'{"tests": [], "x": "'+b'\\"'*(512*1024))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')])
        with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
            with self.assertRaises(HostileReport): self.connector.read(self.source)

    def test_pytest_json_scalar_values_are_bounded_before_decoding(self):
        # Two values in the test entry plus ten floats; the entry's object closes early, so decoding would reach the hook.
        body = '{"tests": [{"nodeid": "a", "outcome": "passed"}], "x": ['+', '.join(['1.5']*10)+']}'
        (self.root/'reports/pytest.json').write_text(body)
        for bound, outcome in ((11, HostileReport), (12, AssertionError)):
            with self.subTest(bound=bound):
                self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_json_values=bound))
                with patch.object(test_results, 'unique_object', side_effect=AssertionError('decoded')):
                    with self.assertRaises(outcome): self.connector.read(self.source)
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_json_values=10**9))
        with self.assertRaisesRegex(ValueError, 'only lower'): self.connector.read(self.source)
        # The ceilings admit 100k tests of about 25 values, and 100k Claude-Mem rows of about 40 fields.
        self.assertGreaterEqual(test_results.LIMITS['max_json_values'], test_results.LIMITS['max_cases']*25)
        self.assertGreaterEqual(claude_mem.LIMITS['max_json_values'], claude_mem.LIMITS['max_rows']*40)

    def test_scalar_count_skips_keys_containers_and_string_contents(self):
        def admitted(raw, bound):
            try: hardened.load_json(raw, dict(max_json_depth=8, max_json_containers=64, max_json_values=bound), 'probe')
            except HostileReport: return False
            return True
        for raw, values in ((b'[]', 0), (b'{}', 0), (b'[1]', 1), (b'[[], {}, [[{}]]]', 0), (b'{"a": [], "b": {}}', 0),
                (b'{"a" : 1, "b": "x,]"}', 2), (b'["a,b", "[{", "}]:,", 1.5e3, true, false, null]', 7),
                (b'"\\"],[\\\\"', 1), (b'{"k": {"k2": [1, 2, {"z": "q\\"],"}]}}', 3), (b' -0.5 ', 1)):
            with self.subTest(raw=raw):
                self.assertTrue(admitted(raw, values))
                if values: self.assertFalse(admitted(raw, values-1))
        # An unterminated string is refused in one pass instead of rescanned from every later quote.
        bounds = dict(max_json_depth=8, max_json_containers=64, max_json_values=10)
        with patch.object(hardened, 'unique_object', side_effect=AssertionError('decoded')):
            started = time.perf_counter()
            with self.assertRaisesRegex(HostileReport, 'probe string is unterminated'):
                hardened.load_json(b'{"tests": ["'+b'\\"'*40000, bounds, 'probe')
            self.assertLess(time.perf_counter()-started, 2)

    def test_scalar_heavy_json_peak_stays_a_bounded_multiple_of_input(self):
        import tracemalloc
        bound = 100000
        bounds = dict(max_json_depth=8, max_json_containers=8, max_json_values=bound)
        def peak(raw):
            tracemalloc.start()
            try:
                try: hardened.load_json(raw, bounds, 'probe')
                except HostileReport: refused = True
                else: refused = False
                return refused, tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
        for item in (b'"ab"', b'1.5'):
            with self.subTest(item=item):
                # Measured about 11.3x for two-character strings and 9.2x for floats; the stated bound is 12x.
                raw = b'['+b','.join([item]*bound)+b']'
                refused, used = peak(raw)
                self.assertFalse(refused)
                self.assertLess(used, 12*len(raw))
                # Twice the bound is refused by the screen, which holds one match at a time.
                raw = b'['+b','.join([item]*2*bound)+b']'
                refused, used = peak(raw)
                self.assertTrue(refused)
                self.assertLess(used, len(raw)//100)

    def test_finished_elements_are_released_while_parsing(self):
        import tracemalloc
        raw = b'<testsuite name="s">'+b'<properties/>'*300000+b'<testcase name="t"/>'*10+b'</testsuite>'
        tracemalloc.start()
        try:
            cases = sum(1 for _ in test_results.parse_junit(raw, test_results.limits(None), set()))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        # Retaining the 300k finished elements peaked near 60 MiB; released elements stay a few MiB.
        self.assertEqual(cases, 10)
        self.assertLess(peak, 16*1024*1024)

    def test_limits_only_lower_hard_ceilings(self):
        for bound in (dict(max_cases=0), dict(max_cases=10**9), dict(unknown=1), dict(max_depth=True), []):
            with self.subTest(bound=bound):
                self.declare(self.selection['reports'], limits=bound)
                with self.assertRaises(ValueError): self.connector.read(self.source)

    def test_pytest_json_rejects_duplicates_constants_depth_and_schema(self):
        bodies = ('{"tests": [], "tests": []}', '{"tests": [{"nodeid": "a", "outcome": "passed", "call": {"duration": NaN}}]}',
            '{"tests": [], "x": '+'['*100+']'*100+'}', '{"tests": {}}', '[]', '{"tests": [{"outcome": "passed"}]}',
            '{"tests": [{"nodeid": "a", "outcome": "passed", "call": []}]}', '{"tests": [], "collectors": {}}',
            '{"tests": [], "x": '+'['*100000+']'*100000+'}', '﻿{"tests": []}')
        for body in bodies:
            with self.subTest(body=body[:50]):
                (self.root/'reports/pytest.json').write_text(body)
                self.declare([dict(path='reports/pytest.json', format='pytest-json')])
                with self.assertRaises(ValueError): self.connector.read(self.source)
        (self.root/'reports/pytest.json').write_text(json.dumps(dict(tests=[dict(nodeid=str(i), outcome='passed') for i in range(9)])))
        self.declare([dict(path='reports/pytest.json', format='pytest-json')], limits=dict(max_cases=8))
        with self.assertRaises(HostileReport): self.connector.read(self.source)

    def test_selection_paths_formats_and_changes_are_checked(self):
        for reports in ([], [dict(path='../x.xml', format='junit-xml')], [dict(path='reports/junit.xml', format='tap')],
                        [dict(path='reports/junit.xml', format='junit-xml')]*2, [dict(path='/etc/passwd', format='junit-xml')],
                        [dict(path='reports/junit.xml', format='junit-xml', extra=1)]):
            with self.subTest(reports=reports):
                self.declare(reports)
                with self.assertRaises(ValueError): self.connector.read(self.source)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')])
        (self.root/'reports/link.xml').symlink_to(self.root/'reports/junit.xml')
        self.declare([dict(path='reports/link.xml', format='junit-xml')])
        with self.assertRaises(ValueError): self.connector.read(self.source)
        self.declare([dict(path='reports/junit.xml', format='junit-xml')])
        actual = test_results.regular_sha256
        with patch.object(test_results, 'regular_sha256', lambda path, limit: '0'*64):
            with self.assertRaises(ValueError): self.connector.read(self.source)
        self.assertIs(actual, test_results.regular_sha256)

    def test_cli_preview_import_and_reimport_keep_hashes_only(self):
        code, preview = self.invoke('preview')
        self.assertEqual(code, 0)
        summary = json.loads(preview)['test_results_summary']
        self.assertEqual((summary['cases'], summary['profile']), (11, 'ci-test-results-v1'))
        self.assertFalse((self.root/'private').exists())
        code, receipt = self.invoke('import')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(receipt)['imported'], 13)
        raw = (self.root/'private/cache.json').read_text()
        for secret in ('SYNTHETIC_SECRET', 'test_fail', 'pkg.mod', 'synthetic-suite', 'tests/test_a.py', SHA, 'reports/junit.xml'):
            self.assertNotIn(secret, raw)
        self.assertEqual(json.loads(self.invoke('import')[1])['skipped'], 13)
        before = (self.root/'private/cache.json').read_bytes()
        (self.root/'reports/junit.xml').write_bytes(b'<!DOCTYPE x><testsuite/>')
        self.assertEqual(self.invoke('import')[0], 2)
        self.assertEqual((self.root/'private/cache.json').read_bytes(), before)

    def test_disabled_or_mismatched_sources_are_blocked_before_reports_are_read(self):
        opened = []
        actual = test_results.read_regular
        def read(path, limit): opened.append(path); return actual(path, limit)
        with patch.object(test_results, 'read_regular', side_effect=read):
            output = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = main(['sources', 'preview', '--manifest', str(self.manifest), '--project-sha256', digest('other'),
                    '--scope-sha256', self.source.scope_sha256])
        self.assertEqual(code, 2)
        self.assertEqual(opened, [])


if __name__ == '__main__':
    unittest.main()
