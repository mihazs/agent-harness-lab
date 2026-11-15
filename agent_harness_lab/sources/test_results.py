"""Offline JUnit XML, pytest JSON and TAP results bound to a declared commit; CI evidence, never task grades."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from functools import lru_cache
import io
import math
import re
import xml.etree.ElementTree as ElementTree
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative, unique_object
from . import hardened
# Re-exported: the hardened JSON loader started here and other modules still import it from this one.
from .hardened import JSON_STRUCTURE, HostileReport, load_json, screen_json  # noqa: F401
from .schema import Contract, EvidenceRecord, digest, label
from .snapshot import LocalExport, Snapshot

PROVIDER = 'test-results'
SOURCE_VERSION = 'v1'
PROFILE = 'ci-test-results-v1'
FORMATS = ('junit-xml', 'pytest-json', 'tap')
STATUSES = ('passed', 'failed', 'error', 'skipped', 'unknown')
FULL_SHA = re.compile('(?:[0-9a-f]{40}|[0-9a-f]{64})')
# Hard ceilings; a selection may only lower them.
LIMITS = dict(max_reports=64, max_report_bytes=16*1024*1024, max_cases=100000, max_elements=500000,
              max_depth=32, max_attributes=64, max_attribute_length=4096, max_line_length=4096, max_json_depth=64,
              max_json_containers=1000000, max_json_values=2500000)
CHUNK = 64*1024
JUNIT_STATUS = {'failure': 'failed', 'error': 'error', 'skipped': 'skipped'}
JUNIT_IGNORED = ('system-out', 'system-err', 'properties')
# Status-like testcase attributes are not part of JUnit; their presence makes an element-less case unknown, not passed.
STATUS_ATTRIBUTES = frozenset(('status', 'result', 'outcome', 'passed', 'failed', 'failure', 'failures', 'error', 'errors',
    'skipped', 'skip', 'disabled'))
PYTEST_STATUS = {'passed': 'passed', 'failed': 'failed', 'error': 'error', 'skipped': 'skipped'}
# Expected-failure outcomes carry inverted semantics; they stay unknown, labelled as reported.
PYTEST_KNOWN = ('passed', 'failed', 'error', 'skipped', 'xfailed', 'xpassed', 'rerun')
DECLARATION = re.compile(rb'\A(?:\xef\xbb\xbf)?<\?xml[ \t\r\n][^?>]*?encoding[ \t\r\n]*=[ \t\r\n]*["\']([^"\']*)["\']')
# TAP 13/14 test points are matched at column zero only; indented lines are YAML diagnostics or subtests and never cases.
TAP_VERSIONS = (13, 14)
TAP_VERSION = re.compile(r'TAP version ([0-9]+)')
TAP_PLAN = re.compile(r'([0-9]+)\.\.([0-9]+)[ \t]*(?:#.*)?')
TAP_POINT = re.compile(r'(not )?ok(?=[ \t]|$)(?:[ \t]+([0-9]+)(?=[ \t]|$))?[ \t]*(?:-(?=[ \t]|$)[ \t]*)?(.*)')
TAP_SUBTEST_POINT = re.compile(r'[ \t]+(?:not )?ok(?=[ \t]|$)')
TAP_UNESCAPED_HASH = re.compile(r'(?<!\\)#')
TAP_DIRECTIVE = re.compile(r'[ \t]*(skip|todo)(\w*)', re.I)
# Any markup declaration (DOCTYPE, ENTITY, ELEMENT, ATTLIST, NOTATION, conditional sections) is refused.
MARKUP_DECLARATION = re.compile(rb'<!(?!--|\[CDATA\[)')


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


def limits(value, ceilings=LIMITS):
    """Selection limits may only lower this source's hard ceilings."""
    return hardened.limits(value, ceilings)


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


def screen_xml(raw, bounds):
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
    # Expat materializes a whole start tag and its attribute dict before any event, so the count is bounded here.
    if any(token[0] == b'<' for token in tags(bounds['max_attributes']).finditer(raw)):
        raise HostileReport('JUnit XML start tag is malformed or exceeds the attribute bound')


