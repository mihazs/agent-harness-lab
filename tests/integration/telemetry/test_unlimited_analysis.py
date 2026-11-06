"""Unrestricted synthetic analysis with explicit-limit and scope controls."""
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import agent_harness_lab
from agent_harness_lab.evidence import collect
from agent_harness_lab.telemetry.ledger import Ledger
from tools.diagnose import analyze
from tools.observations import ObservationCache


def event(case, ident, activity='metadata', **fields):
    return dict(case_id=case,event_id=str(ident),activity=activity,**fields)


class UnlimitedAnalysisTests(unittest.TestCase):
    def setUp(self):
        expected=os.environ.get('AHL_EXPECTED_PACKAGE_ROOT')
        if expected:self.assertTrue(Path(agent_harness_lab.__file__).resolve().is_relative_to(Path(expected).resolve()))
        home=tempfile.TemporaryDirectory();self.addCleanup(home.cleanup)
        self.synthetic_home=Path(home.name)
        environment=patch.dict(os.environ,dict(CODEX_HOME=str(self.synthetic_home/'codex'),
            CLAUDE_CONFIG_DIR=str(self.synthetic_home/'claude')))
        environment.start();self.addCleanup(environment.stop)

    def test_long_metadata_identities_and_observation_dimensions_remain_complete(self):
        from agent_harness_lab.telemetry.schema import event as normalized
        text='synthetic-'+('long-id-'*600)
        dimensions=dict(root_case_id=text+'root',tool_name=text+'tool',input_fingerprint=text+'input',
            result_fingerprint=text+'result',state_revision=text+'state')
        rows=[event(text,text+str(i),'tool_result',body='SYNTHETIC_PRIVATE_BODY',**dimensions) for i in range(2)]
        rows.append(event(text,text+'activity',text+'activity',root_case_id=text+'root'))
        cleaned=[normalized(row) for row in rows]
        self.assertNotIn('SYNTHETIC_PRIVATE_BODY',json.dumps(cleaned))
        self.assertEqual(cleaned[0]['event_id'],text+'0')
        self.assertEqual(cleaned[0]['root_case_id'],text+'root')
        result=analyze(cleaned)
        self.assertEqual(result['events'],3)
        self.assertEqual(result['findings'][0]['evidence'],[dict(case_id=text,event_id=text+str(i)) for i in range(2)])
        self.assertIn(text+'activity',result['activities'])
        ledger=Ledger(self.synthetic_home/'long-events.jsonl')
        self.assertEqual(ledger.ingest(cleaned),3)
        self.assertEqual(json.loads(ledger.path.read_text().splitlines()[0])['event_id'],text+'0')

    def test_unpinned_public_normalization_preserves_all_pending_and_finished_calls(self):
        root=self.synthetic_home;capture=root/'events.jsonl'
        rows=[dict(type='session_meta',payload=dict(id='synthetic'))]
        rows.extend(dict(type='response_item',id='call-'+str(i),payload=dict(type='function_call',call_id=str(i),name='read')) for i in range(4097))
        rows.extend(dict(type='response_item',id='result-'+str(i),payload=dict(type='function_call_output',call_id=str(i),output=dict(exit_code=0))) for i in range(4097))
        rows.append(dict(type='response_item',id='duplicate',payload=dict(type='function_call_output',call_id='0',output=dict(exit_code=0))))
        capture.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        result=subprocess.run([sys.executable,'-m','agent_harness_lab','analyze',str(root),'--events',str(capture),
            '--format','codex','--source-version','codex-jsonl-v1','--output',str(root/'output')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        projected=[json.loads(line) for line in (root/'output/events.v2.jsonl').read_text().splitlines()]
        results=[row for row in projected if row['activity']=='tool_result']
        self.assertEqual(sum(row['capture_status']=='linked' for row in results),4097)
        self.assertEqual(results[-1]['capture_status'],'duplicate_result')
        self.assertNotIn('pending_limit',{row['capture_status'] for row in projected})
        from agent_harness_lab.telemetry.adapters import correlate
        bounded=list(correlate([event('synthetic',1,'tool_call',call_id='a'),event('synthetic',2,'tool_call',call_id='b'),
            event('synthetic',3,'tool_result',call_id='a')],max_pending=1))
        self.assertIn('pending_limit',{row['capture_status'] for row in bounded})
        self.assertIn('unmatched_result',{row['capture_status'] for row in bounded})

    def test_public_analyze_default_and_explicit_event_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'events.jsonl'
            capture.write_text(''.join(json.dumps(event('synthetic',i))+'\n' for i in range(2)))
            argv=[sys.executable,'-m','agent_harness_lab','analyze',str(root),'--events',str(capture)]
            result=subprocess.run([*argv,'--output',str(root/'complete')],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads((root/'complete/analysis.json').read_text())['analysis']['events'],2)
            self.assertEqual(len((root/'complete/events.v2.jsonl').read_text().splitlines()),2)
            limited=subprocess.run([*argv,'--output',str(root/'limited'),'--max-events','1'],capture_output=True,text=True)
            self.assertNotEqual(limited.returncode,0)
            self.assertIn('event limit exceeded',limited.stderr)

    def test_large_valid_record_and_deep_evaluation_report_are_not_size_filtered(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);capture=root/'events.jsonl'
            raw=(json.dumps(event('synthetic','1',body='x'*(1024*1024+1)))+'\n').encode()
            capture.write_bytes(raw)
            result=collect(root,events=capture)
            self.assertEqual(result['analysis']['events'],1)
            self.assertEqual(result['sources'][0]['sha256'],hashlib.sha256(raw).hexdigest())
            self.assertNotIn('x'*100,str(result))
            report=root/'artifacts/a/b/c/d/results.json';report.parent.mkdir(parents=True)
            report.write_text(json.dumps(dict(padding='x'*(1024*1024+1),trials=[dict(task='synthetic',status='failed')])))
            result=collect(root)
            self.assertEqual(len(result['evaluation_failures']),1)

    def test_more_than_former_default_event_limit_and_explicit_limit(self):
        self.assertEqual(analyze(event('synthetic',i) for i in range(100001))['events'],100001)
        with self.assertRaises(ValueError):analyze((event('synthetic',i) for i in range(3)),max_events=2)

    def test_all_recurring_lengths_and_evidence_match_brute_force(self):
        rng=random.Random(7);rows=[];by_case=defaultdict(list);roots={}
        for index in range(6):
            case=str(index);root=str(index//2);roots[case]=root
            for offset,activity in enumerate(['shared']*6+[rng.choice('abc') for _ in range(5)]):
                row=event(case,offset,activity,root_case_id=root);rows.append(row);by_case[case].append(row)
        rows=[by_case[str(case)][offset] for offset in range(11) for case in range(6)]
        positions={(row['case_id'],row['event_id']):index for index,row in enumerate(rows)}
        expected=defaultdict(lambda:dict(roots=set(),evidence=[]))
        for case,events in by_case.items():
            for end,row in enumerate(events):
                for length in range(2,end+2):
                    pattern=tuple(item['activity'] for item in events[end-length+1:end+1])
                    expected[pattern]['roots'].add(roots[case])
                    expected[pattern]['evidence'].append(dict(case_id=case,event_id=row['event_id']))
        expected=[dict(activities=list(pattern),reported_roots=len(value['roots']),evidence=value['evidence'])
            for pattern,value in expected.items() if len(value['roots'])>=3]
        for item in expected:item['evidence'].sort(key=lambda ref:positions[ref['case_id'],ref['event_id']])
        expected.sort(key=lambda item:(-item['reported_roots'],item['activities']))
        actual=analyze(rows)['recurring_sequences']
        self.assertEqual(actual,expected)
        self.assertTrue(any(len(item['activities'])>4 for item in actual))
        self.assertTrue(any(len(item['evidence'])>3 for item in actual))
        self.assertEqual(analyze(rows,sequence_length=4)['recurring_sequences'],
            [item for item in expected if len(item['activities'])<=4])

    def test_unlimited_observations_retain_long_range_matches_and_all_cases(self):
        signature=dict(tool_name='read',result_fingerprint='r',state_revision='s')
        cache=ObservationCache()
        for number in range(4097):
            cache.observe(event('synthetic',number,'tool_result',input_fingerprint=str(number),**signature),number)
        finding=cache.observe(event('synthetic','again','tool_result',input_fingerprint='0',**signature),5000)
        self.assertEqual(finding['evidence'][0]['event_id'],'0')
        for number in range(4097):cache.observe(event(str(number),number),6000+number)
        self.assertEqual(len(cache.cases),4098)
        self.assertFalse(any(cache.coverage[key] for key in ('case_eviction','age_eviction','per_case_eviction','total_eviction')))

    def test_ledger_defaults_are_unlimited_and_explicit_limits_preserve_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.jsonl';ledger=Ledger(path)
            self.assertIsNone(ledger.max_bytes);self.assertIsNone(ledger.max_records)
            self.assertEqual(ledger.ingest(event('synthetic',i) for i in range(100001)),100001)
            before=path.stat().st_size
            with self.assertRaises(ValueError):Ledger(path,max_records=100000).ingest([])
            with self.assertRaises(ValueError):Ledger(path,max_bytes=1).ingest([])
            self.assertEqual(path.stat().st_size,before)

    def test_invalid_explicit_limits_still_reject(self):
        for value in (0,-1,True,1.5):
            with self.assertRaises(ValueError):analyze([],max_events=value)
            with self.assertRaises(ValueError):ObservationCache(total=value)
            with self.assertRaises(ValueError):Ledger('unused',max_records=value)
