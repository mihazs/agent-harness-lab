"""A task-plus-text-expectations case list converts into a native, frozen task bank (#838).

Synthetic cases and stub-runtime outputs only: no model, provider, network or real project data is involved.
"""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shlex
import subprocess
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.evaluation import before_after
from agent_harness_lab.evaluation.bank_tools import audit_graders, export_manifest
from agent_harness_lab.evaluation.integrity import digest
from agent_harness_lab.evaluation.task_bank import validate

FORMAT = 'task-text-cases-json-v1'
MARKER = '[[ESCALATE]]'
GOOD = {'refund-policy': 'Refunds are accepted within Thirty Days with a receipt.',
        'route-ticket': 'Owner: Billing. ' + MARKER,
        'summarize-note': 'Summary for Avery: the note is routine.'}
WORSE = {ident: 'I do not know.' for ident in GOOD}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def synthetic_cases():
    return [
        dict(id='refund-policy', task='Explain the synthetic refund window to a customer.',
             expect={'contains': ['thirty days', 'receipt'], 'not_contains': ['guarantee']}),
        dict(id='route-ticket', task='Route the synthetic ticket and name the owning team.',
             expect={'names': ['Billing'], 'flag': True}),
        dict(id='summarize-note', task='Summarize the synthetic note for its author.',
             expect={'contains': ['summary'], 'names': ['Avery'], 'not_contains': ['secret']}),
    ]


class CaseList:
    """A synthetic case list, its reviewed mapping and the new private bank folder."""

    def __init__(self, root):
        self.inputs = root / 'inputs'; self.inputs.mkdir()
        self.source = self.inputs / 'cases.json'; self.mapping = self.inputs / 'mapping.json'
        self.output = root / 'bank'
        self.cases = synthetic_cases()
        self.value = dict(schema_version=1, format=FORMAT,
            source=dict(repository='synthetic:case-list-fixture', revision='a' * 40, format_version='task-text-cases-v1',
                dataset_id='synthetic-text-cases', source_kind='synthetic', permission='approved',
                dataset_license=dict(id='CC0-1.0', ref='synthetic:fixture-author-dedication'), sha256='a' * 64),
            review=dict(identity='synthetic-operator', source='operator_attestation',
                evidence_refs=[dict(ref='synthetic:review', sha256='b' * 64)]),
            bank=dict(project_group='synthetic-cases', time_group='v1', partition='development', flag_marker=MARKER))
        self.write()

    def write(self):
        raw = (json.dumps(self.cases) + '\n').encode()
        self.source.write_bytes(raw); self.value['source']['sha256'] = sha(raw)
        self.mapping.write_text(json.dumps(self.value))

    def convert(self, output=None):
        from agent_harness_lab.evaluation.case_list_bank import convert
        return convert(self.mapping, self.source, output or self.output)

    def cli(self, *extra):
        stdout = io.StringIO()
        with patch('socket.socket', side_effect=AssertionError('no network')), \
                patch('subprocess.Popen', side_effect=AssertionError('no execution')), redirect_stdout(stdout):
            try:
                code = main(['bank-interchange', 'convert', '--mapping', str(self.mapping), '--source', str(self.source),
                             '--output', str(self.output), *extra])
            except SystemExit as stop:
                code = stop.code
        return code, stdout.getvalue()


@pytest.fixture
def cases(tmp_path):
    return CaseList(tmp_path)


def run_grader(manifest, task_id, root, files):
    """The frozen grader's outcome for one stub-runtime workspace: True, False or None when no valid receipt came back."""
    task = next(t for t in manifest['tasks'] if t['id'] == task_id)
    workspace = root / task_id; workspace.mkdir(parents=True)
    for name, raw in files.items(): (workspace / name).write_bytes(raw)
    argv = [str(workspace) if part == '{workspace}' else part for part in task['grader']]
    run = subprocess.run(argv, capture_output=True, text=True, timeout=10, env={'PATH': '/usr/bin:/bin'})
    try: envelope = json.loads(run.stdout)
    except ValueError: return None
    valid = (envelope.get('valid') is True and type(envelope.get('outcome')) is bool
             and run.returncode == (0 if envelope['outcome'] else 1))
    return envelope['outcome'] if valid else None


def respond(text): return json.dumps(dict(response=text)).encode()


def exported(cases, tmp_path):
    cases.convert()
    return export_manifest(cases.output / 'bank.json', tmp_path / 'export')