@lru_cache(maxsize=8)
def tags(attributes):
    """Comments, CDATA, processing instructions, end tags and bounded start tags; a bare '<' is anything else."""
    return re.compile(rb'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|</|<[^\s/>!?<=]+'
        rb'(?:\s+[^\s=/>]+\s*=\s*(?:"[^"]*"|\'[^\']*\')){0,%d}\s*/?>|<' % attributes, re.S)


def parse_junit(raw, bounds, warnings):
    """Yield (suite identity, case name, status, reported outcome, duration) with early abort on bounds."""
    screen_xml(raw, bounds)
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
            if not found and STATUS_ATTRIBUTES & set(element.attrib):
                status, reported = 'unknown', 'status_attribute'; warnings.add('status_attribute_unrecognized')
            elif not found: status, reported = 'passed', 'no_status_element'
            elif 'unrecognized' in found: status, reported = 'unknown', 'unrecognized'; warnings.add('unrecognized_case_element')
            elif len(found) > 1: status, reported = 'unknown', 'conflicting'; warnings.add('conflicting_case_status')
            else: reported = found.pop(); status = JUNIT_STATUS[reported]
            suites = [s.get('name') for s in stack if s.tag == 'testsuite']
            yield (['junit-xml', suites, element.get('classname')], name, status, reported, duration(element.get('time'), warnings))
        elif parent is not None and parent.tag in ('testsuites', 'testsuite'):
            # Suite-level failures and errors name no case; they are never attributed to one.
            if element.tag in JUNIT_STATUS: warnings.add('suite_level_status_unattributed')
            elif element.tag not in ('testsuite', *JUNIT_IGNORED): warnings.add('unrecognized_suite_element')
    def drain():
        for event, element in parser.read_events():
            if event == 'start': stack.append(element); admit(element); continue
            stack.pop(); yield from finish(element)
            if not stack: continue
            # Finished elements are emptied; only a testcase keeps its (tag-only) children until it is finished.
            element.clear()
            if stack[-1].tag != 'testcase': stack[-1].remove(element)
    try:
        for offset in range(0, len(raw), CHUNK):
            parser.feed(raw[offset:offset+CHUNK]); yield from drain()
        parser.close(); yield from drain()
    except ElementTree.ParseError:
        raise ValueError('malformed JUnit XML') from None
    if elements == 0: raise ValueError('empty JUnit XML')


def parse_pytest(raw, bounds, warnings):
    value = load_json(raw, bounds, 'pytest JSON', unique_object)
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
        if total is not None and not math.isfinite(total):
            # Finite phases can still overflow when summed; the case keeps no duration.
            warnings.add('invalid_duration'); total = None
        outcome = test['outcome']
        if outcome not in PYTEST_KNOWN: warnings.add('unrecognized_outcome')
        status, reported = PYTEST_STATUS.get(outcome, 'unknown'), outcome if outcome in PYTEST_KNOWN else 'unrecognized'
        if status == 'passed' and any(phase.get('outcome', 'passed') != 'passed' for phase in phases):
            # A passed test with a failed, errored or skipped phase is contradictory, never a pass.
            status, reported = 'unknown', 'conflicting'; warnings.add('conflicting_case_status')
        nodeid = test['nodeid']
        suite = nodeid.rsplit('::', 1)[0] if '::' in nodeid else None
        yield (['pytest-json', suite], nodeid, status, reported, total)


