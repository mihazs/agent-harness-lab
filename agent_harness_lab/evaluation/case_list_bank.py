"""Convert a reviewed task-plus-text-expectations case list into a native, frozen task bank (#838).

Deterministic offline text graders are generated only for the assertion kinds a response string can settle. Every other
case is listed with a typed exclusion reason: never dropped silently, never graded, never counted as a pass. The result
is an ordinary bank (``bank.json`` plus fixtures and frozen grader bundles), so baseline and candidate score the same
inputs through the existing before/after guard. Review, license and independence claims stay operator declarations.
"""
import json
from pathlib import Path
import re
import shutil
import tempfile

from agent_harness_lab.private_journal import encoded, sync_directory, write_bytes
from .integrity import digest, read_regular
from .interchange import MAX_BYTES, check_review, check_source, _hash, _json
from .regressions import fields
from .task_bank import identifier, validate

PROFILE = 'ahl-text-case-bank-v1'
FORMAT = 'task-text-cases-json-v1'
GRADER_ID, GRADER_VERSION = 'ahl-text-expectations', 'v1'
KINDS = ('contains', 'not_contains', 'names', 'flag')
HINTS = {'repo_search': 'needs_repository_context', 'hosting_search': 'needs_hosting_service_context'}
RECORD_FIELDS = frozenset(('id', 'task', 'expect', *HINTS))
REASONS = ('invalid_record', 'invalid_case_id', 'duplicate_case_id', 'unknown_record_field', 'needs_repository_context',
           'needs_hosting_service_context', 'unsupported_assertion_kind', 'invalid_assertion_value', 'flag_marker_undeclared',
           'no_positive_assertion', 'contradictory_assertions')
SAFE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}')
MAX_ITEMS, MAX_PHRASE_BYTES, MAX_RECORDS = 32, 512, 1000
PROTOCOL = '\n\nWrite your answer to result.json as {"response": "<text>"}.'
SEMANTICS = dict(contains='every phrase appears in the response, ignoring case', not_contains='no phrase appears in the response, ignoring case',
                 names='every name appears as a whole word with exactly the declared case',
                 flag='the declared flag marker appears (true) or does not appear (false), ignoring case')

# The one definition of the checks: the frozen grader embeds this source and the converter runs it on its own controls.
CHECK = r'''import re

def check(response, spec):
    text = response.casefold()
    ok = all(phrase.casefold() in text for phrase in spec['required_phrases'])
    ok = ok and not any(phrase.casefold() in text for phrase in spec['forbidden_phrases'])
    ok = ok and all(re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', response) for name in spec['required_names'])
    if spec['flag'] is not None:
        ok = ok and (spec['flag']['marker'].casefold() in text) == spec['flag']['expected']
    return ok

def positive(spec):
    return bool(spec['required_phrases'] or spec['required_names'] or spec['flag'] and spec['flag']['expected'])
'''
PROGRAM = '''import json
import sys
from pathlib import Path

''' + CHECK + r'''

def load(path):
    raw = path.read_bytes()
    if len(raw) > 1048576: raise ValueError('input too large')
    def unique(items):
        if len({key for key, _ in items}) != len(items): raise ValueError('duplicate key')
        return dict(items)
    def invalid(value): raise ValueError('invalid number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid)

def receipt(**extra):
    print(json.dumps(dict(schema_version=1, grader_id='ahl-text-expectations', grader_version='v1', **extra)))

try:
    spec = load(Path(sys.argv[2]))
    check('', spec)
    if type(spec['schema_version']) is not int or spec['schema_version'] != 1 or spec.get('profile') != 'ahl-text-case-bank-v1' or not positive(spec):
        raise ValueError('unsupported or vacuous reference')
except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
    receipt(valid=False, outcome=None, error='reference_invalid')
    raise SystemExit(2)
try:
    actual = load(Path(sys.argv[1]) / 'result.json')
    outcome = type(actual) is dict and type(actual.get('response')) is str and check(actual['response'], spec) is True
except (OSError, ValueError, TypeError, RecursionError):
    outcome = False
receipt(valid=True, outcome=outcome)
raise SystemExit(0 if outcome else 1)
'''
_NAMESPACE = {}
exec(compile(CHECK, '<ahl-text-expectations>', 'exec'), _NAMESPACE)
check, positive = _NAMESPACE['check'], _NAMESPACE['positive']


class _Excluded(Exception):
    def __init__(self, reason, detail=()):
        if reason not in REASONS: raise ValueError('unknown exclusion reason')
        super().__init__(reason); self.reason, self.detail = reason, list(detail)


def _phrase(value):
    return (type(value) is str and bool(value.strip()) and len(value.encode()) <= MAX_PHRASE_BYTES
            and not any(ord(char) < 32 for char in value))