def test_synthetic_case_list_converts_to_a_valid_frozen_private_bank(cases):
    report = cases.convert()
    assert report['status'] == 'converted' and report['converted'] == sorted(GOOD) and report['excluded'] == []
    checked = validate(cases.output / 'bank.json')
    assert checked['tasks'] == 3 and checked['bank_sha256'] == report['bank_sha256']
    assert (checked['certification_eligible'], report['bank_admission'], report['publication_allowed'],
            report['automatic_promotion'], report['independence']) == (False, False, False, False, 'unverified')
    assert cases.output.stat().st_mode & 0o777 == 0o700
    assert {p.stat().st_mode & 0o777 for p in cases.output.rglob('*') if p.is_file()} == {0o600}


def test_converted_graders_pass_the_bank_controls_without_a_vacuous_success(cases):
    cases.convert()
    result = audit_graders(cases.output / 'bank.json', cooperative=True)
    assert result['status'] == 'controls_passed'
    assert {check['control'] for check in result['checks']} >= {'no_op', 'broken', 'partial', 'complete', 'alternative_valid'}


@pytest.mark.parametrize('task_id, response, outcome', [
    pytest.param('refund-policy', GOOD['refund-policy'], True, id='phrases-match-ignoring-case'),
    pytest.param('refund-policy', 'Refunds take thirty days.', False, id='required-phrase-missing'),
    pytest.param('refund-policy', GOOD['refund-policy'] + ' This is a GUARANTEE.', False, id='forbidden-phrase-present'),
    pytest.param('route-ticket', GOOD['route-ticket'], True, id='name-and-flag-present'),
    pytest.param('route-ticket', 'Owner: Billing.', False, id='flag-marker-missing'),
    pytest.param('route-ticket', 'Owner: billing. ' + MARKER, False, id='name-is-case-sensitive'),
    pytest.param('route-ticket', 'Owner: Billings. ' + MARKER, False, id='name-needs-a-whole-word'),
    pytest.param('summarize-note', GOOD['summarize-note'], True, id='all-assertion-kinds-hold'),
    pytest.param('summarize-note', GOOD['summarize-note'] + ' The secret is out.', False, id='one-forbidden-phrase-fails-the-case'),
    pytest.param('summarize-note', '', False, id='empty-response'),
])
def test_grader_judges_a_stub_response_offline(cases, tmp_path, task_id, response, outcome):
    manifest = exported(cases, tmp_path)
    assert run_grader(manifest, task_id, tmp_path / 'arm', {'result.json': respond(response)}) is outcome


@pytest.mark.parametrize('files', [
    pytest.param({}, id='no-result-file'),
    pytest.param({'result.json': b'not json'}, id='malformed-json'),
    pytest.param({'result.json': b'{"response": 4}'}, id='response-not-text'),
    pytest.param({'result.json': b'{"response": "a", "response": "b"}'}, id='duplicate-keys'),
    pytest.param({'result.json': b'["response"]'}, id='not-an-object'),
])
def test_missing_or_malformed_output_fails_and_never_passes(cases, tmp_path, files):
    manifest = exported(cases, tmp_path)
    assert run_grader(manifest, 'route-ticket', tmp_path / 'arm', files) is False


def exclusion(cases, record, **mapping):
    """The conversion report after replacing the case list with the one record, plus a convertible control case."""
    cases.cases = [synthetic_cases()[0], record]
    cases.value['bank'].update(mapping); cases.write()
    return cases.convert()


