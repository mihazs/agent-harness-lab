"""Synthetic public-entrypoint controls for automatic analysis and safe progress."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent_harness_lab
from agent_harness_lab.cli import main, assets, _progress_reporter
from agent_harness_lab.evidence import collect
from agent_harness_lab.evaluation.construction import views
from agent_harness_lab.evaluation.benchmark import public_export_allowed
from agent_harness_lab.diagnostics.process_mining import mine
from tools.diagnose import analyze


class Pipeline:
    """A synthetic project and capture driven through the public CLI."""

    def __init__(self, root):
        self.root = root
        self.project = self.root/'project'; self.project.mkdir()

    def capture(self, rows):
        path = self.root/'synthetic.jsonl'
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return path

    def invoke(self, command='analyze', rows=None, extra=()):
        args = [command, str(self.project), '--output', str(self.root/'output')]
        if rows is not None:
            args += ['--events', str(self.capture(rows))]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([*args, *extra])
        return code, json.loads(out.getvalue()), err.getvalue()

    @staticmethod
    def rows():
        return [dict(case_id=f'PRIVATE_CASE_{case}', event_id=str(offset), activity=activity,
                     capture_order=offset, body='PRIVATE_BODY', **fields)
                for offset, (activity, fields) in enumerate([
                    ('tool_result', dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')),
                    ('tool_result', dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')),
                    ('check', dict(execution_status='error'))]) for case in range(3)]

    def incident_corpus(self):
        path = self.root/'incidents.json'
        path.write_text(json.dumps([dict(incident_id='synthetic-incident', root_id='synthetic-root',
            summary='Synthetic timeout observation', labels=['timeout'], evidence_refs=['synthetic:fixture'],
            reviewer='synthetic-reviewer', provenance='reviewed_annotation', sampling='signal_enriched',
            source_kind='synthetic', approved=True)]))
        return path


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    expected = os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
    if expected:
        assert Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve())
    for name, folder in (('CODEX_HOME', 'codex'), ('CLAUDE_CONFIG_DIR', 'claude'), ('XDG_STATE_HOME', 'state')):
        monkeypatch.setenv(name, str(tmp_path/folder))
    return Pipeline(tmp_path)


def test_analysis_alias_runs_all_default_methods_and_reports_insufficient_authority(pipeline):
    code, receipt, err = pipeline.invoke('analysis', pipeline.rows())
    assert code == 0
    assert receipt['status'] == 'analyzed'
    assert receipt['analysis']['evidence_state'] == 'observed'
    methods = {row['id']: row for row in receipt['analysis']['algorithms']}
    assert methods['directly_follows']['outputs']['raw_edges'] == 2
    assert methods['recurring_sequences']['status'] == 'completed'
    assert methods['recurring_sequences']['outputs']['sequences'] > 0
    assert methods['duplicate_observations']['outputs']['findings'] == 3
    assert methods['structured_checks']['outputs']['findings'] == 3
    assert all(row['authority'] == 'descriptive_only' for row in methods.values())
    assert all(row['selected'] for row in methods.values())
    assert receipt['analysis']['method_selection']['policy'] == 'structural_prerequisites_v1'
    assert not receipt['analysis']['method_selection']['output_values_used_for_selection']
    assert receipt['analysis']['method_selection']['profile'] == 'ahl-analysis-execution-v1'
    synthesis = receipt['analysis']['synthesis']
    assert set(synthesis['contributing_methods']) == set(methods)
    assert len(synthesis['dependency_groups']) == 1
    assert synthesis['dependency_groups'][0]['independence'] == 'unverified'
    assert not synthesis['promotion_authority']
    assert synthesis['profile'] == 'ahl-analysis-execution-v1'
    assert len(receipt['analysis']['not_run']) == 4
    data = json.loads((pipeline.root/'output/analysis.json').read_text())
    assert data['execution'] == receipt['analysis']
    assert 'independent_roots' not in data['analysis']['process_mining']
    assert 'Directly-follows edges: 2' in (pipeline.root/'output/report.md').read_text()
    for stage in ('discovery', 'collection', 'analysis', 'collection_complete'):
        assert 'ahl: '+stage in err
    for canary in ('PRIVATE_CASE', 'PRIVATE_BODY', 'synthetic.jsonl'):
        assert canary not in err + json.dumps(receipt)
    assert list(pipeline.project.iterdir()) == []


def test_empty_history_is_explicit_and_does_not_require_configuration(pipeline):
    code, receipt, _ = pipeline.invoke()
    assert code == 0
    assert receipt['analysis']['evidence_state'] == 'empty'
    assert all(row['status'] == 'no_events' for row in receipt['analysis']['algorithms'])
    assert 'current-source' in receipt['next_action']
    assert not (pipeline.project/'ahl.json').exists()


def test_explicit_lexical_method_executes_and_keeps_retrieval_untrusted(pipeline):
    from agent_harness_lab.diagnostics.retrieval import LexicalIndex
    original = LexicalIndex.query
    with patch.object(LexicalIndex, 'query', autospec=True, side_effect=original) as dispatch:
        code, receipt, err = pipeline.invoke(rows=pipeline.rows(), extra=[
            '--incident-corpus', str(pipeline.incident_corpus()), '--incident-query', 'timeout'])
    assert code == 0
    dispatch.assert_called_once()
    method = receipt['analysis']['algorithms'][-1]
    assert method['id'] == 'incident_retrieval'
    assert method['execution'] == 'completed'
    assert method['outputs']['matches'] == 1
    assert method['authority'] == 'untrusted_evidence_only'
    assert method['approval'] == 'caller_declared_reviewed_approval_unverified'
    assert 'incident_retrieval' not in [row['id'] for row in receipt['analysis']['not_run']]
    data = json.loads((pipeline.root/'output/analysis.json').read_text())
    retrieval = data['incident_retrieval']
    assert retrieval['calibration'] == 'not_a_correctness_oracle'
    assert not retrieval['promotion_authority']
    assert retrieval['results'][0]['evidence_refs'] == ['synthetic:fixture']
    assert 'Synthetic timeout observation' not in err + json.dumps(receipt)
    assert 'local lexical' in (pipeline.root/'output/report.md').read_text()
    prepared = views(data, assets(), pipeline.root/'retrieval-roles', time.monotonic()+30)
    for folder in prepared.values():
        summary = json.loads((folder/'analysis-summary.json').read_text())
        assert summary['incident_retrieval'] == retrieval
    provenance = dict(source_kind='synthetic', source_ref='synthetic:test', approved_publication=True, derived_from_private=False)
    for facet in (retrieval, retrieval['results'][0], method):
        for wrapper in (None, 'incident_retrieval', 'analysis', 'execution', 'receipt', 'context', 'binding'):
            marked = dict(facet, publication_allowed=True, provenance=provenance)
            record = marked if wrapper is None else dict(provenance=provenance, publication_allowed=True, **{wrapper:marked})
            assert not public_export_allowed([record])


def test_no_match_or_empty_capture_does_not_skip_explicit_retrieval(pipeline):
    code, receipt, _ = pipeline.invoke(rows=[], extra=[
        '--incident-corpus', str(pipeline.incident_corpus()), '--incident-query', 'unmatched'])
    assert code == 0
    method = receipt['analysis']['algorithms'][-1]
    assert method['selected']
    assert method['status'] == 'completed'
    assert method['outputs'] == dict(matches=0)
    assert receipt['analysis']['evidence_state'] == 'empty'


@pytest.mark.parametrize('flags', [
    pytest.param(lambda path, invalid: ['--incident-corpus', str(path)], id='corpus-without-query'),
    pytest.param(lambda path, invalid: ['--incident-query', 'timeout'], id='query-without-corpus'),
    pytest.param(lambda path, invalid: ['--incident-corpus', str(path), '--incident-query', '  '], id='blank-query'),
    pytest.param(lambda path, invalid: ['--incident-corpus', str(invalid), '--incident-query', 'timeout'],
                 id='unapproved-corpus'),
])
def test_partial_or_unapproved_lexical_inputs_fail_before_collection(pipeline, flags):
    path = pipeline.incident_corpus()
    invalid = pipeline.root/'unapproved.json'
    invalid.write_text(path.read_text().replace('true', 'false'))
    with patch('agent_harness_lab.workflow.collect') as collection:
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as rejected:
            main(['analyze', str(pipeline.project), '--output', str(pipeline.root/'rejected'), *flags(path, invalid)])
        assert rejected.value.code == 2
        collection.assert_not_called()


@pytest.mark.parametrize('changes', [
    pytest.param(dict(approved='true'), id='string-approval'),
    pytest.param(dict(provenance='automatic'), id='automatic-provenance'),
    pytest.param(dict(reviewer=''), id='empty-reviewer'),
    pytest.param(dict(evidence_refs=[]), id='no-evidence-refs'),
    pytest.param(dict(labels='timeout'), id='string-labels'),
    pytest.param(dict(source_kind='unknown'), id='unknown-source-kind'),
])
def test_lexical_declarations_require_valid_review_fields_and_empty_corpus_completes(pipeline, changes):
    from agent_harness_lab.diagnostics.retrieval import contextual_search
    path = pipeline.incident_corpus()
    valid = json.loads(path.read_text())[0]
    path.write_text(json.dumps([dict(valid, **changes)]))
    with pytest.raises(ValueError):
        contextual_search(path, 'timeout')


def test_empty_lexical_corpus_completes(pipeline):
    from agent_harness_lab.diagnostics.retrieval import contextual_search
    path = pipeline.incident_corpus()
    path.write_text('[]')
    result = contextual_search(path, 'timeout')
    assert result['input_records'] == 0
    assert result['results'] == []


PUBLIC_PROVENANCE = dict(source_kind='synthetic', source_ref='synthetic:test',
                         approved_publication=True, derived_from_private=False)
RECEIPT_FACETS = ('receipt.analysis', 'receipt.method_selection', 'receipt.synthesis', 'analysis', 'process_mining',
                  'method_inputs', 'method_execution', 'view.proposer', 'view.evaluator_author')
EXPORT_WRAPPERS = (None, 'execution', 'receipt', 'analysis', 'context', 'binding', 'process_mining')


def receipt_facets(pipeline):
    """Every private execution receipt facet the public analysis entrypoint writes or prepares."""
    _, result, _ = pipeline.invoke(rows=pipeline.rows())
    data = json.loads((pipeline.root/'output/analysis.json').read_text())
    prepared = views(data, assets(), pipeline.root/'private-role-views', time.monotonic()+30)
    facets = {'receipt.analysis': result['analysis'], 'receipt.method_selection': result['analysis']['method_selection'],
              'receipt.synthesis': result['analysis']['synthesis'], 'analysis': data['analysis'],
              'process_mining': data['analysis']['process_mining'], 'method_inputs': data['analysis']['method_inputs'],
              'method_execution': data['analysis']['method_execution']}
    facets.update({'view.'+role: json.loads((folder/'analysis-summary.json').read_text())
                   for role, folder in prepared.items()})
    return facets


def test_private_execution_receipt_facets_cover_every_prepared_view(pipeline):
    assert tuple(receipt_facets(pipeline)) == RECEIPT_FACETS


@pytest.mark.parametrize(('facet', 'wrapper'), [
    pytest.param(facet, wrapper, id=f"{facet}@{wrapper or 'top'}") for facet in RECEIPT_FACETS for wrapper in EXPORT_WRAPPERS])
def test_private_execution_receipt_rejects_relabelled_export_and_supported_wrappers(pipeline, facet, wrapper):
    receipt = dict(receipt_facets(pipeline)[facet], publication_allowed=True, provenance=PUBLIC_PROVENANCE)
    record = receipt if wrapper is None else dict(publication_allowed=True,
        provenance=PUBLIC_PROVENANCE, **{wrapper: receipt})
    assert not public_export_allowed([record])


def test_public_execution_and_graph_records_remain_exportable(pipeline):
    assert public_export_allowed([dict(publication_allowed=True,
        provenance=PUBLIC_PROVENANCE, execution=dict(exit_code=0))])
    public_graph = mine(json.dumps(row) for row in pipeline.rows())
    assert public_export_allowed([dict(public_graph,
        publication_allowed=True, provenance=PUBLIC_PROVENANCE)])


def test_partial_capture_and_thin_support_do_not_report_clean_evidence(pipeline):
    path = pipeline.capture([dict(case_id='s', event_id='1', activity='read')])
    with path.open('a') as stream:
        stream.write('invalid synthetic record\n')
    code, receipt, _ = pipeline.invoke(extra=['--events', str(path)])
    assert code == 0
    assert receipt['analysis']['evidence_state'] == 'incomplete'
    methods = {row['id']: row for row in receipt['analysis']['algorithms']}
    assert methods['recurring_sequences']['status'] == 'insufficient_support'
    assert methods['duplicate_observations']['status'] == 'insufficient_support'
    assert receipt['analysis']['caveats']


def test_graphs_keep_case_and_declared_root_boundaries_without_id_length_limit(pipeline):
    rows = [dict(case_id=case*300, event_id=str(index), activity=activity, root_case_id='shared')
            for index, activity in enumerate(('read', 'check')) for case in ('a', 'b')]
    data = analyze(iter(rows))['process_mining']
    assert data['reported_roots'] == 1
    assert data['raw_edges'] == [dict(source='read', target='check', occurrences=2, root_cases=1)]
    assert 'success' not in data


def test_bad_timestamps_are_coverage_gaps_in_default_analysis(pipeline):
    code, receipt, _ = pipeline.invoke(rows=[dict(case_id='s', event_id='1', activity='read', timestamp='bad')])
    assert code == 0
    assert receipt['analysis']['evidence_state'] == 'incomplete'
    graph = json.loads((pipeline.root/'output/analysis.json').read_text())['analysis']['process_mining']
    assert graph['invalid_timestamps'] == 1
    assert graph['missing_timestamps'] == 1


def test_improve_receives_graph_without_separate_mine_or_analyze_invocation(pipeline):
    (pipeline.project/'AGENTS.md').write_text('Synthetic project instructions\n')
    agent = pipeline.root/'fake-agent'
    agent.write_text('#!'+sys.executable+'\n'+'''import json, pathlib, sys
if '--version' in sys.argv:
    print('synthetic'); sys.exit(0)
sys.stdin.read()
context = json.loads(pathlib.Path('context.json').read_text())
data = json.loads((pathlib.Path.cwd().parent/'analysis.json').read_text())
assert data['analysis']['process_mining']['raw_edges']
assert data['incident_retrieval']['results'][0]['authority'] == 'untrusted_evidence_only'
(pathlib.Path(context['evaluation'])/'proposal.json').write_text('{"status":"no_change"}')
print(json.dumps({'type':'turn.completed','usage':{'output_tokens':1}}))
''')
    agent.chmod(0o700)
    code, receipt, err = pipeline.invoke('improve', pipeline.rows(), ['--agent', 'codex', '--codex', str(agent), '--timeout', '30',
        '--incident-corpus', str(pipeline.incident_corpus()), '--incident-query', 'timeout'])
    assert code == 0
    assert receipt['status'] == 'no_change'
    assert receipt['changed_files'] == []
    assert receipt['analysis']['algorithms'][0]['outputs']['raw_edges'] == 2
    assert receipt['analysis']['algorithms'][-1]['outputs']['matches'] == 1
    assert 'candidate_preparation' in err
    assert not list((pipeline.root/'output').glob('evaluation-*'))
    assert (pipeline.project/'AGENTS.md').read_text() == 'Synthetic project instructions\n'
    assert receipt['analysis']['method_selection']['policy'] == 'structural_prerequisites_v1'


def test_method_selection_uses_prerequisites_instead_of_favorable_findings(pipeline):
    rows = [dict(case_id='case', event_id=str(index), capture_order=index,
                 activity='tool_result', tool_name='read', input_fingerprint=str(index),
                 result_fingerprint=str(index), state_revision='s', execution_status='returned')
            for index in range(2)]
    _, receipt, _ = pipeline.invoke(rows=rows)
    methods = {row['id']: row for row in receipt['analysis']['algorithms']}
    assert methods['duplicate_observations']['selected']
    assert methods['duplicate_observations']['outputs']['findings'] == 0
    assert methods['structured_checks']['selected']
    assert methods['structured_checks']['outputs']['findings'] == 0
    assert not methods['recurring_sequences']['selected']
    assert all(not_run['reason'] for not_run in receipt['analysis']['not_run'])


def test_activity_only_capture_keeps_graph_and_explains_missing_method_inputs(pipeline):
    _, receipt, _ = pipeline.invoke(rows=[dict(case_id='case', event_id=str(index), activity=activity)
                                      for index, activity in enumerate(('read', 'check'))])
    methods = {row['id']: row for row in receipt['analysis']['algorithms']}
    assert methods['directly_follows']['selected']
    assert methods['directly_follows']['outputs']['raw_edges'] == 1
    for name in ('recurring_sequences', 'duplicate_observations', 'structured_checks'):
        assert not methods[name]['selected']
        assert methods[name]['status'] == 'insufficient_support'
    assert 'execution order' in methods['directly_follows']['selection_reason']


@pytest.mark.parametrize('ordering', [pytest.param(None, id='unverified'), pytest.param((1, 0, 2), id='reversed')])
def test_unverified_or_reversed_order_does_not_qualify_sequence_outputs(pipeline, ordering):
    rows = pipeline.rows()
    for row in rows:
        if ordering is None:
            row.pop('capture_order')
        else:
            row['capture_order'] = ordering[int(row['event_id'])]
    raw = analyze(rows)
    data = analyze(rows, select_methods=True)
    from agent_harness_lab.analysis_receipt import summarize
    receipt = summarize(dict(analysis=data))
    methods = {row['id']: row for row in receipt['algorithms']}
    assert len(raw['recurring_sequences']) > 0
    assert data['recurring_sequences'] == []
    assert not methods['recurring_sequences']['selected']
    assert not methods['duplicate_observations']['selected']
    assert methods['recurring_sequences']['execution'] == 'not_run'
    assert data['process_mining'] == raw['process_mining']


def test_strict_timestamps_can_qualify_order_but_ties_cannot(pipeline):
    from agent_harness_lab.analysis_receipt import summarize
    rows = pipeline.rows()
    for row in rows:
        row.pop('capture_order')
        row['timestamp'] = f"2026-01-01T00:00:0{row['event_id']}Z"
    receipt = summarize(dict(analysis=analyze(rows, select_methods=True)))
    assert receipt['algorithms'][1]['selected']
    for row in rows:
        row['timestamp'] = '2026-01-01T00:00:00Z'
    receipt = summarize(dict(analysis=analyze(rows, select_methods=True)))
    assert not receipt['algorithms'][1]['selected']


def test_declared_shared_root_cannot_inflate_recurrence_eligibility(pipeline):
    from agent_harness_lab.analysis_receipt import summarize
    rows = pipeline.rows()
    for row in rows:
        row['root_case_id'] = 'shared-root'
    receipt = summarize(dict(analysis=analyze(rows, select_methods=True)))
    assert receipt['method_selection']['inputs']['sequence_roots'] == 1
    assert not receipt['algorithms'][1]['selected']


def test_unordered_case_does_not_suppress_ordered_method_subsets(pipeline):
    from agent_harness_lab.analysis_receipt import summarize
    ordered = pipeline.rows()
    unsupported = [dict(row, case_id='unordered') for row in ordered if row['case_id'] == 'PRIVATE_CASE_0']
    for row in unsupported:
        row.pop('capture_order')
    selected = analyze([*ordered, *unsupported], select_methods=True)
    supported = analyze(ordered, select_methods=True)
    receipt = summarize(dict(analysis=selected))
    methods = {row['id']: row for row in receipt['algorithms']}
    assert methods['recurring_sequences']['selected']
    assert methods['duplicate_observations']['selected']
    assert selected['recurring_sequences'] == supported['recurring_sequences']
    assert selected['findings'] == supported['findings']
    assert methods['recurring_sequences']['input_events'] == len(ordered)
    assert methods['recurring_sequences']['discovery_input_events'] == len(ordered) + len(unsupported)
    assert receipt['method_selection']['inputs']['unordered_sequence_cases'] == 1
    assert receipt['method_selection']['inputs']['unordered_observation_cases'] == 1
    assert selected['process_mining']['events'] == len(ordered) + len(unsupported)


@pytest.mark.parametrize('source_format', ['codex', 'claude'])
def test_public_native_collectors_preserve_declared_capture_order_for_selection(pipeline, source_format):
    rows = []
    for case in range(3):
        if source_format == 'codex':
            rows += [dict(type='session_meta', payload=dict(id=str(case), cwd=str(pipeline.project))),
                     dict(payload=dict(type='function_call', name='read', call_id=str(case))),
                     dict(payload=dict(type='function_call_output', call_id=str(case), output=dict(exit_code=0)))]
        else:
            rows += [dict(sessionId=str(case), uuid=f'call-{case}', type='assistant',
                          message=dict(content=[dict(type='tool_use', id=str(case), name='Read')])),
                     dict(sessionId=str(case), uuid=f'result-{case}', type='user',
                          message=dict(content=[dict(type='tool_result', tool_use_id=str(case), is_error=False)]))]
    code, receipt, _ = pipeline.invoke(rows=rows, extra=['--format', source_format,
        '--output', str(pipeline.root/f'output-{source_format}')])
    assert code == 0
    methods = {row['id']: row for row in receipt['analysis']['algorithms']}
    assert methods['directly_follows']['selected']
    assert methods['recurring_sequences']['selected']
    assert methods['structured_checks']['selected']
    assert not methods['duplicate_observations']['selected']
    assert receipt['analysis']['method_selection']['inputs']['capture_order_cases'] == 3


def test_controlled_preparation_receives_graph_but_no_case_identity_or_body(pipeline):
    data = collect(pipeline.project, events=pipeline.capture(pipeline.rows()))
    prepared = views(data, assets(), pipeline.root/'roles', time.monotonic()+30)
    for folder in prepared.values():
        summary = (folder/'analysis-summary.json').read_text()
        assert len(json.loads(summary)['process_mining']['raw_edges']) == 2
        assert json.loads(summary)['method_execution']['policy'] == 'structural_prerequisites_v1'
        assert json.loads(summary)['method_inputs']['sequence_roots'] == 3
        assert 'PRIVATE_CASE' not in summary
        assert 'PRIVATE_BODY' not in summary


def test_progress_precedes_end_of_discovery_and_interruption_never_says_complete(pipeline):
    stream = io.StringIO()
    ticks = iter(range(100))
    progress = _progress_reporter(stream, clock=lambda: next(ticks))
    def discovery(root):
        assert 'discovery' in stream.getvalue()
        raise KeyboardInterrupt()
        yield
    with patch('agent_harness_lab.evidence._capture_candidates', side_effect=discovery):
        with pytest.raises(KeyboardInterrupt):
            collect(pipeline.project, progress=progress)
    assert 'complete' not in stream.getvalue()
    with pytest.raises(ValueError):
        progress('collection', path='PRIVATE')


def test_progress_does_not_change_selection_or_fingerprint(pipeline):
    capture = pipeline.capture(pipeline.rows())
    initial = collect(pipeline.project, events=capture)
    calls = []
    actual = collect(pipeline.project, events=capture, progress=lambda stage, **counts: calls.append((stage, counts)))
    assert initial == actual
    assert calls[-1][0] == 'collection_complete'
    assert any(stage == 'collection' and counts['events'] < 9 for stage, counts in calls)


def test_discovery_heartbeats_continue_before_late_project_metadata(pipeline):
    captures = pipeline.root/'codex/sessions'; captures.mkdir(parents=True)
    rows = [dict(type='event_msg', payload=dict(type='unknown')) for _ in range(20)]
    rows.append(dict(type='session_meta', payload=dict(id='synthetic', cwd=str(pipeline.project))))
    (captures/'late-scope.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    calls = []
    collect(pipeline.project, progress=lambda stage, **counts: calls.append((stage, counts)))
    heartbeats = [counts for stage, counts in calls if stage == 'discovery' and 'captures' in counts]
    assert len(heartbeats) == len(rows)
    assert all(counts['captures'] == 0 for counts in heartbeats)


def test_workflow_evidence_retains_explicit_progress_callback(pipeline):
    from agent_harness_lab.workflow import evidence
    output = pipeline.root/'explicit-progress'; output.mkdir()
    args = SimpleNamespace(project=pipeline.project, events=pipeline.capture(pipeline.rows()), format='normalized')
    calls = []
    data = evidence(args, output, progress=lambda stage, **counts: calls.append((stage, counts)))
    assert calls[-1][0] == 'collection_complete'
    assert calls[-1][1]['events'] == data['analysis']['events']


def test_progress_io_failure_propagates_instead_of_truncating_readable_capture(pipeline):
    capture = pipeline.capture(pipeline.rows())
    heartbeats = 0
    def progress(stage, **counts):
        nonlocal heartbeats
        if stage == 'collection':
            heartbeats += 1
            if heartbeats == 3:
                raise OSError('synthetic progress sink failed')
    with pytest.raises(OSError, match='synthetic progress sink failed'):
        collect(pipeline.project, events=capture, progress=progress)


@pytest.mark.parametrize('failure', ['open', 'read'])
def test_capture_open_and_read_errors_remain_incomplete_evidence(pipeline, failure):
    capture = pipeline.capture(pipeline.rows())
    class FailedRead(io.BytesIO):
        def readline(self, *args):
            raise OSError('synthetic source read failed')
    options = dict(side_effect=OSError('synthetic source open failed')) if failure == 'open' else dict(return_value=FailedRead())
    with patch.object(Path, 'open', **options):
        data = collect(pipeline.project, events=capture)
    assert data['analysis']['events'] == 0
    assert 'Unreadable capture files were skipped; evidence is incomplete.' in data['caveats']


def test_bounded_multifile_discovery_reports_incomplete_and_progress_before_last_file(pipeline):
    captures = pipeline.root/'codex/sessions'
    captures.mkdir(parents=True)
    for number in range(3):
        (captures/f'{number}.jsonl').write_text(json.dumps(dict(type='session_meta',
            payload=dict(id=str(number), cwd=str(pipeline.project))))+'\n')
    observed = []
    def progress(stage, **counts):
        observed.append((stage, counts))
    data = collect(pipeline.project, max_scan_files=1, progress=progress)
    assert any('scan-file limit' in caveat for caveat in data['caveats'])
    assert any(stage == 'discovery' and counts.get('files') == 1 for stage, counts in observed)
    assert any(stage == 'discovery' and counts.get('files') == 2 for stage, counts in observed)
    assert observed[-1][0] == 'evidence_incomplete'


def test_all_malformed_records_are_incomplete_rather_than_clean_empty_history(pipeline):
    capture = pipeline.capture([]); capture.write_text('malformed\n')
    _, receipt, _ = pipeline.invoke(extra=['--events', str(capture)])
    assert receipt['analysis']['evidence_state'] == 'incomplete'
    assert receipt['analysis']['events'] == 0


def test_mine_remains_usable_in_installed_layout(pipeline):
    capture = pipeline.capture([dict(case_id='s', event_id=str(i), activity=a) for i, a in enumerate(('read', 'check'))])
    result = subprocess.run([sys.executable, '-m', 'agent_harness_lab', 'mine', str(capture)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    graph = json.loads(result.stdout)
    assert graph['raw_edges'][0]['source'] == 'read'
    assert graph['reported_roots'] == 1

