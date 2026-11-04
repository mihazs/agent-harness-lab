from contextlib import nullcontext, redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import benchmark_models as bench


def nli_row(text):
    return dict(text=text, split='heldout', root_case_id='synthetic', label=None)


class Scores:
    def __init__(self, count): self.count = count
    def softmax(self, _): return self
    def __getitem__(self, _): return self
    def tolist(self): return [.75] * self.count


class ModelInputTests(unittest.TestCase):
    def nli(self, *, kind='bert', positions=8, tokenizer_limit=8, padding=None):
        table = SimpleNamespace(num_embeddings=positions, weight=SimpleNamespace(shape=(positions, 4)), padding_idx=padding)
        embeddings = SimpleNamespace(position_embeddings=table, padding_idx=padding)
        configuration = SimpleNamespace(model_type=kind, max_position_embeddings=positions,
            pad_token_id=padding, position_embedding_type='absolute', id2label={0:'contradiction', 1:'entailment'})
        calls, tokens = [], []
        class Model:
            config = configuration
            base_model = SimpleNamespace(embeddings=embeddings)
            def eval(self): return self
            def __call__(self, **inputs):
                calls.append(inputs)
                return SimpleNamespace(logits=Scores(inputs['input_ids'].shape[0]))
        class Tokenizer:
            model_max_length = tokenizer_limit
            def __call__(self, texts, questions, *, padding, truncation, return_tensors):
                tokens.append(dict(texts=texts, questions=questions, truncation=truncation))
                length = max(len(text.split())+len(question.split())+3 for text, question in zip(texts, questions))
                if truncation: length = min(length, self.model_max_length)
                return dict(input_ids=SimpleNamespace(shape=(len(texts), length)))
        tokenizer, model = Tokenizer(), Model()
        modules = {
            'torch': SimpleNamespace(inference_mode=nullcontext),
            'transformers': SimpleNamespace(
                AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: tokenizer),
                AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=lambda *a, **k: model)),
            'transformers.tokenization_utils_base': SimpleNamespace(LARGE_INTEGER=int(1e20)),
        }
        return modules, tokenizer, model, calls, tokens

    def test_nli_preserves_full_premise_and_hypothesis_at_context_boundary(self):
        modules, _, _, calls, tokens = self.nli()
        with patch.dict(sys.modules, modules):
            result = bench.model_predict('nli', [nli_row('one two three four')], '/synthetic', question='hypothesis')
        self.assertEqual(result, [.75])
        self.assertEqual(len(calls), 1)
        self.assertFalse(tokens[0]['truncation'])
        self.assertEqual(tokens[0]['questions'], ['hypothesis'])

    def test_nli_overflow_does_not_score_a_truncated_prefix_or_question(self):
        for text, question in (('one two three four five', 'hypothesis'), ('one', 'a b c d e')):
            with self.subTest(text=text):
                modules, _, _, calls, tokens = self.nli()
                with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'context'):
                    bench.model_predict('nli', [nli_row(text)], '/synthetic', question=question)
                self.assertFalse(calls)
                self.assertFalse(tokens[0]['truncation'])

    def test_tokenizer_sentinel_cannot_override_actual_model_positions(self):
        modules, _, _, calls, _ = self.nli(tokenizer_limit=int(1e30))
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'context'):
            bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
        self.assertFalse(calls)

    def test_roberta_padding_offset_reduces_actual_position_capacity(self):
        for kind in ('roberta', 'xlm-roberta'):
            modules, _, _, calls, _ = self.nli(kind=kind, positions=10, tokenizer_limit=10, padding=1)
            with self.subTest(kind=kind), patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'context'):
                bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
            self.assertFalse(calls)

    def test_unknown_or_inconsistent_capacity_fails_before_inference(self):
        for change in ('unknown', 'mismatch', 'sentinel', 'relative', 'weights', 'missing', 'unbounded', 'tokenizer'):
            modules, tokenizer, model, calls, _ = self.nli()
            if change == 'unknown': model.config.model_type = 'unsupported'
            if change == 'mismatch': model.config.max_position_embeddings = 9
            if change == 'sentinel': model.config.max_position_embeddings = int(1e30)
            if change == 'relative': model.config.position_embedding_type = 'relative_key'
            if change == 'weights': model.base_model.embeddings.position_embeddings.weight.shape = (7, 4)
            if change == 'missing': model.base_model.embeddings.position_embeddings = None
            if change == 'unbounded':
                model.config.max_position_embeddings = int(1e30)
                model.base_model.embeddings.position_embeddings.num_embeddings = int(1e30)
                model.base_model.embeddings.position_embeddings.weight.shape = (int(1e30), 4)
            if change == 'tokenizer': tokenizer.model_max_length = 0
            with self.subTest(change=change), patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'context'):
                bench.model_predict('nli', [nli_row('one')], '/synthetic', question='hypothesis')
            self.assertFalse(calls)

    def test_smaller_finite_tokenizer_capacity_is_respected(self):
        modules, _, _, calls, _ = self.nli(positions=16, tokenizer_limit=8)
        with patch.dict(sys.modules, modules), self.assertRaisesRegex(ValueError, 'context'):
            bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
        self.assertFalse(calls)

    def test_public_cli_rejects_long_input_without_a_scored_report(self):
        modules, _, _, calls, _ = self.nli()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'examples.jsonl'
            path.write_text(json.dumps(nli_row('one two three four five')))
            output, errors = io.StringIO(), io.StringIO()
            arguments = ['benchmark', str(path), '--method', 'nli', '--model-path', temporary, '--question', 'hypothesis']
            with patch.dict(sys.modules, modules), patch.object(sys, 'argv', arguments), redirect_stdout(output), redirect_stderr(errors):
                with self.assertRaises(SystemExit) as stopped: bench.main()
            self.assertEqual(stopped.exception.code, 2)
            self.assertIn('context', errors.getvalue())
            self.assertEqual(output.getvalue(), '')
            self.assertFalse(calls)

    def test_tfidf_retains_all_extracted_training_features(self):
        settings = []
        class Vectorizer:
            def __init__(self, **kwargs): settings.append(kwargs)
            def fit_transform(self, texts): return texts
            def transform(self, texts): return texts
        class Classifier:
            classes_ = [False, True]
            def __init__(self, **kwargs): pass
            def fit(self, *args): pass
            def predict_proba(self, rows): return Scores(len(rows))
        modules = {
            'sklearn': SimpleNamespace(), 'sklearn.linear_model': SimpleNamespace(LogisticRegression=Classifier),
            'sklearn.feature_extraction': SimpleNamespace(),
            'sklearn.feature_extraction.text': SimpleNamespace(TfidfVectorizer=Vectorizer),
        }
        rows = [dict(text='positive', label=True, split='train'), dict(text='negative', label=False, split='train')]
        with patch.dict(sys.modules, modules):
            self.assertEqual(bench.model_predict('tfidf', rows), [.75, .75])
        self.assertIsNone(settings[0].get('max_features'))