@pytest.mark.parametrize('extra, reason', [
    pytest.param(dict(expect={'contains': ['x'], 'repo_search': ['where is the parser']}), 'needs_repository_context', id='repository-hint-in-expect'),
    pytest.param(dict(repo_search=['where is the parser'], expect={'contains': ['x']}), 'needs_repository_context', id='repository-hint-on-record'),
    pytest.param(dict(expect={'contains': ['x']}, hosting_search='open issues'), 'needs_hosting_service_context', id='hosting-service-hint'),
    pytest.param(dict(expect={'regex': 'x+'}), 'unsupported_assertion_kind', id='unknown-assertion-kind'),
    pytest.param(dict(expect={'contains': ['x'], 'judge': 'be nice'}), 'unsupported_assertion_kind', id='unknown-kind-beside-a-supported-one'),
    pytest.param(dict(expect={'contains': ['x']}, weight=2), 'unknown_record_field', id='unknown-record-field'),
    pytest.param(dict(expect={'contains': 'x'}), 'invalid_assertion_value', id='phrase-list-is-a-string'),
    pytest.param(dict(expect={'contains': ['']}), 'invalid_assertion_value', id='empty-phrase'),
    pytest.param(dict(expect={'contains': ['x' * 513]}), 'invalid_assertion_value', id='oversized-phrase'),
    pytest.param(dict(expect={'contains': ['x'] * 33}), 'invalid_assertion_value', id='too-many-phrases'),
    pytest.param(dict(expect={'contains': None}), 'invalid_assertion_value', id='null-assertion-list'),
    pytest.param(dict(expect={'names': [7]}), 'invalid_assertion_value', id='name-not-text'),
    pytest.param(dict(expect={'contains': ['x'], 'flag': 'yes'}), 'invalid_assertion_value', id='flag-not-boolean'),
    pytest.param(dict(expect={'contains': ['x'], 'flag': False}, _marker=None), 'flag_marker_undeclared', id='flag-without-declared-marker'),
    pytest.param(dict(expect={'not_contains': ['x']}), 'no_positive_assertion', id='only-a-forbidden-phrase'),
    pytest.param(dict(expect={'flag': False}), 'no_positive_assertion', id='only-an-absent-flag'),
    pytest.param(dict(expect={}), 'no_positive_assertion', id='no-assertion'),
    pytest.param(dict(expect={'contains': ['hello world'], 'not_contains': ['hello']}), 'contradictory_assertions', id='required-phrase-contains-a-forbidden-one'),
    pytest.param(dict(expect={'contains': [MARKER], 'flag': False}), 'contradictory_assertions', id='required-phrase-carries-an-absent-flag'),
    pytest.param(dict(expect=['contains']), 'invalid_record', id='expect-not-an-object'),
    pytest.param(dict(expect=None), 'invalid_record', id='expect-missing'),
    pytest.param(dict(task=''), 'invalid_record', id='empty-task'),
])
def test_unsupported_case_is_excluded_with_its_typed_reason_and_never_graded(cases, extra, reason):
    marker = extra.pop('_marker', MARKER)
    record = {'id': 'candidate', 'task': 'A synthetic task.', **extra}
    report = exclusion(cases, record, flag_marker=marker)
    assert [(row['case_id'], row['reason']) for row in report['excluded']] == [('candidate', reason)]
    assert report['converted'] == ['refund-policy'] and not (cases.output / 'graders' / 'candidate').exists()
    assert len(report['converted']) + len(report['excluded']) == report['coverage']['records'] == 2
    assert report['exclusion_reasons'] == {reason: 1} and report['scores_cover'] == 'converted_cases_only'
    assert [task['task_id'] for task in json.loads((cases.output / 'bank.json').read_bytes())['tasks']] == ['refund-policy']


@pytest.mark.parametrize('ident', [
    pytest.param('../escape', id='parent-traversal'), pytest.param('a/b', id='path-separator'),
    pytest.param('', id='empty-id'), pytest.param('x' * 129, id='oversized-id'), pytest.param(7, id='numeric-id'),
    pytest.param('.hidden', id='leading-dot'),
])
def test_unsafe_case_identifier_is_excluded_and_not_echoed(cases, ident):
    report = exclusion(cases, dict(id=ident, task='A synthetic task.', expect={'contains': ['x']}))
    assert report['converted'] == ['refund-policy']
    assert [row['reason'] for row in report['excluded']] == ['invalid_case_id']
    assert report['excluded'][0]['case_id'] is None


def test_duplicate_case_identifiers_exclude_every_copy(cases):
    cases.cases = [dict(id='twin', task='One task.', expect={'contains': ['x']}), dict(id='twin', task='Another task.', expect={'contains': ['y']})]
    cases.write(); report = cases.convert()
    assert report['converted'] == [] and report['status'] == 'nothing_convertible' and not cases.output.exists()
    assert [row['reason'] for row in report['excluded']] == ['duplicate_case_id'] * 2


@pytest.mark.parametrize('record', [pytest.param('a string', id='string'), pytest.param(None, id='null'), pytest.param([1], id='list')])
def test_a_record_that_is_not_an_object_is_excluded_not_dropped(cases, record):
    report = exclusion(cases, record)
    assert [(row['record'], row['reason']) for row in report['excluded']] == [(2, 'invalid_record')]


def test_unknown_fields_are_reported_by_record_and_scope_and_never_ignored(cases):
    record = dict(id='candidate', task='A synthetic task.', priority=1, expect={'contains': ['x'], 'tone': 'kind'})
    report = exclusion(cases, record)
    assert report['unknown_fields'] == [dict(record=2, case_id='candidate', scope='record', name='priority'),
                                        dict(record=2, case_id='candidate', scope='expect', name='tone')]
    assert report['converted'] == ['refund-policy'] and report['excluded'][0]['reason'] == 'unknown_record_field'


