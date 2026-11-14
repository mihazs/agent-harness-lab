"""Offline SARIF 2.1.0 quality and security findings bound to a declared commit; CI evidence, never a task grade or a test result."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from agent_harness_lab.evaluation.integrity import contained, read_regular, regular_sha256, relative, unique_object
from . import hardened
from .hardened import HostileReport, load_json
from .schema import Contract, EvidenceRecord, digest
from .snapshot import LocalExport, Snapshot
from .test_results import commit

PROVIDER = 'quality-reports'
SOURCE_VERSION = 'v1'
PROFILE = 'ci-quality-reports-v1'
FORMATS = ('sarif',)
# Finding levels are SARIF's own and are never mixed with test statuses; an absent or unknown level stays unknown.
LEVELS = ('error', 'warning', 'note', 'none', 'unknown')
KINDS = ('fail', 'pass', 'open', 'review', 'notApplicable', 'informational')
LIMITS = dict(max_reports=64, max_report_bytes=16*1024*1024, max_findings=100000, max_json_depth=64,
              max_json_containers=1000000, max_json_values=2500000)


@dataclass(frozen=True)
class QualityFinding(Contract):
    report_path_sha256: str
    report_sha256: str
    format: str
    tool_sha256: str
    rule_sha256: str
    finding_sha256: str
    occurrence: int
    level: str
    kind: str
    suppression: str
    commit_sha256: str
    commit_binding: str = 'caller_declared'
    commit_verified: bool = False
    outcome_scope: str = 'declared_commit_quality_evidence'
    schema_version: int = 1

    def __post_init__(self):
        self.validate(('report_path_sha256', 'report_sha256', 'tool_sha256', 'rule_sha256', 'finding_sha256', 'commit_sha256'),
            {'format': FORMATS, 'level': LEVELS, 'kind': KINDS, 'suppression': ('none', 'declared'),
             'commit_binding': ('caller_declared',), 'outcome_scope': ('declared_commit_quality_evidence',)})
        if type(self.occurrence) is not int or self.occurrence < 0 or self.commit_verified is not False:
            raise ValueError('invalid quality finding identity or unverified commit binding')


def _object(value, name):
    if not isinstance(value, dict): raise ValueError('invalid SARIF '+name)
    return value


def _location(result, warnings):
    """The first physical location as (uri, start line); the URI is hashed, never opened or fetched."""
    locations = result.get('locations', [])
    if not isinstance(locations, list): raise ValueError('invalid SARIF locations')
    if not locations: warnings.add('location_absent'); return None
    physical = _object(_object(locations[0], 'location').get('physicalLocation', {}), 'physicalLocation')
    uri = _object(physical.get('artifactLocation', {}), 'artifactLocation').get('uri')
    line = _object(physical.get('region', {}), 'region').get('startLine')
    if uri is not None and not isinstance(uri, str) or line is not None and type(line) is not int:
        raise ValueError('invalid SARIF location')
    if uri is None: warnings.add('location_absent')
    return [uri, line]


def parse_sarif(raw, bounds, warnings):
    """Yield (tool identity, rule id, location, level, kind, suppression) for every result of every run."""
    log = _object(load_json(raw, bounds, 'SARIF', unique_object), 'log')
    if log.get('version') != '2.1.0' or not isinstance(log.get('runs'), list): raise ValueError('only SARIF 2.1.0 logs with runs are supported')
    found = 0
    for run in log['runs']:
        run = _object(run, 'run')
        driver = _object(_object(run.get('tool'), 'tool').get('driver'), 'driver')
        name, version = driver.get('name'), driver.get('version')
        if not isinstance(name, str) or not name or version is not None and not isinstance(version, str):
            raise ValueError('SARIF tool driver requires a name')
        # A tool that failed or says nothing about its own success has not shown that no findings means clean.
        invocations = run.get('invocations', [])
        if not isinstance(invocations, list): raise ValueError('invalid SARIF invocations')
        states = [i.get('executionSuccessful') for i in invocations if isinstance(i, dict)]
        if any(s is False for s in states): warnings.add('tool_execution_failure_reported')
        elif not any(s is True for s in states): warnings.add('tool_execution_success_unstated')
        if 'externalPropertyFileReferences' in run: raise HostileReport('SARIF external property file references are refused')
        results = run.get('results', [])
        if not isinstance(results, list): raise ValueError('invalid SARIF results')
        found += len(results)
        if found > bounds['max_findings']: raise HostileReport('SARIF finding bound exceeded')
        for result in results:
            result = _object(result, 'result')
            rule = result.get('ruleId')
            if rule is None: rule = _object(result.get('rule', {}), 'rule').get('id')
            if rule is not None and not isinstance(rule, str): raise ValueError('invalid SARIF rule id')
            if rule is None: warnings.add('rule_id_absent')
            level, kind, suppressions = result.get('level'), result.get('kind', 'fail'), result.get('suppressions', [])
            if kind not in KINDS: raise ValueError('unsupported SARIF result kind')
            if level is None or level not in LEVELS[:4]:
                warnings.add('level_not_stated' if level is None else 'unrecognized_level')
                level = 'unknown'
            elif kind != 'fail' and level != 'none':
                # SARIF allows a level only on a failing result; a contradiction is neither a finding nor a pass.
                warnings.add('conflicting_result_kind'); level = 'unknown'
            if not isinstance(suppressions, list): raise ValueError('invalid SARIF suppressions')
            if suppressions: warnings.add('suppression_status_not_interpreted')
            yield [name, version], rule, _location(result, warnings), level, kind, 'declared' if suppressions else 'none'


class QualityReportsExport(LocalExport):
    """Imports only selected local SARIF files; never runs analyzers, follows references or opens report URIs."""

    def read(self, source):
        if (source.kind, source.provider, source.version) != ('local_file', PROVIDER, SOURCE_VERSION):
            raise ValueError('unsupported quality report source version')
        descriptor, selection = self.selection(source)
        if (not isinstance(selection, dict) or set(selection) - {'limits'} != {'schema_version', 'commit_sha', 'reports'}
                or type(selection['schema_version']) is not int or selection['schema_version'] != 1):
            raise ValueError('invalid quality report selection descriptor')
        if digest(selection) != source.scope_sha256: raise ValueError('quality report selection scope mismatch')
        sha, bounds = commit(selection['commit_sha']), hardened.limits(selection.get('limits'), LIMITS)
        reports = selection['reports']
        if not isinstance(reports, list) or not reports or len(reports) > bounds['max_reports']:
            raise ValueError('invalid or unbounded quality report selection')
        for report in reports:
            if not isinstance(report, dict) or set(report) != {'path', 'format'} or report['format'] not in FORMATS:
                raise ValueError('invalid quality report declaration')
            relative(report['path'])
        if len({r['path'] for r in reports}) != len(reports): raise ValueError('duplicate quality report path')
        now = datetime.now(timezone.utc).isoformat()
        commit_sha256 = digest(sha)
        records, findings, warnings = [], [], set()
        def record(kind, native, revision):
            return EvidenceRecord(source.source_key, native, revision, revision, 'development_artifact', kind, now,
                provenance='native_field', capture_completeness='partial')
        def consume(position, raw, report_sha256):
            path_sha256 = digest(reports[position]['path'])
            seen = Counter()
            for tool, rule, location, level, kind, suppression in parse_sarif(raw, bounds, warnings):
                if len(findings) >= bounds['max_findings']: raise HostileReport('quality finding bound exceeded')
                identity = digest([tool, rule, location])
                occurrence = seen[identity]; seen[identity] += 1
                if occurrence: warnings.add('duplicate_finding_identity')
                finding = QualityFinding(path_sha256, report_sha256, 'sarif', digest(tool), digest(rule), identity, occurrence,
                    level, kind, suppression, commit_sha256)
                findings.append(finding)
                records.append(record('quality_finding', digest(('quality_finding', sha, path_sha256, identity, occurrence)), digest(asdict(finding))))
            records.append(record('quality_report', digest(('quality_report', sha, path_sha256)), report_sha256))
        self.capture(source, descriptor, selection, [contained(self.manifest.parent, r['path']) for r in reports],
            bounds['max_report_bytes'], consume, 'quality report changed during capture', read_regular, regular_sha256)
        self.findings = tuple(findings)
        self.summary = dict(schema_version=1, profile=PROFILE, commit_sha256=commit_sha256, commit_binding='caller_declared',
            commit_verified=False, reports=len(reports), findings=len(findings),
            levels={level: sum(f.level == level for f in findings) for level in LEVELS},
            kinds={kind: sum(f.kind == kind for f in findings) for kind in KINDS},
            suppressed=sum(f.suppression == 'declared' for f in findings),
            warnings=sorted(warnings | {'commit_binding_declared'} | ({'no_findings_is_not_clean'} if not findings else set())),
            outcome_scope='declared_commit_quality_evidence', task_outcomes_verified=False, independent_trials=None,
            verification_authorized=False, execution_authorized=False, scripts_executed=False,
            publication_allowed=False, network_used=False, capture_completeness='partial')
        return Snapshot(digest(dict(self.summary, findings=[asdict(f) for f in findings])), tuple(records))
