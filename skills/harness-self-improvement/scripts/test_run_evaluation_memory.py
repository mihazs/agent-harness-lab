"""Equivalence and peak-memory checks for the evaluation runner's streaming paths.

The reference functions below are frozen copies of the whole-input implementations they
replaced; every synthetic input must give the same digest, classification and bytes.
"""
import hashlib
import json
import math
from pathlib import Path
import random
import tempfile
import tracemalloc
import unittest
from unittest import mock

import run_evaluation
from run_evaluation import digest, jsonl_lines, trace_result, write_results

MIB = 1024 * 1024
# One reused chunk buffer plus interpreter noise; the whole-file reference needs two copies of a file.
DIGEST_LIMIT = run_evaluation.DIGEST_CHUNK_BYTES + MIB // 2


def reference_digest(root):
    if not root.is_dir() or root.is_symlink():
        raise ValueError(f'evaluation input must be an existing directory: {root}')
    h = hashlib.sha256()
    for p in sorted(root.rglob('*')):
        if p.is_symlink():
            raise ValueError(f'symlinks are not allowed in evaluation inputs: {p}')
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b'\0' + p.read_bytes())
    return h.hexdigest()


def reference_trace_result(run, agent="codex"):
    if run['timeout'] or run['exit_code'] != 0:
        return 'execution_error', None
    try:
        events = [json.loads(line) for line in jsonl_lines(run['stdout']) if line.strip()]
        if any(not isinstance(e, dict) for e in events):
            return 'invalid_trace', None
        if agent == 'claude':
            completed = [e for e in events if e.get('type') == 'result']
            if (len(completed) != 1 or completed[0].get('subtype') != 'success'
                    or completed[0].get('is_error') is not False
                    or any(e.get('type') == 'error' for e in events)):
                return 'incomplete', None
            return 'completed', completed[0].get('usage')
        completed = [e for e in events if e.get('type') == 'turn.completed']
        if not completed or any(e.get('type') in ('turn.failed', 'error') for e in events):
            return 'incomplete', None
        return 'completed', completed[-1].get('usage')
    except json.JSONDecodeError:
        return 'invalid_trace', None


def peak(function, *args):
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        value = function(*args)
        return value, tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def synthetic_tree(root, rng):
    (root / 'nested/deeper').mkdir(parents=True)
    (root / 'empty-dir').mkdir()
    (root / 'nested/deeper/empty').write_bytes(b'')
    (root / 'nested/a.txt').write_text('a\n')
    (root / 'nested/ünïcode name.md').write_text('synthetic ✓\n')
    (root / 'exact-chunk.bin').write_bytes(rng.randbytes(MIB))
    (root / 'crosses-chunks.bin').write_bytes(rng.randbytes(2 * MIB + 17))
    (root / 'z-last').write_bytes(b'\0' * 5)


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_chunked_digest_matches_whole_file_digest(self):
        tree = self.root / 'tree'
        tree.mkdir()
        synthetic_tree(tree, random.Random(221))
        expected = reference_digest(tree)
        self.assertEqual(digest(tree), expected)
        for size in (1, 7, 4096):
            with self.subTest(chunk=size), mock.patch.object(run_evaluation, 'DIGEST_CHUNK_BYTES', size):
                self.assertEqual(digest(self.root / 'tree/nested'), reference_digest(self.root / 'tree/nested'))
        empty = self.root / 'empty'
        empty.mkdir()
        self.assertEqual(digest(empty), reference_digest(empty))
        # A file's boundary is still the next path, so moving bytes between files changes the digest.
        (tree / 'z-last').write_bytes(b'\0' * 4)
        (tree / 'nested/a.txt').write_text('a\n\0')
        self.assertNotEqual(digest(tree), expected)
        self.assertEqual(digest(tree), reference_digest(tree))

    def test_digest_rejections_are_unchanged(self):
        missing = self.root / 'missing'
        regular = self.root / 'file'
        regular.write_text('x')
        linked = self.root / 'linked'
        linked.mkdir()
        (linked / 'target').write_text('x')
        (linked / 'link').symlink_to('target')
        alias = self.root / 'alias'
        alias.symlink_to(linked)
        for root in (missing, regular, linked, alias):
            with self.subTest(root=root.name):
                with self.assertRaises(ValueError) as expected:
                    reference_digest(root)
                with self.assertRaises(ValueError) as actual:
                    digest(root)
                self.assertEqual(str(actual.exception), str(expected.exception))

    def test_digest_peak_is_bounded_by_the_chunk_not_the_file(self):
        tree = self.root / 'large'
        tree.mkdir()
        rng = random.Random(7)
        for index in range(3):
            (tree / f'part-{index}.bin').write_bytes(rng.randbytes(8 * MIB))
        expected, before = peak(reference_digest, tree)
        actual, after = peak(digest, tree)
        self.assertEqual(actual, expected)
        self.assertGreater(before, 8 * MIB)
        self.assertLess(after, DIGEST_LIMIT)


