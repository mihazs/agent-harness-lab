"""Offline JUnit XML and pytest JSON results bound to a declared commit; CI evidence, never task grades."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ElementTree
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_equal, relative, unique_object
from .schema import Contract, EvidenceRecord, digest, label
from .snapshot import LocalExport, Snapshot

PROVIDER = 'test-results'
SOURCE_VERSION = 'v1'
PROFILE = 'ci-test-results-v1'
FORMATS = ('junit-xml', 'pytest-json')
STATUSES = ('passed', 'failed', 'error', 'skipped', 'unknown')
FULL_SHA = re.compile('(?:[0-9a-f]{40}|[0-9a-f]{64})')
# Hard ceilings; a selection may only lower them.
LIMITS = dict(max_reports=64, max_report_bytes=16*1024*1024, max_cases=100000, max_elements=500000,
              max_depth=32, max_attributes=64, max_attribute_length=4096, max_json_depth=64)
CHUNK = 64*1024
JUNIT_STATUS = {'failure': 'failed', 'error': 'error', 'skipped': 'skipped'}
JUNIT_IGNORED = ('system-out', 'system-err', 'properties')
PYTEST_STATUS = {'passed': 'passed', 'failed': 'failed', 'error': 'error', 'skipped': 'skipped'}
# Expected-failure outcomes carry inverted semantics; they stay unknown, labelled as reported.
PYTEST_KNOWN = ('passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed', 'rerun')
DECLARATION = re.compile(rb'\A(?:\xef\xbb\xbf)?<\?xml[ \t\r\n][^?>]*?encoding[ \t\r\n]*=[ \t\r\n]*["\']([^"\']*)["\']')
# Any markup declaration (DOCTYPE, ENTITY, ELEMENT, ATTLIST, NOTATION, conditional sections) is refused.
MARKUP_DECLARATION = re.compile(rb'<!(?!--|\[CDATA\[)')


class HostileReport(ValueError):
    """A report was refused before or during bounded parsing; nothing from it is imported."""


@dataclass(frozen=True)
class TestResult(Contract):
    __test__ = False
    report_path_sha256: str
    report_sha256: str
    format: str
    suite_sha256: str
    case_sha256: str
    occurrence: int
    status: str
    reported_outcome: str
    duration_seconds: float | None
    commit_sha256: str
    commit_binding: str = 'caller_declared'
    commit_verified: bool = False
    outcome_scope: str = 'declared_commit_ci_evidence'
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('report_path_sha256', 'report_sha256', 'suite_sha256', 'case_sha256', 'commit_sha256'),
            {'format': FORMATS, 'status': STATUSES, 'commit_binding': ('caller_declared',),
             'outcome_scope': ('declared_commit_ci_evidence',)})
        label(self.reported_outcome)
        if type(self.occurrence) is not int or self.occurrence < 0 or self.commit_verified is not False:
            raise ValueError('invalid test result identity or unverified commit binding')
        if self.duration_seconds is not None and (type(self.duration_seconds) is not float
                or not math.isfinite(self.duration_seconds) or self.duration_seconds < 0):
            raise ValueError('invalid test duration')


def commit(value):
    if not isinstance(value, str) or FULL_SHA.fullmatch(value) is None:
        raise ValueError('full lowercase 40- or 64-hex commit identity required')
    return value


def limits(value):
    if value is None: return dict(LIMITS)
    if not isinstance(value, dict) or set(value) - set(LIMITS): raise ValueError('unsupported test result limits')
    result = dict(LIMITS)
    for key, bound in value.items():
        if type(bound) is not int or not 1 <= bound <= LIMITS[key]: raise ValueError('test result limits may only lower ceilings')
        result[key] = bound
    return result


def duration(value, warnings):
    if value is None: return None
    try:
        number = float(value) if isinstance(value, str) and re.fullmatch(r'[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?', value) else (
            float(value) if type(value) in (int, float) else None)
    except (OverflowError, ValueError):
        number = None
    if number is None or not math.isfinite(number) or number < 0:
        warnings.add('invalid_duration'); return None
    return number


def screen_xml(raw):
    """Refuse encodings and declarations the byte screen cannot see through, before any parser runs."""
    if b'\0' in raw or raw.startswith((b'\xfe\xff', b'\xff\xfe', b'\x00\x00\xfe\xff', b'\xff\xfe\x00\x00')):
        raise HostileReport('only UTF-8 JUnit XML is supported')
    try: raw.decode('utf-8')
    except UnicodeDecodeError: raise HostileReport('JUnit XML is not valid UTF-8') from None
    declared = DECLARATION.match(raw)
    if declared and declared[1].lower() not in (b'utf-8', b'us-ascii'):
        raise HostileReport('only UTF-8 JUnit XML is supported')
    if MARKUP_DECLARATION.search(raw):
        raise HostileReport('DOCTYPE, ENTITY and other markup declarations are refused')


def parse_junit(raw, bounds, warnings):
    """Yield (suite identity, case name, status, reported outcome, duration) with early abort on bounds."""
    screen_xml(raw)
    parser = ElementTree.XMLPullParser(events=('start', 'end'))
    stack, elements = [], 0
    def admit(element):
        nonlocal elements
        elements += 1
        if elements > bounds['max_elements']: raise HostileReport('JUnit XML element bound exceeded')
        if len(stack) > bounds['max_depth']: raise HostileReport('JUnit XML depth bound exceeded')
        if len(element.attrib) > bounds['max_attributes']: raise HostileReport('JUnit XML attribute count bound exceeded')
        for key, value in element.attrib.items():
            if len(key) > bounds['max_attribute_length'] or len(value) > bounds['max_attribute_length']:
                raise HostileReport('JUnit XML attribute length bound exceeded')
        if len(stack) == 1 and element.tag not in ('testsuites', 'testsuite'):
            raise ValueError('unsupported JUnit XML root element')
    def finish(element):
        parent = stack[-1] if stack else None
        if element.tag == 'testcase':
            if parent is None or parent.tag != 'testsuite': raise ValueError('JUnit testcase outside a testsuite')
            name = element.get('name')
            if not name: raise ValueError('JUnit testcase name is required')
            found = set()
            for child in element:
                if child.tag in JUNIT_STATUS: found.add(child.tag)
                elif child.tag not in JUNIT_IGNORED: found.add('unrecognized')
            if not found: status, reported = 'passed', 'no_status_element'
            elif 'unrecognized' in found: status, reported = 'unknown', 'unrecognized'; warnings.add('unrecognized_case_element')
            elif len(found) > 1: status, reported = 'unknown', 'conflicting'; warnings.add('conflicting_case_status')
            else: reported = found.pop(); status = JUNIT_STATUS[reported]
            suites = [s.get('name') for s in stack if s.tag == 'testsuite']
            yield (['junit-xml', suites, element.get('classname')], name, status, reported, duration(element.get('time'), warnings))
        elif parent is not None and parent.tag in ('testsuites', 'testsuite'):
            # Suite-level failures and errors name no case; they are never attributed to one.
            if element.tag in JUNIT_STATUS: warnings.add('suite_level_status_unattributed')
            elif element.tag not in ('testsuite', *JUNIT_IGNORED): warnings.add('unrecognized_suite_element')
        if parent is not None and element.tag in ('testcase', 'testsuite'): element.clear()
    def drain():
        for event, element in parser.read_events():
            if event == 'start': stack.append(element); admit(element)
            else: stack.pop(); yield from finish(element)
    try:
        for offset in range(0, len(raw), CHUNK):
            parser.feed(raw[offset:offset+CHUNK]); yield from drain()
        parser.close(); yield from drain()
    except ElementTree.ParseError:
        raise ValueError('malformed JUnit XML') from None
    if elements == 0: raise ValueError('empty JUnit XML')


def _constant(value):
    raise ValueError('non-finite JSON numbers are unsupported')


def parse_pytest(raw, bounds, warnings):
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_object, parse_constant=_constant)
    except RecursionError:
        raise HostileReport('pytest JSON depth bound exceeded') from None
    except UnicodeDecodeError:
        raise ValueError('pytest JSON is not valid UTF-8') from None
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > bounds['max_json_depth']: raise HostileReport('pytest JSON depth bound exceeded')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, depth+1) for child in children if isinstance(child, (dict, list)))
    if not isinstance(value, dict) or not isinstance(value.get('tests'), list):
        raise ValueError('pytest JSON report requires a tests list')
    if len(value['tests']) > bounds['max_cases']: raise HostileReport('pytest JSON case bound exceeded')
    collectors = value.get('collectors')
    if collectors is not None:
        if not isinstance(collectors, list) or not all(isinstance(c, dict) for c in collectors):
            raise ValueError('invalid pytest JSON collectors')
        if any(c.get('outcome') not in ('passed', None) for c in collectors): warnings.add('collection_failures_reported')
    for test in value['tests']:
        if not isinstance(test, dict) or not isinstance(test.get('nodeid'), str) or not test['nodeid'] or not isinstance(test.get('outcome'), str):
            raise ValueError('pytest JSON tests require nodeid and outcome text')
        phases = [test.get(name) for name in ('setup', 'call', 'teardown') if name in test]
        if not all(isinstance(phase, dict) for phase in phases): raise ValueError('invalid pytest JSON test phase')
        spans = [duration(phase['duration'], warnings) for phase in phases if 'duration' in phase]
        total = None if not spans or None in spans else float(sum(spans))
        outcome = test['outcome']
        if outcome not in PYTEST_KNOWN: warnings.add('unrecognized_outcome')
        nodeid = test['nodeid']
        suite = nodeid.rsplit('::', 1)[0] if '::' in nodeid else None
        yield (['pytest-json', suite], nodeid, PYTEST_STATUS.get(outcome, 'unknown'),
               outcome if outcome in PYTEST_KNOWN else 'unrecognized', total)


class TestResultsExport(LocalExport):
    """Imports only selected local report files; never runs tests, scripts or report-embedded content."""
    __test__ = False

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported test result source version')
        descriptor, selection = self.selection(source)
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != {'schema_version', 'commit_sha', 'reports'}
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1):
            raise ValueError('invalid test result selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('test result selection scope mismatch')
        sha, bounds = commit(selection['commit_sha']), limits(selection.get('limits'))
        reports = selection['reports']
        if not isinstance(reports, list) or not reports or len(reports) > bounds['max_reports']:
            raise ValueError('invalid or unbounded test report selection')
        for report in reports:
            if not isinstance(report, dict) or set(report) != {'path', 'format'} or report['format'] not in FORMATS:
                raise ValueError('invalid test report declaration')
            relative(report['path'])
        if len({r['path'] for r in reports}) != len(reports): raise ValueError('duplicate test report path')
        now = datetime.now(timezone.utc).isoformat()
        commit_sha256 = digest(sha)
        records, results, raws, warnings, cases = [], [], [], set(), 0
        declared = {}
        def record(kind, native, revision):
            return EvidenceRecord(source.source_key, native, revision, revision, 'development_artifact', kind, now,
                provenance='native_field', capture_completeness='partial')
        for report in reports:
            path = contained(self.manifest.parent, report['path'])
            raw = read_regular(path, bounds['max_report_bytes'])
            raws.append((path, raw))
            report_sha256, path_sha256 = hashlib.sha256(raw).hexdigest(), digest(report['path'])
            parse = parse_junit if report['format'] == 'junit-xml' else parse_pytest
            seen = Counter()
            for suite, case, status, reported, seconds in parse(raw, bounds, warnings):
                cases += 1
                if cases > bounds['max_cases']: raise HostileReport('test case bound exceeded')
                suite_sha256, case_sha256 = digest(suite), digest([suite, case])
                occurrence = seen[case_sha256]; seen[case_sha256] += 1
                if occurrence: warnings.add('duplicate_case_identity')
                result = TestResult(path_sha256, report_sha256, report['format'], suite_sha256, case_sha256, occurrence,
                    status, reported, seconds, commit_sha256)
                results.append(result)
                declared[case_sha256] = (suite, case)
                value = asdict(result)
                records.append(record('test_case_result', digest(('test_case_result', sha, path_sha256, case_sha256, occurrence)), digest(value)))
            records.append(record('test_report', digest(('test_report', sha, path_sha256)), report_sha256))
        for path, raw in raws:
            if not regular_equal(path, raw, bounds['max_report_bytes']): raise ValueError('test report changed during capture')
        self.verify_selection(source, descriptor, selection)
        statuses = Counter(r.status for r in results)
        self.results, self.declared_cases = tuple(results), declared
        self.summary = dict(schema_version=1, profile=PROFILE, commit_sha256=commit_sha256, commit_binding='caller_declared',
            commit_verified=False, reports=len(reports), cases=len(results),
            statuses={status: statuses.get(status, 0) for status in STATUSES},
            durations_reported=sum(r.duration_seconds is not None for r in results),
            warnings=sorted(warnings | {'commit_binding_declared', 'reruns_not_independent'}),
            outcome_scope='declared_commit_ci_evidence', task_outcomes_verified=False, independent_trials=None,
            verification_authorized=False, execution_authorized=False, scripts_executed=False,
            publication_allowed=False, network_used=False, capture_completeness='partial')
        return Snapshot(digest(dict(self.summary, results=[asdict(r) for r in results])), tuple(records))
