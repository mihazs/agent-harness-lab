import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_harness_lab.evidence import collect, MAX_RECORD_BYTES


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.project = self.home / 'work'
        self.project.mkdir()
        self.codex = self.home / 'codex'
        self.claude = self.home / 'claude'
        self.env = patch.dict(os.environ, CODEX_HOME=str(self.codex), CLAUDE_CONFIG_DIR=str(self.claude))
        self.env.start()
        self.addCleanup(self.env.stop)

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
        self.assertEqual(len(result['sources']), 1)
        self.assertEqual(result['analysis']['cases'], 1)
        self.assertEqual(result['analysis']['activities']['tool_result'], 1)
        self.assertEqual(result['analysis']['findings'][0]['kind'], 'tool_error')
        self.assertNotIn('PRIVATE', str(result))
        self.assertNotIn('other', str(result))

    def test_claude_errors_are_observations_not_task_outcomes(self):
        self.write(self.claude/'projects/example/session.jsonl', [
            dict(type='assistant', cwd=str(self.project), sessionId='s', uuid='a', message={'content':[
                {'type':'thinking', 'thinking':'PRIVATE'},
                {'type':'tool_use','id':'call','name':'Read','input':{'path':'PRIVATE'}}]}),
            dict(type='user', cwd=str(self.project), sessionId='s', uuid='b', message={'content':[
                {'type':'tool_result','tool_use_id':'call','is_error':True,'content':'PRIVATE'}]})])
        result = collect(self.project)
        self.assertNotIn('PRIVATE', str(result))
        self.assertEqual(result['analysis']['findings'][0]['status'], 'observed_pattern')
        self.assertEqual(result['analysis']['cases'], 1)
        self.assertEqual(result['analysis']['activities']['tool_call'], 1)

    def test_fingerprint_changes_with_evidence_not_reading(self):
        p=self.write(self.codex/'sessions/own.jsonl', self.codex_rows(code=0))
        first=collect(self.project)['fingerprint']
        self.assertEqual(first,collect(self.project)['fingerprint'])
        self.write(p,self.codex_rows(code=1))
        self.assertNotEqual(first,collect(self.project)['fingerprint'])

    def test_evaluation_failures_and_owned_workflow_exclusion(self):
        rows={'trials':[{'task':'synthetic','version':'candidate','status':'failed'},
                        {'task':'ok','version':'baseline','status':'passed'}]}
        for folder in ('prior','owned'):
            p=self.project/'artifacts'/folder
            p.mkdir(parents=True)
            (p/'results.json').write_text(json.dumps(rows))
        (self.project/'artifacts/owned/workflow.json').write_text('{}')
        result=collect(self.project)
        self.assertEqual(len(result['evaluation_failures']),1)
        self.assertEqual(result['evaluation_failures'][0]['status'],'failed')
        self.assertNotIn('owned',str(result['sources']))

    def test_explicit_events_does_not_discover_other_sources(self):
        self.write(self.codex/'sessions/own.jsonl',self.codex_rows())
        path=self.write(self.home/'selected.jsonl',[dict(case_id='case',event_id='1',activity='check',body='PRIVATE')])
        result=collect(self.project,events=path)
        self.assertEqual(len(result['sources']),1)
        self.assertEqual(result['analysis']['events'],1)
        self.assertEqual(result['analysis']['findings'],[])
        self.assertNotIn('PRIVATE',str(result))

    def test_malformed_and_oversized_records_are_explicit(self):
        p=self.write(self.codex/'sessions/own.jsonl', self.codex_rows())
        with p.open('a') as f:
            f.write('not json\n')
            f.write('x'*(MAX_RECORD_BYTES+1)+'\n')
        result=collect(self.project)
        self.assertTrue(any('Malformed' in c for c in result['caveats']))
        self.assertTrue(any('Oversized' in c for c in result['caveats']))
        with self.assertRaises(ValueError): collect(self.project,max_events=1)

    def test_symlinks_and_changed_cwd_are_excluded(self):
        rows=self.codex_rows()
        rows.insert(1,dict(type='turn_context',payload={'cwd':str(self.home/'other')}))
        self.write(self.codex/'sessions/own.jsonl', rows)
        target=self.write(self.home/'outside.jsonl',self.codex_rows())
        (self.codex/'sessions/link.jsonl').symlink_to(target)
        result=collect(self.project)
        self.assertEqual(result['analysis']['events'],1)
        self.assertEqual(result['analysis']['findings'],[])

    def test_bounded_latest_candidates_report_incomplete_selection(self):
        self.write(self.codex/'sessions/old.jsonl',self.codex_rows())
        self.write(self.codex/'sessions/new.jsonl',self.codex_rows())
        result=collect(self.project,max_files=1)
        self.assertEqual(len(result['sources']),1)
        self.assertTrue(any('file limit' in c for c in result['caveats']))

    def test_codex_error_text_alone_is_not_an_error_label(self):
        rows=self.codex_rows()
        rows[-1]['payload']['output']='Process failed exit code 1 PRIVATE'
        self.write(self.codex/'sessions/own.jsonl',rows)
        self.assertEqual(collect(self.project)['analysis']['findings'],[])

    def test_same_native_session_in_distinct_files_is_one_root(self):
        for name in ('one','two','three'):
            self.write(self.codex/f'sessions/{name}.jsonl',self.codex_rows())
        result=collect(self.project)
        self.assertEqual(result['analysis']['cases'],3)
        self.assertEqual(result['analysis']['reported_roots'],1)
        self.assertEqual(result['analysis']['recurring_sequences'],[])

    def test_long_valid_case_ids_do_not_collide(self):
        path=self.write(self.home/'events.jsonl',[
            dict(case_id='x'*230+suffix,event_id='1',activity='inspect') for suffix in ('a','b')])
        result=collect(self.project,events=path)
        self.assertEqual(result['analysis']['cases'],2)
        self.assertEqual(result['analysis']['events'],2)

    def test_body_only_changes_do_not_retrigger_monitor(self):
        rows=self.codex_rows()
        path=self.write(self.codex/'sessions/own.jsonl',rows)
        first=collect(self.project)
        rows[1]['payload']['arguments']='CHANGED PRIVATE INPUT'
        rows[2]['payload']['output']['stdout']='CHANGED PRIVATE RESULT'
        self.write(path,rows)
        second=collect(self.project)
        self.assertEqual(first['fingerprint'],second['fingerprint'])
        self.assertNotEqual(first['sources'][0]['sha256'],second['sources'][0]['sha256'])

    def test_excluded_foreign_rows_never_change_watch_fingerprint(self):
        rows=self.codex_rows()
        rows += [dict(type='turn_context',payload={'cwd':str(self.home/'other')}),
                 dict(type='response_item',payload={'type':'function_call','name':'PRIVATE'})]
        path=self.write(self.codex/'sessions/own.jsonl',rows)
        first=collect(self.project)['fingerprint']
        rows[-1]['payload']['name']='CHANGED PRIVATE'
        self.write(path,rows)
        self.assertEqual(first,collect(self.project)['fingerprint'])

    def test_evaluation_body_and_timing_do_not_change_watch_fingerprint(self):
        path=self.project/'artifacts/prior/results.json'
        path.parent.mkdir(parents=True)
        result={'manifest_sha256':'manifest','trials':[{'task':'test','version':'candidate',
                'status':'failed','grader':{'exit_code':1,'stdout':'PRIVATE','elapsed_seconds':1}}]}
        path.write_text(json.dumps(result))
        first=collect(self.project)['fingerprint']
        result['trials'][0]['grader'].update(stdout='CHANGED PRIVATE',elapsed_seconds=20)
        path.write_text(json.dumps(result))
        self.assertEqual(first,collect(self.project)['fingerprint'])
        result['trials'][0]['status']='passed'
        path.write_text(json.dumps(result))
        self.assertNotEqual(first,collect(self.project)['fingerprint'])

    def test_raw_read_budget_bounds_malformed_and_oversized_tails(self):
        path=self.write(self.codex/'sessions/own.jsonl',self.codex_rows())
        with path.open('a') as stream:
            stream.write('not-json\n'*1000)
        with patch('agent_harness_lab.evidence.MAX_READ_CHUNKS',20):
            with self.assertRaisesRegex(ValueError,'read budget'):
                collect(self.project)
        self.write(path,self.codex_rows())
        with path.open('a') as stream:
            stream.write('x'*(MAX_RECORD_BYTES+1)+'\n')
        with patch('agent_harness_lab.evidence.MAX_READ_BYTES',1024):
            with self.assertRaisesRegex(ValueError,'read budget'):
                collect(self.project)

    def test_hashing_does_not_repair_invalid_normalized_case_ids(self):
        for case in (None, 17, '', 'x'*257):
            path=self.write(self.home/'invalid.jsonl',[dict(case_id=case,event_id='1',activity='inspect')])
            with self.assertRaisesRegex(ValueError,'case identity'):
                collect(self.project,events=path)