def event(rng, agent):
    kinds = ['result', 'error', 'turn.completed', 'turn.failed', 'assistant', 'message', None, 7, ['result']]
    record = {'type': rng.choice(kinds)} if rng.random() < .9 else {}
    if rng.random() < .6:
        record['subtype'] = rng.choice(['success', 'error_max_turns', None, 1])
    if rng.random() < .6:
        record['is_error'] = rng.choice([False, True, None, 0, 'false'])
    if rng.random() < .7:
        record['usage'] = rng.choice([{'input_tokens': rng.randrange(9), 'output_tokens': rng.randrange(9)},
                                      None, 3, [], {'nested': {'n': rng.random()}}])
    return record


def completion(rng, agent):
    record = ({'type': 'result', 'subtype': 'success', 'is_error': False} if agent == 'claude'
              else {'type': 'turn.completed'})
    choice = rng.randrange(3)
    if choice:
        record['usage'] = None if choice == 1 else {'output_tokens': rng.randrange(9)}
    return record


def line(rng, agent):
    roll = rng.random()
    if roll < .03:
        return rng.choice(['[]', '1', '"text"', 'null', 'true'])
    if roll < .05:
        return rng.choice(['{', 'bad-json', '{"type":"result",}', '{"text":"a\x0bb"}'])
    if roll < .08:
        return rng.choice(['', '  ', '\t'])
    text = json.dumps(event(rng, agent), ensure_ascii=rng.random() < .5)
    return text.replace('{', '{"text":"a b", ', 1) if text != '{}' and rng.random() < .05 else text