def test_hint_with_an_empty_value_does_not_claim_repository_context(cases):
    record = dict(id='candidate', task='A synthetic task.', expect={'contains': ['x'], 'repo_search': []})
    assert exclusion(cases, record)['converted'] == ['candidate', 'refund-policy']


def mutated(cases, change):
    change(cases.value); cases.write()


@pytest.mark.parametrize('change', [
    pytest.param(lambda v: v.pop('review'), id='no-review'),
    pytest.param(lambda v: v['review'].update(source='model_review'), id='review-not-an-operator-attestation'),
    pytest.param(lambda v: v['review'].update(evidence_refs=[]), id='no-review-evidence'),
    pytest.param(lambda v: v['source'].update(permission='unverified'), id='permission-not-approved'),
    pytest.param(lambda v: v['source']['dataset_license'].update(id='unknown'), id='license-unknown'),
    pytest.param(lambda v: v.update(format='promptfoo-cases-json-v1'), id='other-profile'),
    pytest.param(lambda v: v.update(extra='x'), id='unknown-mapping-field'),
    pytest.param(lambda v: v['bank'].update(partition='certification'), id='protected-partition'),
    pytest.param(lambda v: v['bank'].update(flag_marker=''), id='empty-flag-marker'),
    pytest.param(lambda v: v['bank'].pop('time_group'), id='missing-bank-declaration'),
])
def test_no_grader_is_generated_without_an_accepted_review_mapping(cases, change):
    mutated(cases, change)
    with pytest.raises(ValueError):
        cases.convert()
    assert not cases.output.exists()
    code, _ = cases.cli()
    assert code == 2 and not cases.output.exists()


def test_a_changed_source_is_rejected_before_any_output(cases):
    cases.source.write_text(json.dumps(cases.cases[:1]))
    with pytest.raises(ValueError, match='digest'):
        cases.convert()
    assert not cases.output.exists()


@pytest.mark.parametrize('raw', [pytest.param(b'{}', id='object'), pytest.param(b'[]', id='empty-list'),
                                 pytest.param(b'[1,', id='truncated'), pytest.param(b'[{"id":"a","id":"b"}]', id='duplicate-keys')])
def test_malformed_source_is_rejected(cases, raw):
    cases.source.write_bytes(raw); cases.value['source']['sha256'] = sha(raw); cases.mapping.write_text(json.dumps(cases.value))
    with pytest.raises(ValueError):
        cases.convert()
    assert not cases.output.exists()


def test_existing_or_overlapping_output_is_rejected(cases, tmp_path):
    cases.output.mkdir()
    with pytest.raises(ValueError):
        cases.convert()
    with pytest.raises(ValueError, match='overlap'):
        cases.convert(cases.inputs / 'bank')


def test_conversion_is_deterministic_and_binds_the_source_and_mapping(cases, tmp_path):
    first = cases.convert(); second = cases.convert(tmp_path / 'again')
    assert first['bank_sha256'] == second['bank_sha256']
    assert digest(cases.output / 'graders') == digest(tmp_path / 'again' / 'graders')
    assert first['source_sha256'] == sha(cases.source.read_bytes()) and first['mapping_sha256'] == sha(cases.mapping.read_bytes())


def test_cli_converts_offline_and_prints_the_report(cases):
    code, out = cases.cli()
    report = json.loads(out)
    assert code == 0 and report['status'] == 'converted' and (cases.output / 'conversion.json').is_file()
    assert json.loads((cases.output / 'conversion.json').read_bytes()) == report


def test_cli_prints_the_exclusions_and_exits_two_when_nothing_converts(cases):
    cases.cases = [dict(id='only', task='A synthetic task.', expect={'contains': ['x'], 'repo_search': ['q']})]; cases.write()
    code, out = cases.cli()
    assert code == 2 and json.loads(out)['excluded'][0]['reason'] == 'needs_repository_context' and not cases.output.exists()


def documented_export_argv(bank, export):
    """The how-to's step 5 export command, with its placeholder folders replaced by real ones."""
    page = Path(__file__).resolve().parents[3] / 'docs' / 'convert-a-case-list.md'
    line = next(l for l in page.read_text().splitlines() if l.startswith('ahl bank ') and '--export' in l)
    swap = {'NEW_PRIVATE_BANK/bank.json': str(bank / 'bank.json'), 'NEW_PRIVATE_EXPORT': str(export)}
    return [swap.get(word, word) for word in shlex.split(line)[1:]]


