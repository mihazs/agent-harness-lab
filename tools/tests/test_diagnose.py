import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('diagnose', Path(__file__).parents[1] / 'diagnose.py')
d = importlib.util.module_from_spec(spec)
spec.loader.exec_module(d)


def event(case, ident, **extra):
    return dict(case_id=case, event_id=str(ident), activity='tool_result', **extra)


class DiagnosisTests(unittest.TestCase):
    def test_duplicate_observation_requires_state_and_is_not_waste(self):
        signature = dict(tool_name='read', input_fingerprint='i', result_fingerprint='r', state_revision='s')
        result = d.analyze([event('a', 1, **signature), event('a', 2, **signature)])
        self.assertEqual(result['findings'][0]['status'], 'observed_pattern')
        signature['state_revision'] = 'new'
        self.assertEqual(d.analyze([event('a', 1), event('a', 2, **signature)])['findings'], [])

    def test_related_cases_do_not_inflate_support(self):
        rows = [event(str(i), j, root_case_id='root') for i in range(4) for j in range(3)]
        self.assertEqual(d.analyze(rows)['recurring_sequences'], [])

    def test_invalid_duplicate_and_limits_fail(self):
        for rows in ([None], [{}], [event('a', 1), event('a', 1)]):
            with self.assertRaises(ValueError): d.analyze(rows)
        with self.assertRaises(ValueError): d.analyze([event('a', 1), event('a', 2)], 1)

    def test_native_normalization_does_not_export_bodies(self):
        rows = list(d.normalize([dict(kind='stop', body='SECRET', session_id='s')], 'ai-memory', 'default/project'))
        self.assertNotIn('SECRET', str(rows))
        self.assertEqual(rows[0]['activity'], 'turn_stop')
        self.assertEqual(d.analyze(rows)['findings'], [])

    def test_native_session_metadata_preserves_case_identity(self):
        rows = list(d.normalize([{'type': 'session_meta', 'payload': {'id': 's'}},
                               {'type': 'response_item', 'payload': {'type': 'function_call', 'name': 'read'}}], 'codex', 'default/project'))
        self.assertTrue(all(r['case_id'] == 's' for r in rows))
        self.assertEqual(rows[1]['tool_name'], 'read')

    def test_interleaving_does_not_create_sequences(self):
        self.assertEqual(d.analyze([event('a', 1), event('b', 1)])['recurring_sequences'], [])

    def test_claude_tools_are_linked_without_content_or_thinking(self):
        rows = [dict(type='assistant', uuid='a', sessionId='session', message={'content':[
            {'type':'thinking','thinking':'PRIVATE'},
            {'type':'tool_use','id':'call','name':'Read','input':{'path':'PRIVATE'}}]}),
            dict(type='user', uuid='b', sessionId='session', message={'content':[
            {'type':'tool_result','tool_use_id':'call','content':'PRIVATE','is_error':True}]}),
            dict(type='assistant',uuid='c',sessionId='other',message={'content':'PRIVATE'})]
        normalized=list(d.normalize(rows,'claude','synthetic'))
        self.assertEqual([e['activity'] for e in normalized], ['tool_call','tool_result','assistant'])
        self.assertEqual([e['call_id'] for e in normalized[:2]], ['call','call'])
        self.assertEqual(normalized[0]['event_id'], 'a:1')
        self.assertNotIn('PRIVATE', str(normalized))
        self.assertEqual(d.analyze(normalized)['cases'], 2)
        self.assertEqual(d.analyze(normalized)['findings'], [])