class TraceResultTests(unittest.TestCase):
    def test_classification_matches_reference_on_synthetic_corpus(self):
        rng = random.Random(221)
        seen = set()
        for _ in range(3000):
            agent = rng.choice(['claude', 'codex', 'other'])
            newline = rng.choice(['\n', '\r\n'])
            lines = [line(rng, agent) for _ in range(rng.randrange(6))]
            if rng.random() < .4:
                lines.append(json.dumps(completion(rng, agent)))
            stdout = newline.join(lines)
            if rng.random() < .5:
                stdout += newline
            run = dict(timeout=rng.random() < .03, exit_code=0 if rng.random() < .95 else 1, stdout=stdout)
            expected = reference_trace_result(run, agent)
            actual = trace_result(run, agent)
            self.assertEqual(actual, expected, (agent, stdout))
            seen.add((agent, expected[0], expected[1] is not None))
        for agent in ('claude', 'codex', 'other'):
            for status in ('completed', 'incomplete', 'invalid_trace', 'execution_error'):
                self.assertIn(status, {s for a, s, _ in seen if a == agent})
            self.assertIn((agent, 'completed', True), seen)
            self.assertIn((agent, 'completed', False), seen)

    def test_first_and_last_usage_and_returned_identity(self):
        first, second = {'input_tokens': 1}, {'input_tokens': 2}
        lines = [{'type': 'turn.completed', 'usage': first}, {'type': 'turn.completed', 'usage': second}]
        run = dict(timeout=False, exit_code=0, stdout='\n'.join(map(json.dumps, lines)))
        self.assertEqual(trace_result(run), ('completed', second))
        claude = [{'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': first}]
        run = dict(timeout=False, exit_code=0, stdout=json.dumps(claude[0]))
        self.assertEqual(trace_result(run, 'claude'), ('completed', first))
        run['stdout'] += '\n' + json.dumps(dict(claude[0], usage=second))
        self.assertEqual(trace_result(run, 'claude'), ('incomplete', None))

    def test_parse_errors_after_a_non_object_still_surface_in_order(self):
        # An over-long integer raises plain ValueError, which is not an invalid-trace JSONDecodeError.
        huge = '{"type":"turn.completed","usage":' + '1' * 5000 + '}'
        for agent in ('claude', 'codex'):
            for stdout in ('[]\n' + huge, huge + '\n{', json.dumps({'type': 'error'}) + '\n' + huge):
                with self.subTest(agent=agent, first=stdout[:2]):
                    run = dict(timeout=False, exit_code=0, stdout=stdout)
                    with self.assertRaises(ValueError) as expected:
                        reference_trace_result(run, agent)
                    with self.assertRaises(ValueError) as actual:
                        trace_result(run, agent)
                    self.assertIs(type(actual.exception), type(expected.exception))
                    self.assertNotIsInstance(actual.exception, json.JSONDecodeError)
            run = dict(timeout=False, exit_code=0, stdout='[]\n{\n' + huge)
            self.assertEqual(reference_trace_result(run, agent), ('invalid_trace', None))
            self.assertEqual(trace_result(run, agent), ('invalid_trace', None))

    def test_trace_peak_does_not_retain_parsed_events(self):
        for agent, final in (('claude', {'type': 'result', 'subtype': 'success', 'is_error': False, 'usage': {'n': 1}}),
                             ('codex', {'type': 'turn.completed', 'usage': {'n': 1}})):
            body = json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'x' * 40}],
                                                                'id': 'synthetic', 'n': list(range(8))}})
            run = dict(timeout=False, exit_code=0, stdout='\n'.join([body] * 40000 + [json.dumps(final)]) + '\n')
            with self.subTest(agent=agent):
                expected, before = peak(reference_trace_result, run, agent)
                actual, after = peak(trace_result, run, agent)
                self.assertEqual(actual, expected)
                self.assertEqual(actual, ('completed', {'n': 1}))
                self.assertGreater(before, 20 * MIB)
                self.assertLess(after, MIB)


def synthetic_results(trials):
    return dict(manifest_sha256='0' * 64, versions={'baseline': '1' * 64, 'candidate': '2' * 64},
                fixtures={'case': '3' * 64}, runtime={'agent': 'codex', 'codex_version': 'synthétique ✓'},
                isolation='cooperative', trials=[dict(trial_id=f'trial-{index}', status='passed',
                                                      execution=dict(exit_code=0, elapsed_seconds=index / 3, timeout=False),
                                                      usage={'input_tokens': index, 'ratio': math.inf, 'nan': math.nan},
                                                      text='line separator\n"quoted"\\', empty=[], nested={})
                                                 for index in range(trials)], inputs_unchanged=True, extra=None)


class ResultsWriteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_written_bytes_match_whole_string_write(self):
        for value in (synthetic_results(0), synthetic_results(3), {}, [], {'a': [1, [2, {}]]}):
            expected, actual = self.root / 'expected.json', self.root / 'results.json'
            expected.write_text(json.dumps(value, indent=2) + '\n')
            write_results(actual, value)
            self.assertEqual(actual.read_bytes(), expected.read_bytes())
            self.assertEqual(actual.stat().st_mode, expected.stat().st_mode)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['expected.json', 'results.json'])

    def test_serialization_error_keeps_previous_results(self):
        path = self.root / 'results.json'
        write_results(path, synthetic_results(1))
        previous = path.read_bytes()
        for value in (dict(synthetic_results(2), bad={1, 2}), dict(synthetic_results(2), bad=Path('x'))):
            with self.assertRaises(TypeError):
                json.dumps(value, indent=2)
            with self.assertRaises(TypeError):
                write_results(path, value)
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual([p.name for p in self.root.iterdir()], ['results.json'])

    def test_write_peak_is_below_the_serialized_document(self):
        results = synthetic_results(8000)
        size = len(json.dumps(results, indent=2)) + 1
        _, after = peak(write_results, self.root / 'results.json', results)
        self.assertEqual((self.root / 'results.json').stat().st_size, size)
        self.assertGreater(size, 2 * MIB)
        self.assertLess(after, size // 4)


if __name__ == '__main__':
    unittest.main()
