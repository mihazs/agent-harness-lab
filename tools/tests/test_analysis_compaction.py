"""Synthetic multi-root corpora: compact analysis state gives the frozen implementation's exact output."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import random
import tempfile
import tracemalloc
import unittest
from unittest.mock import patch

from agent_harness_lab.cli import main
import tools.diagnose as diagnose
if __package__:
    from . import diagnose_reference as reference
else:
    import diagnose_reference as reference

STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)
MOTIFS = (('tool_call', 'tool_result', 'assistant'), ('user_message', 'tool_call', 'tool_result', 'tool_call', 'tool_result'),
          ('assistant', 'check', 'tool_call', 'tool_result'), ('metadata', 'turn_stop'), ('tool_call', 'tool_result'))
MODES = ('capture', 'timestamp', 'both', 'late', 'broken')


def case_events(rng, case, root, steps):
    """One case; its mode decides which order evidence stays usable and for how long."""
    mode, events, pending, k = rng.choice(MODES), [], [], 0
    for _ in range(steps):
        motif = MOTIFS[rng.randrange(len(MOTIFS))] if rng.random() < 0.85 else (f'noise-{rng.randrange(4)}',)
        for activity in motif:
            k += 1
            event = dict(case_id=case, event_id=f'e{k}', activity=activity)
            if root is not None:
                event['root_case_id'] = root
            usable = mode in ('capture', 'both') or (mode == 'late' and k < steps)
            if usable or mode == 'timestamp':
                if mode != 'timestamp':
                    event['capture_order'] = k
            elif rng.random() < 0.7:
                event['capture_order'] = rng.choice((0, k - 3, None, 'x'))
            if mode in ('timestamp', 'both') or (mode == 'late' and k < steps):
                event['timestamp'] = (STAMP + timedelta(seconds=k)).isoformat().replace('+00:00', 'Z')
            elif rng.random() < 0.5:
                event['timestamp'] = rng.choice(('not-a-time', '2026-01-01T00:00:00Z', None))
            if activity == 'tool_call':
                call = rng.choice((f'call-{k}', f'call-{k}', None, 'call-reused'))
                if call is not None or rng.random() < 0.5:
                    event['call_id'] = call
                pending.append(call)
                event['tool_effect'] = rng.choice(('read_only', 'read_only', 'read_only', 'mutation', None))
            elif activity == 'tool_result':
                if pending and rng.random() < 0.9:
                    event['call_id'] = pending.pop()
                event.update(tool_name=rng.choice(('read', 'grep')), input_fingerprint=f'i{rng.randrange(3)}',
                             result_fingerprint=rng.choice(('r0', 'r0', 'r1', '')))
                if rng.random() < 0.85:
                    event['state_revision'] = f's{rng.randrange(2)}'
            if rng.random() < 0.04:
                event['mutation'] = True
            if rng.random() < 0.05:
                event.update(contract_violation=True, contract_ref=rng.choice(('contract-a', '')))
            if rng.random() < 0.08:
                event['execution_status'] = rng.choice(('error', 'returned'))
            if rng.random() < 0.3:
                event.update(outcome=rng.choice(('ok', 'unknown')), lineage_verified=rng.choice((True, 'unverified')))
            events.append(event)
    return events


def corpus(seed, roots, cases, steps, lineage=True):
    """Interleave cases randomly so cross-case observation limits see a mixed capture order."""
    rng = random.Random(seed)
    queues = [case_events(rng, f'case-{r}-{c}', f'root-{r}' if lineage else None, rng.randint(1, steps))
              for r in range(roots) for c in range(rng.randint(1, cases))]
    rows = []
    while queues:
        queue = queues[rng.randrange(len(queues))]
        rows.append(queue.pop(0))
        queues = [q for q in queues if q]
    return rows


def exportable(rows):
    """The same corpus within the strict v2 export schema, which rejects null, empty and malformed fields."""
    def valid(key, value):
        if key == 'capture_order':
            return type(value) is int and value >= 0
        return value is not None and value != '' and (key != 'lineage_verified' or type(value) is bool)
    return [{key: value for key, value in row.items() if valid(key, value)} for row in rows]


def outcome(module, rows, **options):
    try:
        return module.analyze(iter(rows), **options)
    except Exception as error:  # Both implementations must fail identically.
        return (type(error), str(error))


def sharing(result):
    """Which evidence lists and references are the same object, by first appearance."""
    first, shape = {}, []
    for row in [*result['recurring_sequences'], *result['findings']]:
        shape.append(first.setdefault(id(row['evidence']), len(first)))
        shape.extend(first.setdefault(id(ref), len(first)) for ref in row['evidence'])
    return shape


class AnalysisCompactionTests(unittest.TestCase):
    def assertEquivalent(self, rows, **options):
        expected, actual = outcome(reference, rows, **options), outcome(diagnose, rows, **options)
        if isinstance(expected, tuple):
            self.assertEqual(actual, expected)
            return None
        self.assertEqual(json.dumps(actual, indent=2), json.dumps(expected, indent=2))
        self.assertEqual(diagnose.report(actual), diagnose.report(expected))
        self.assertEqual(sharing(actual), sharing(expected))
        return actual

    def test_seeded_multi_root_corpora_match_the_frozen_implementation(self):
        exercised = dict(sequences=0, duplicates=0, violations=0, unordered=0, selected=0)
        for seed in range(16):
            rng = random.Random(1000 + seed)
            rows = corpus(seed, roots=1 + seed % 7, cases=1 + seed % 4, steps=4 + 3 * (seed % 6), lineage=seed % 5 != 4)
            for _ in range(4):
                options = dict(select_methods=rng.random() < 0.6, sequence_length=rng.choice((None, None, 1, 2, 3, 5)))
                if rng.random() < 0.5:
                    options.update(observation_per_case=rng.choice((None, 1, 2)), observation_total=rng.choice((None, 2, 5)),
                                   observation_max_age=rng.choice((None, 3, 40)))
                with self.subTest(seed=seed, events=len(rows), **options):
                    result = self.assertEquivalent(rows, **options)
                    exercised['sequences'] += len(result['recurring_sequences'])
                    exercised['duplicates'] += sum(f['kind'] == 'duplicate_observation' for f in result['findings'])
                    exercised['violations'] += sum(f['kind'] == 'contract_violation' for f in result['findings'])
                    exercised['unordered'] += result['method_inputs']['unordered_sequence_cases']
                    exercised['selected'] += result['method_inputs']['selected_sequence_cases']
        self.assertTrue(all(exercised.values()), exercised)

    def test_event_limits_and_invalid_inputs_fail_identically(self):
        rows = corpus(7, roots=4, cases=3, steps=10)
        for select in (False, True):
            with self.subTest(select=select):
                self.assertIsNotNone(self.assertEquivalent(rows, max_events=len(rows), select_methods=select))
                self.assertEqual(outcome(diagnose, rows, max_events=len(rows) - 1, select_methods=select),
                                 outcome(reference, rows, max_events=len(rows) - 1, select_methods=select))
                for broken in (dict(rows[3], event_id=rows[1]['event_id'], case_id=rows[1]['case_id']),
                               dict(rows[3], root_case_id='elsewhere'), dict(rows[3], activity=''), 'row'):
                    damaged = [*rows[:5], broken, *rows[5:]]
                    self.assertIsInstance(outcome(reference, damaged, select_methods=select), tuple)
                    self.assertEquivalent(damaged, select_methods=select)

    def test_mapping_references_keep_their_identity(self):
        rows = corpus(3, roots=5, cases=2, steps=12)
        cases, roots = {}, {}
        for number, event in enumerate(rows, 1):
            roots[event['case_id']] = event['root_case_id']
            ref = {'case_id': event['case_id'], 'event_id': event['event_id']}
            cases.setdefault(event['case_id'], []).append((event['activity'], number, ref))
        expected = reference.recurring_sequences(cases, roots, 4)
        actual = diagnose.recurring_sequences(cases, roots, 4)
        self.assertTrue(expected)
        self.assertEqual(json.dumps(actual), json.dumps(expected))
        self.assertEqual(sharing(dict(recurring_sequences=actual, findings=[])),
                         sharing(dict(recurring_sequences=expected, findings=[])))
        given = {id(entry[2]) for entries in cases.values() for entry in entries}
        self.assertTrue(all(id(ref) in given for row in actual for ref in row['evidence']))

    def test_analyze_command_writes_identical_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); project = root/'project'; project.mkdir()
            capture = root/'events.jsonl'
            capture.write_text(''.join(json.dumps(row)+'\n' for row in exportable(corpus(11, roots=5, cases=3, steps=14))))
            environment = dict(CODEX_HOME=str(root/'codex'), CLAUDE_CONFIG_DIR=str(root/'claude'), XDG_STATE_HOME=str(root/'state'))

            def run(name):
                out, err = io.StringIO(), io.StringIO()
                with patch.dict(os.environ, environment), redirect_stdout(out), redirect_stderr(err):
                    code = main(['analyze', str(project), '--output', str(root/name), '--events', str(capture)])
                files = {path.name: path.read_bytes().replace(str(root/name).encode(), b'OUTPUT')
                         for path in sorted((root/name).iterdir())}
                return code, out.getvalue().replace(str(root/name), 'OUTPUT'), files
            actual = run('compact')
            with patch('agent_harness_lab.evidence.analyze', reference.analyze):
                expected = run('frozen')
        self.assertEqual(actual[0], 0)
        self.assertIn('analysis.json', actual[2])
        self.assertTrue(json.loads(actual[2]['analysis.json'])['analysis']['recurring_sequences'])
        self.assertEqual(actual, expected)

    def test_peak_and_retained_bytes_per_event_fall(self):
        rows = corpus(5, roots=40, cases=4, steps=100)

        def measure(module):
            retained = []

            def mining(*args, **kwargs):
                retained.append(tracemalloc.get_traced_memory()[0])
                return original(*args, **kwargs)
            original = module.recurring_sequences
            tracemalloc.start()
            try:
                base = tracemalloc.get_traced_memory()[0]
                with patch.object(module, 'recurring_sequences', mining):
                    result = module.analyze(iter(rows), select_methods=True, sequence_length=8)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            return json.dumps(result), (peak - base) / len(rows), (retained[0] - base) / len(rows)
        expected, old_peak, old_retained = measure(reference)
        actual, new_peak, new_retained = measure(diagnose)
        self.assertEqual(actual, expected)
        self.assertGreater(len(rows), 10000)
        message = f'per event: peak {old_peak:.0f} -> {new_peak:.0f} B, retained {old_retained:.0f} -> {new_retained:.0f} B'
        self.assertLess(new_peak, 0.5 * old_peak, message)
        self.assertLess(new_retained, 0.5 * old_retained, message)


if __name__ == '__main__':
    unittest.main()