def _mapping(value):
    fields(value, 'schema_version format source review bank')
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['format'] != FORMAT:
        raise ValueError('unsupported case-list mapping profile')
    check_source(value['source']); check_review(value['review'])
    bank = value['bank']; fields(bank, 'project_group time_group partition flag_marker')
    identifier(bank['project_group']); identifier(bank['time_group'])
    if bank['partition'] not in ('construction', 'development'): raise ValueError('imports remain construction or development data')
    if bank['flag_marker'] is not None and not _phrase(bank['flag_marker']): raise ValueError('flag marker must be bounded single-line text or null')
    return value


def _items(expect, kind):
    if kind not in expect: return []
    value = expect[kind]
    if not isinstance(value, list) or len(value) > MAX_ITEMS or not all(_phrase(item) for item in value):
        raise _Excluded('invalid_assertion_value', [kind])
    return list(dict.fromkeys(value))


def _spec(record, marker, duplicates):
    """The grader reference of one record, or ``_Excluded`` with the first typed reason that applies."""
    if not isinstance(record, dict) or not isinstance(record.get('task'), str) or not record['task'].strip() \
            or not isinstance(record.get('expect'), dict):
        raise _Excluded('invalid_record')
    ident, expect = record.get('id'), record['expect']
    if not isinstance(ident, str) or not SAFE_ID.fullmatch(ident): raise _Excluded('invalid_case_id')
    if ident in duplicates: raise _Excluded('duplicate_case_id')
    if set(record) - RECORD_FIELDS: raise _Excluded('unknown_record_field', sorted(set(record) - RECORD_FIELDS))
    for hint, reason in HINTS.items():
        if record.get(hint) or expect.get(hint): raise _Excluded(reason, [hint])
    unsupported = sorted(set(expect) - set(KINDS) - set(HINTS))
    if unsupported: raise _Excluded('unsupported_assertion_kind', unsupported)
    required, forbidden, names = (_items(expect, kind) for kind in ('contains', 'not_contains', 'names'))
    if 'flag' in expect and type(expect['flag']) is not bool: raise _Excluded('invalid_assertion_value', ['flag'])
    if 'flag' in expect and marker is None: raise _Excluded('flag_marker_undeclared', ['flag'])
    flag = None if 'flag' not in expect else dict(marker=marker, expected=expect['flag'])
    pieces = list(dict.fromkeys([*required, *names, *([marker] if flag and flag['expected'] else [])]))
    spec = dict(schema_version=1, profile=PROFILE, required_phrases=required, forbidden_phrases=forbidden, required_names=names, flag=flag)
    if not positive(spec): raise _Excluded('no_positive_assertion')
    if not check('\n'.join(pieces), spec): raise _Excluded('contradictory_assertions')
    return spec, pieces


def _controls(spec, pieces):
    """Frozen reference answers: two the grader must reject, two it must accept."""
    partial = next((text for text in ('\n'.join(pieces[:i] + pieces[i + 1:]) for i in range(len(pieces))) if not check(text, spec)), '')
    alternative = ' | '.join(reversed(pieces))
    return dict(broken={}, partial=dict(response=partial), complete=dict(response='\n'.join(pieces)),
                alternative_valid=dict(response=alternative if check(alternative, spec) else '\n'.join(pieces)))


def _put(root, name, value):
    path = root / name; path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes(path, value if isinstance(value, bytes) else encoded(value))


def _write_case(stage, mapping, ident, prompt, spec, pieces):
    fixture, bundle = stage / 'tasks' / ident, stage / 'graders' / ident
    _put(fixture, 'task.json', dict(prompt=prompt + PROTOCOL))
    _put(bundle, 'grade.py.txt', PROGRAM.encode()); _put(bundle, 'expected.json', spec)
    states = _controls(spec, pieces)
    for state, answer in states.items():
        _put(bundle, f'controls/{state}/task.json', (fixture / 'task.json').read_bytes()); _put(bundle, f'controls/{state}/result.json', answer)
    _put(bundle, 'controls.json', [dict(state=state, fixture='controls/' + state) for state in states])
    source = mapping['source']
    return dict(task_id=ident, root_id=ident, family='text-expectations', partition=mapping['bank']['partition'],
        project_group=mapping['bank']['project_group'], time_group=mapping['bank']['time_group'], fixture=f'tasks/{ident}',
        fixture_digest=digest(fixture), grader_bundle=f'graders/{ident}', grader_digest=digest(bundle), grader_version=GRADER_VERSION,
        source_kind=source['source_kind'], source_ref=f"{source['repository']}@{source['revision']}#{source['dataset_id']}",
        permission='approved', reviewer=mapping['review']['identity'], review_status='accepted', independence='unverified',
        safety=['offline disposable data-only fixture', 'no model or production mutation'], artifacts=['result.json'])