@pytest.mark.parametrize('count', [13, 16], ids=['just-above-the-default-ceiling', 'sixteen-cases'])
def test_documented_export_command_exports_a_converted_bank_above_twelve_cases(cases, tmp_path, count):
    cases.cases = [dict(id=f'case-{n:02d}', task=f'Describe synthetic item {n}.', expect={'contains': [f'item{n}']})
                   for n in range(count)]
    cases.write(); cases.convert()
    stdout = io.StringIO()
    with redirect_stdout(stdout):
        code = main(documented_export_argv(cases.output, tmp_path / 'export'))
    # 3 is the descriptive, never-certified outcome of every export; the ceiling rejection exits 2 instead.
    assert code == 3 and len(json.loads((tmp_path / 'export' / 'manifest.json').read_bytes())['tasks']) == count


def arm_pairs(manifest, root, baseline, candidate):
    pairs = []
    for task in manifest['tasks']:
        outcomes = [run_grader(manifest, task['id'], root / arm / task['id'], {'result.json': respond(outputs[task['id']])})
                    if task['id'] in outputs else None for arm, outputs in (('baseline', baseline), ('candidate', candidate))]
        pairs.append(dict(task=task['id'], root=task['root'], baseline=outcomes[0], candidate=outcomes[1]))
    return pairs


def guard(cases, manifest, pairs):
    arm = before_after.frozen_inputs(bank_sha256=sha(cases.output.joinpath('bank.json').read_bytes()),
        grader_sha256=digest(cases.output / 'graders'), runtime_sha256='c' * 64,
        seed_schedule_sha256=before_after.schedule_sha256(manifest['tasks']), budget_share=dict(trials=len(pairs)))
    return before_after.decide(pairs, baseline=arm, candidate=dict(arm))


def test_an_identical_arm_is_never_reported_as_improved_and_reports_scores_intervals_and_counts(cases, tmp_path):
    manifest = exported(cases, tmp_path)
    result = guard(cases, manifest, arm_pairs(manifest, tmp_path / 'aa', GOOD, GOOD))
    assert result['verdict'] == 'inconclusive' and result['adoption_blocked'] is False
    assert result['pairs']['eligible'] == 3 and result['pairs']['unchanged'] == 3 and result['pairs']['repeats_per_task_arm'] == 1
    assert result['scores']['baseline']['mean'] == result['scores']['candidate']['mean'] == 1.0
    assert result['delta']['mean'] == 0 and {'low', 'high'} <= set(result['scores']['baseline']) and {'low', 'high'} <= set(result['delta'])


def test_a_deliberately_harmful_candidate_is_reported_worse_with_scores_intervals_and_counts(cases, tmp_path):
    manifest = exported(cases, tmp_path)
    result = guard(cases, manifest, arm_pairs(manifest, tmp_path / 'harm', GOOD, WORSE))
    assert (result['verdict'], result['adoption_blocked']) == ('candidate_regressed', True)
    assert result['pairs']['eligible'] == 3 and result['pairs']['flipped_to_fail'] == 3
    assert (result['scores']['baseline']['mean'], result['scores']['candidate']['mean'], result['delta']['mean']) == (1.0, 0.0, -1.0)
    assert {'low', 'high'} <= set(result['scores']['candidate']) and {'low', 'high'} <= set(result['delta'])


def test_excluded_cases_never_count_as_pairs_or_passes(cases, tmp_path):
    cases.cases.append(dict(id='needs-repo', task='Find the parser.', expect={'contains': ['x'], 'repo_search': ['parser']}))
    cases.write(); report = cases.convert()
    manifest = export_manifest(cases.output / 'bank.json', tmp_path / 'export')
    result = guard(cases, manifest, arm_pairs(manifest, tmp_path / 'aa', GOOD, GOOD))
    assert [row['case_id'] for row in report['excluded']] == ['needs-repo']
    assert result['pairs']['eligible'] == 3 == len(manifest['tasks']) and 'needs-repo' not in {t['id'] for t in manifest['tasks']}


def test_an_arm_without_output_for_a_case_is_unknown_and_never_a_pass(cases, tmp_path):
    manifest = exported(cases, tmp_path)
    result = guard(cases, manifest, arm_pairs(manifest, tmp_path / 'gap', GOOD, {k: v for k, v in GOOD.items() if k != 'route-ticket'}))
    assert result['pairs']['unknown'] == 1 and result['pairs']['eligible'] == 2 and 'unknown_outcomes' in result['reasons']
    assert result['verdict'] != 'improved'