def parse_tap(raw, bounds, warnings):
    """Yield (suite identity, case name, status, reported outcome, None) for each top-level TAP 13/14 test point.

    The stream is read one bounded line at a time and the read ends at the first bound exceeded or a bail out. A plan
    that disagrees with the test points is flagged, never filled in, so missing tests stay unknown instead of failing.
    """
    if b'\0' in raw: raise HostileReport('only UTF-8 TAP is supported')
    plan, points, bailed = None, 0, False
    for position, chunk in enumerate(io.BytesIO(raw)):
        chunk = chunk.rstrip(b'\r\n')
        if len(chunk) > bounds['max_line_length']: raise HostileReport('TAP line length bound exceeded')
        try: line = chunk.decode('utf-8')
        except UnicodeDecodeError: raise HostileReport('TAP is not valid UTF-8') from None
        version = TAP_VERSION.fullmatch(line) if position == 0 else None
        if version:
            if int(version[1]) not in TAP_VERSIONS: raise ValueError('only TAP versions 13 and 14 are supported')
        elif not line.strip() or line.startswith(('#', 'pragma ')): continue
        elif line[0] in ' \t':
            if TAP_SUBTEST_POINT.match(line): warnings.add('tap_subtest_lines_not_imported')
        elif line[:9].lower() == 'bail out!': warnings.add('tap_bail_out_reported'); bailed = True; break
        elif planned := TAP_PLAN.fullmatch(line):
            first, last = int(planned[1]), int(planned[2])
            if plan is not None: raise ValueError('duplicate TAP plan')
            if first != 1: raise ValueError('a TAP plan must start at 1')
            if last > bounds['max_cases']: raise HostileReport('TAP plan exceeds the case bound')
            if last == 0: warnings.add('tap_plan_skipped')
            plan = last
        elif point := TAP_POINT.match(line):
            points += 1
            if points > bounds['max_cases']: raise HostileReport('TAP case bound exceeded')
            if point[2] is not None and int(point[2]) != points: warnings.add('tap_test_number_out_of_sequence')
            description, hash_at = point[3], TAP_UNESCAPED_HASH.search(point[3])
            directive = TAP_DIRECTIVE.match(description, hash_at.end()) if hash_at else None
            if hash_at: description = description[:hash_at.start()]
            if directive is None: status, reported = ('failed', 'not_ok') if point[1] else ('passed', 'ok')
            elif directive[2]: status, reported = 'unknown', 'unrecognized_directive'; warnings.add('unrecognized_directive')
            # TODO inverts the meaning of the result, and SKIP on a failing line contradicts itself: neither is a verdict.
            elif directive[1].lower() == 'todo': status, reported = 'unknown', 'todo'
            elif point[1]: status, reported = 'unknown', 'conflicting'; warnings.add('conflicting_case_status')
            else: status, reported = 'skipped', 'skip'
            yield ['tap'], description.strip() or '#%d' % points, status, reported, None
        else: warnings.add('tap_unrecognized_line')
    if plan is None and not points and not bailed: raise ValueError('TAP report has neither a plan nor a test point')
    if plan is None: warnings.add('tap_plan_absent')
    elif plan != points: warnings.add('tap_plan_mismatch')


PARSERS = {'junit-xml': parse_junit, 'pytest-json': parse_pytest, 'tap': parse_tap}


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
        records, results, warnings, cases = [], [], set(), 0
        declared = {}
        def record(kind, native, revision, outcome=None):
            return EvidenceRecord(source.source_key, native, revision, revision, 'development_artifact', kind, now,
                provenance='native_field', capture_completeness='partial', schema_version=2 if outcome is not None else 1,
                outcome=outcome)
        def consume(position, raw, report_sha256):
            nonlocal cases
            report = reports[position]
            path_sha256 = digest(report['path'])
            parse = PARSERS[report['format']]
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
                observed = dict(kind='observed', value={'passed': 'success', 'failed': 'failure'}.get(status, 'unknown'),
                    provenance_tier='test_result', proof=value)
                records.append(record('test_case_result', digest(('test_case_result', sha, path_sha256, case_sha256, occurrence)), digest(value), observed))
            records.append(record('test_report', digest(('test_report', sha, path_sha256)), report_sha256))
        self.capture(source, descriptor, selection, [contained(self.manifest.parent, r['path']) for r in reports],
            bounds['max_report_bytes'], consume, 'test report changed during capture', read_regular, regular_sha256)
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