def _plan(mapping_path, source_path):
    raw_mapping = read_regular(mapping_path, MAX_BYTES); mapping = _mapping(_json(raw_mapping))
    raw_source = read_regular(source_path, MAX_BYTES)
    if _hash(raw_source) != mapping['source']['sha256']: raise ValueError('external source digest changed')
    records = _json(raw_source)
    if not isinstance(records, list) or not 0 < len(records) <= MAX_RECORDS: raise ValueError('bounded case array required')
    marker = mapping['bank']['flag_marker']
    seen = [r['id'] for r in records if isinstance(r, dict) and isinstance(r.get('id'), str)]
    duplicates = {ident for ident in seen if seen.count(ident) > 1}
    converted, excluded, unknown = [], [], []
    for index, record in enumerate(records, 1):
        ident = record.get('id') if isinstance(record, dict) else None
        ident = ident if isinstance(ident, str) and SAFE_ID.fullmatch(ident) else None
        if isinstance(record, dict):
            unknown += [dict(record=index, case_id=ident, scope='record', name=str(name)[:64]) for name in sorted(set(record) - RECORD_FIELDS)]
            if isinstance(record.get('expect'), dict):
                unknown += [dict(record=index, case_id=ident, scope='expect', name=str(name)[:64])
                            for name in sorted(set(record['expect']) - set(KINDS) - set(HINTS))]
        try:
            spec, pieces = _spec(record, marker, duplicates)
            converted.append((index, record, spec, pieces))
        except _Excluded as why:
            excluded.append(dict(record=index, case_id=ident, reason=why.reason, detail=[str(item)[:64] for item in why.detail]))
    return raw_mapping, raw_source, mapping, converted, excluded, unknown


def _report(raw_mapping, raw_source, mapping, converted, excluded, unknown, status, bank=None):
    reasons = {}
    for row in excluded: reasons[row['reason']] = reasons.get(row['reason'], 0) + 1
    return dict(schema_version=1, profile=PROFILE, status=status, source_sha256=_hash(raw_source), mapping_sha256=_hash(raw_mapping),
        bank_sha256=None if bank is None else bank['bank_sha256'], converted=sorted(item[1]['id'] for item in converted),
        excluded=excluded, exclusion_reasons=dict(sorted(reasons.items())), unknown_fields=unknown,
        coverage=dict(records=len(converted) + len(excluded), converted=len(converted), excluded=len(excluded)),
        scores_cover='converted_cases_only', grader=dict(id=GRADER_ID, version=GRADER_VERSION, semantics=SEMANTICS),
        review=dict(identity=mapping['review']['identity'], status='operator_attested_unverified'),
        independence='unverified', root_policy='one_root_per_case', bank_issues=[] if bank is None else bank['issues'],
        certification_eligible=False, bank_admission=False, automatic_promotion=False, publication_allowed=False)


def convert(mapping_path, source_path, output):
    """Write a new private bank for the convertible cases; a case that cannot be graded offline is excluded with a typed reason.

    Returns the conversion report. With no convertible case nothing is written and its status is ``nothing_convertible``.
    """
    plan = _plan(mapping_path, source_path)
    raw_mapping, raw_source, mapping, converted, excluded, unknown = plan
    if not converted: return _report(*plan, 'nothing_convertible')
    output = Path(output).absolute()
    if output.exists() or '..' in output.parts or any(p.is_symlink() for p in (output, *output.parents)):
        raise ValueError('new canonical private bank output required')
    inputs = [Path(p).absolute() for p in (mapping_path, source_path)]
    if any(output == p or output.is_relative_to(p.parent) or p.is_relative_to(output) for p in inputs):
        raise ValueError('bank output and source directories must not overlap')
    from agent_harness_lab.read_only import require_writable
    require_writable(output); output.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.case-bank-', dir=output.parent))
    try:
        tasks = [_write_case(stage, mapping, record['id'], record['task'], spec, pieces) for _, record, spec, pieces in converted]
        raw = (json.dumps(dict(schema_version=1, separate_by=['root_id'], tasks=tasks), sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()
        if len(raw) > 1024 * 1024: raise ValueError('converted bank exceeds the 1 MiB manifest bound; split the case list')
        write_bytes(stage / 'bank.json', raw)
        report = _report(*plan, 'converted', validate(stage / 'bank.json'))
        write_bytes(stage / 'conversion.json', encoded(report))
        for folder in (stage, *stage.rglob('*')):
            if folder.is_dir(): folder.chmod(0o700)
        sync_directory(stage); stage.rename(output); sync_directory(output.parent)
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return report
