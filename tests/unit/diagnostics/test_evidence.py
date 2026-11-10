import hashlib
import itertools
import json
import os

import pytest

from agent_harness_lab.evidence import collect

FORMER_RECORD_BYTES = 1024 * 1024


class TestEvidence:
    def test_default_discovery_has_no_scan_accepted_file_or_scope_prefix_ceiling(self):
        for number in range(2001):
            self.write(self.codex/f'sessions/foreign-{number}.jsonl',self.codex_rows(self.home/'foreign'))
        for number in range(101):
            rows=[dict(type='metadata')]*101+self.codex_rows()
            self.write(self.codex/f'sessions/owned-{number}.jsonl',rows)
        result=collect(self.project)
        assert len(result['sources']) == 101
        assert result['discovery_limits']['scan_files'] is None
        assert result['discovery_limits']['accepted_files'] is None
        limited=collect(self.project,max_files=2)
        assert len(limited['sources']) == 2

    @pytest.fixture(autouse=True)
    def _home(self, tmp_path, monkeypatch):
        self.home = tmp_path
        self.project = self.home / 'work'
        self.project.mkdir()
        self.codex = self.home / 'codex'
        self.claude = self.home / 'claude'
        monkeypatch.setenv('CODEX_HOME', str(self.codex))
        monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(self.claude))

    def write(self, path, rows):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def codex_rows(self, cwd=None, code=1):
        return [dict(type='session_meta', payload=dict(id='session', cwd=str(cwd or self.project))),
                dict(type='response_item', payload=dict(type='function_call', name='shell', call_id='call', arguments='PRIVATE')),
                dict(type='response_item', payload=dict(type='function_call_output', call_id='call', output=dict(exit_code=code, stdout='PRIVATE')))]

    def test_exact_project_scope_and_metadata_only(self):
        self.write(self.codex/'sessions/own.jsonl', self.codex_rows())
        self.write(self.codex/'sessions/foreign.jsonl', self.codex_rows(self.home/'other'))
        result = collect(self.project)
        assert len(result['sources']) == 1
        assert result['analysis']['cases'] == 1
        assert result['analysis']['activities']['tool_result'] == 1
        assert result['analysis']['findings'][0]['kind'] == 'tool_error'
        assert 'PRIVATE' not in str(result)
        assert 'other' not in str(result)

    def test_claude_errors_are_observations_not_task_outcomes(self):
        self.write(self.claude/'projects/example/session.jsonl', [
            dict(type='assistant', cwd=str(self.project), sessionId='s', uuid='a', message={'content':[
                {'type':'thinking', 'thinking':'PRIVATE'},
                {'type':'tool_use','id':'call','name':'Read','input':{'path':'PRIVATE'}}]}),
            dict(type='user', cwd=str(self.project), sessionId='s', uuid='b', message={'content':[
                {'type':'tool_result','tool_use_id':'call','is_error':True,'content':'PRIVATE'}]})])
        result = collect(self.project)
        assert 'PRIVATE' not in str(result)
        assert result['analysis']['findings'][0]['status'] == 'observed_pattern'
        assert result['analysis']['cases'] == 1
        assert result['analysis']['activities']['tool_call'] == 1

    def test_fingerprint_changes_with_evidence_not_reading(self):
        p=self.write(self.codex/'sessions/own.jsonl', self.codex_rows(code=0))
        first=collect(self.project)['fingerprint']
        assert first == collect(self.project)['fingerprint']
        self.write(p,self.codex_rows(code=1))
        assert first != collect(self.project)['fingerprint']

    def test_evaluation_failures_and_owned_workflow_exclusion(self):
        rows={'trials':[{'task':'synthetic','version':'candidate','status':'failed'},
                        {'task':'ok','version':'baseline','status':'passed'}]}
        for folder in ('prior','owned'):
            p=self.project/'artifacts'/folder
            p.mkdir(parents=True)
            (p/'results.json').write_text(json.dumps(rows))
        (self.project/'artifacts/owned/workflow.json').write_text('{}')
        result=collect(self.project)
        assert len(result['evaluation_failures']) == 1
        assert result['evaluation_failures'][0]['status'] == 'failed'
        assert 'owned' not in str(result['sources'])

    def test_explicit_events_does_not_discover_other_sources(self):
        self.write(self.codex/'sessions/own.jsonl',self.codex_rows())
        path=self.write(self.home/'selected.jsonl',[dict(case_id='case',event_id='1',activity='check',body='PRIVATE')])
        result=collect(self.project,events=path)
        assert len(result['sources']) == 1
        assert result['analysis']['events'] == 1
        assert result['analysis']['findings'] == []
        assert 'PRIVATE' not in str(result)

    def test_malformed_records_are_explicit_without_a_size_ceiling(self):
        p=self.write(self.codex/'sessions/own.jsonl', self.codex_rows())
        with p.open('a') as f:
            f.write('not json\n')
            f.write('x'*(FORMER_RECORD_BYTES+1)+'\n')
        result=collect(self.project)
        assert any('Malformed' in c for c in result['caveats'])
        assert not any('Oversized' in c for c in result['caveats'])
        with pytest.raises(ValueError): collect(self.project,max_events=1)

    def test_symlinks_and_changed_cwd_are_excluded(self):
        rows=self.codex_rows()
        rows.insert(1,dict(type='turn_context',payload={'cwd':str(self.home/'other')}))
        self.write(self.codex/'sessions/own.jsonl', rows)
        target=self.write(self.home/'outside.jsonl',self.codex_rows())
        (self.codex/'sessions/link.jsonl').symlink_to(target)
        result=collect(self.project)
        assert result['analysis']['events'] == 1
        assert result['analysis']['findings'] == []

    def test_bounded_latest_candidates_report_incomplete_selection(self):
        self.write(self.codex/'sessions/old.jsonl',self.codex_rows())
        self.write(self.codex/'sessions/new.jsonl',self.codex_rows())
        result=collect(self.project,max_files=1)
        assert len(result['sources']) == 1
        assert any('file limit' in c for c in result['caveats'])

    def test_codex_error_text_alone_is_not_an_error_label(self):
        rows=self.codex_rows()
        rows[-1]['payload']['output']='Process failed exit code 1 PRIVATE'
        self.write(self.codex/'sessions/own.jsonl',rows)
        assert collect(self.project)['analysis']['findings'] == []

    def test_same_native_session_in_distinct_files_is_one_root(self):
        for name in ('one','two','three'):
            self.write(self.codex/f'sessions/{name}.jsonl',self.codex_rows())
        result=collect(self.project)
        assert result['analysis']['cases'] == 3
        assert result['analysis']['reported_roots'] == 1
        assert result['analysis']['recurring_sequences'] == []

    def test_long_valid_case_ids_do_not_collide(self):
        path=self.write(self.home/'events.jsonl',[
            dict(case_id='x'*230+suffix,event_id='1',activity='inspect') for suffix in ('a','b')])
        result=collect(self.project,events=path)
        assert result['analysis']['cases'] == 2
        assert result['analysis']['events'] == 2

    def test_body_only_changes_do_not_retrigger_monitor(self):
        rows=self.codex_rows()
        path=self.write(self.codex/'sessions/own.jsonl',rows)
        first=collect(self.project)
        rows[1]['payload']['arguments']='CHANGED PRIVATE INPUT'
        rows[2]['payload']['output']['stdout']='CHANGED PRIVATE RESULT'
        self.write(path,rows)
        second=collect(self.project)
        assert first['fingerprint'] == second['fingerprint']
        assert first['sources'][0]['sha256'] != second['sources'][0]['sha256']

    def test_excluded_foreign_rows_never_change_watch_fingerprint(self):
        rows=self.codex_rows()
        rows += [dict(type='turn_context',payload={'cwd':str(self.home/'other')}),
                 dict(type='response_item',payload={'type':'function_call','name':'PRIVATE'})]
        path=self.write(self.codex/'sessions/own.jsonl',rows)
        first=collect(self.project)['fingerprint']
        rows[-1]['payload']['name']='CHANGED PRIVATE'
        self.write(path,rows)
        assert first == collect(self.project)['fingerprint']

    def test_evaluation_body_and_timing_do_not_change_watch_fingerprint(self):
        path=self.project/'artifacts/prior/results.json'
        path.parent.mkdir(parents=True)
        result={'manifest_sha256':'manifest','trials':[{'task':'test','version':'candidate',
                'status':'failed','grader':{'exit_code':1,'stdout':'PRIVATE','elapsed_seconds':1}}]}
        path.write_text(json.dumps(result))
        first=collect(self.project)['fingerprint']
        result['trials'][0]['grader'].update(stdout='CHANGED PRIVATE',elapsed_seconds=20)
        path.write_text(json.dumps(result))
        assert first == collect(self.project)['fingerprint']
        result['trials'][0]['status']='passed'
        path.write_text(json.dumps(result))
        assert first != collect(self.project)['fingerprint']

    def test_streaming_past_former_byte_ceiling_skips_malformed_records_and_hashes_tail(self):
        path=self.home/'large.jsonl';digest=hashlib.sha256()
        first=json.dumps(dict(case_id='synthetic',event_id='1',activity='inspect')).encode()+b'\n'
        second=json.dumps(dict(case_id='synthetic',event_id='2',activity='check')).encode()+b'\n'
        block=b'x'*FORMER_RECORD_BYTES
        with path.open('wb') as stream:
            for raw in itertools.chain((first,),itertools.repeat(block,65),(b'\n',second,block,b'y')):
                stream.write(raw);digest.update(raw)
        assert path.stat().st_size > 64*1024*1024
        result=collect(self.project,events=path,max_events=2)
        assert result['analysis']['events'] == 2
        assert result['sources'][0]['sha256'] == digest.hexdigest()
        assert sum('Malformed' in caveat for caveat in result['caveats']) == 1
        assert 'Oversized' not in ' '.join(result['caveats'])

    def test_streaming_past_former_chunk_ceiling_retains_valid_events_after_malformed_rows(self):
        path=self.home/'many-rows.jsonl';digest=hashlib.sha256()
        block=b'not-json\n'*8192
        valid=json.dumps(dict(case_id='synthetic',event_id='1',activity='inspect')).encode()+b'\n'
        assert 62*8192 > 500000
        with path.open('wb') as stream:
            for raw in itertools.chain(itertools.repeat(block,62),(valid,)):
                stream.write(raw);digest.update(raw)
        result=collect(self.project,events=path,max_events=1)
        assert result['analysis']['events'] == 1
        assert result['sources'][0]['sha256'] == digest.hexdigest()
        assert sum('Malformed' in caveat for caveat in result['caveats']) == 1

    def test_hashing_does_not_repair_invalid_normalized_case_ids(self):
        for case in (None, 17, ''):
            path=self.write(self.home/'invalid.jsonl',[dict(case_id=case,event_id='1',activity='inspect')])
            with pytest.raises(ValueError, match='case identity'):
                collect(self.project,events=path)

    def test_unrelated_new_captures_do_not_consume_accepted_limit(self):
        own = self.write(self.codex/'sessions/own.jsonl', self.codex_rows())
        os.utime(own, (1, 1))
        for i in range(5):
            self.write(self.codex/f'sessions/foreign-{i}.jsonl', self.codex_rows(self.home/'other'))
        result = collect(self.project, max_files=1, max_scan_files=10)
        assert len(result['sources']) == 1
        assert result['sources'][0]['path'] == str(own)
        assert collect(self.project, max_scan_files=1)['caveats']

    def test_success_controls_are_retained_without_invented_outcomes(self):
        path = self.project/'artifacts/run/results.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'trials': [dict(task='ok', version='baseline', status='passed')]}))
        result = collect(self.project)
        assert len(result['evaluation_successes']) == 1
        assert result['evaluation_successes'][0]['outcome'] == 'unknown'
        assert result['evaluation_failures'] == []

    def test_typed_grade_outcomes_keep_provenance_and_reject_contradictions(self):
        path=self.project/'artifacts/typed/results.json'; path.parent.mkdir(parents=True)
        trial=dict(task='synthetic',root='reported-root',version='candidate',status='passed',
            execution=dict(exit_code=0,timeout=False),
            grader=dict(exit_code=0,timeout=False,valid=True,outcome=True,grader_id='synthetic',grader_version='v1'))
        result=dict(inputs_unchanged=True,trials=[trial])
        path.write_text(json.dumps(result)); first=collect(self.project)
        outcome=first['evaluation_successes'][0]
        assert outcome['outcome'] == 'success'
        assert outcome['outcome_provenance'] == 'executable_grader'
        assert outcome['root_id'] == 'reported-root'; assert not outcome['lineage_verified']
        trial['grader']['outcome']=False
        path.write_text(json.dumps(result)); second=collect(self.project)
        assert second['evaluation_successes'][0]['outcome'] == 'unknown'
        assert first['fingerprint'] != second['fingerprint']
