from contextlib import nullcontext, redirect_stdout, redirect_stderr
import io
import json
from types import SimpleNamespace
import sys
from unittest.mock import patch

import pytest

from tools import benchmark_models as bench


def nli_row(text):
    return dict(text=text, split='heldout', root_case_id='synthetic', label=None)


class Scores:
    def __init__(self, count): self.count = count
    def softmax(self, _): return self
    def __getitem__(self, _): return self
    def tolist(self): return [.75] * self.count


class TokenTensor:
    def __init__(self, rows):
        self.rows = rows
        self.shape = (len(rows), len(rows[0]))
    def equal(self, other): return isinstance(other, TokenTensor) and self.rows == other.rows


class SyntheticClassifier:
    classes_ = [False, True]
    def __init__(self, **kwargs): pass
    def fit(self, *args): pass
    def predict_proba(self, rows): return Scores(len(rows))


def nli_stack(*, kind='bert', positions=8, tokenizer_limit=8, padding=None):
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
        def __call__(self, texts, questions=None, *, padding, truncation, return_tensors, max_length=None):
            tokens.append(dict(texts=texts, questions=questions, truncation=truncation))
            rows = [['CLS', *text.split(), 'SEP'] for text in texts]
            if questions is not None:
                rows = [row+question.split()+['SEP'] for row, question in zip(rows, questions)]
            limit = max_length if max_length is not None else self.model_max_length
            if truncation and limit is not None: rows = [row[:limit] for row in rows]
            longest = max(map(len, rows))
            masks = [[1]*len(row)+[0]*(longest-len(row)) for row in rows]
            rows = [row+['PAD']*(longest-len(row)) for row in rows]
            return dict(input_ids=TokenTensor(rows), attention_mask=TokenTensor(masks))
    tokenizer, model = Tokenizer(), Model()
    modules = {
        'torch': SimpleNamespace(inference_mode=nullcontext, is_tensor=lambda value: isinstance(value, TokenTensor),
                                 equal=lambda first, second: first.equal(second)),
        'transformers': SimpleNamespace(
            AutoTokenizer=SimpleNamespace(from_pretrained=lambda *a, **k: tokenizer),
            AutoModelForSequenceClassification=SimpleNamespace(from_pretrained=lambda *a, **k: model)),
        'transformers.tokenization_utils_base': SimpleNamespace(LARGE_INTEGER=int(1e20)),
    }
    return modules, tokenizer, model, calls, tokens


def embedding_stack(*, encoder_limit=8, do_lower_case=False):
    modules, tokenizer, model, _, tokens = nli_stack(positions=16, tokenizer_limit=16)
    calls = []
    class Transformer:
        def __init__(self):
            self.tokenizer, self.auto_model = tokenizer, model
            self.max_seq_length = encoder_limit
            self.do_lower_case = do_lower_case
    class Pooling: pass
    class Encoder(list):
        default_prompt_name = None
        prompts = {}
        def tokenize(self, texts):
            prepared = [text.strip().lower() if self[0].do_lower_case else text.strip() for text in texts]
            return tokenizer(prepared, padding=True, truncation='longest_first',
                             return_tensors='pt', max_length=self[0].max_seq_length)
        def encode(self, texts, **kwargs):
            calls.append(texts)
            return texts
    encoder = Encoder([Transformer(), Pooling()])
    modules.update({
        'sentence_transformers': SimpleNamespace(SentenceTransformer=lambda *a, **k: encoder,
            models=SimpleNamespace(Transformer=Transformer, Pooling=Pooling)),
        'sklearn': SimpleNamespace(),
        'sklearn.linear_model': SimpleNamespace(LogisticRegression=SyntheticClassifier),
    })
    return modules, encoder, calls, tokens


def embedding_rows(text='one two'):
    return [dict(text='positive', label=True, split='train'),
            dict(text='negative', label=False, split='train'), dict(text=text, label=None, split='heldout')]


def test_nli_preserves_full_premise_and_hypothesis_at_context_boundary():
    modules, _, _, calls, tokens = nli_stack()
    with patch.dict(sys.modules, modules):
        result = bench.model_predict('nli', [nli_row('one two three four')], '/synthetic', question='hypothesis')
    assert result == [.75]
    assert len(calls) == 1
    assert not tokens[0]['truncation']
    assert tokens[0]['questions'] == ['hypothesis']


@pytest.mark.parametrize(('text', 'question'), [
    pytest.param('one two three four five', 'hypothesis', id='long-premise'),
    pytest.param('one', 'a b c d e', id='long-question'),
])
def test_nli_overflow_does_not_score_a_truncated_prefix_or_question(text, question):
    modules, _, _, calls, tokens = nli_stack()
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('nli', [nli_row(text)], '/synthetic', question=question)
    assert not calls
    assert not tokens[0]['truncation']


def test_tokenizer_sentinel_cannot_override_actual_model_positions():
    modules, _, _, calls, _ = nli_stack(tokenizer_limit=int(1e30))
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
    assert not calls


@pytest.mark.parametrize('kind', ['roberta', 'xlm-roberta'])
def test_roberta_padding_offset_reduces_actual_position_capacity(kind):
    modules, _, _, calls, _ = nli_stack(kind=kind, positions=10, tokenizer_limit=10, padding=1)
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
    assert not calls


@pytest.mark.parametrize('change', ['unknown', 'mismatch', 'sentinel', 'relative', 'weights', 'missing', 'unbounded',
                                    'tokenizer'])
def test_unknown_or_inconsistent_capacity_fails_before_inference(change):
    modules, tokenizer, model, calls, _ = nli_stack()
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
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('nli', [nli_row('one')], '/synthetic', question='hypothesis')
    assert not calls


def test_smaller_finite_tokenizer_capacity_is_respected():
    modules, _, _, calls, _ = nli_stack(positions=16, tokenizer_limit=8)
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('nli', [nli_row('one two three four five')], '/synthetic', question='hypothesis')
    assert not calls


def test_public_cli_rejects_long_input_without_a_scored_report(tmp_path):
    modules, _, _, calls, _ = nli_stack()
    path = tmp_path/'examples.jsonl'
    path.write_text(json.dumps(nli_row('one two three four five')))
    output, errors = io.StringIO(), io.StringIO()
    arguments = ['benchmark', str(path), '--method', 'nli', '--model-path', str(tmp_path), '--question', 'hypothesis']
    with patch.dict(sys.modules, modules), patch.object(sys, 'argv', arguments), redirect_stdout(output), redirect_stderr(errors):
        with pytest.raises(SystemExit) as stopped: bench.main()
    assert stopped.value.code == 2
    assert 'context' in errors.getvalue()
    assert output.getvalue() == ''
    assert not calls


def test_tfidf_retains_all_extracted_training_features():
    settings = []
    class Vectorizer:
        def __init__(self, **kwargs): settings.append(kwargs)
        def fit_transform(self, texts): return texts
        def transform(self, texts): return texts
    modules = {
        'sklearn': SimpleNamespace(), 'sklearn.linear_model': SimpleNamespace(LogisticRegression=SyntheticClassifier),
        'sklearn.feature_extraction': SimpleNamespace(),
        'sklearn.feature_extraction.text': SimpleNamespace(TfidfVectorizer=Vectorizer),
    }
    rows = [dict(text='positive', label=True, split='train'), dict(text='negative', label=False, split='train')]
    with patch.dict(sys.modules, modules):
        assert bench.model_predict('tfidf', rows) == [.75, .75]
    assert settings[0].get('max_features') is None


def test_embedding_preserves_checked_preprocessing_at_encoder_boundary():
    modules, _, calls, _ = embedding_stack(encoder_limit=6, do_lower_case=True)
    with patch.dict(sys.modules, modules):
        result = bench.model_predict('embeddings', embedding_rows('  One TWO three FOUR  '), '/synthetic')
    assert result == [.75]*3
    assert len(calls) == 2


def test_embedding_late_overflow_rejects_before_any_encoding():
    modules, _, calls, _ = embedding_stack(encoder_limit=6)
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='context'):
        bench.model_predict('embeddings', embedding_rows('one two three four five'), '/synthetic', batch_size=1)
    assert not calls


@pytest.mark.parametrize('change', ['prompt', 'pipeline', 'noniterable', 'lowercase', 'capacity', 'encoder_limit'])
def test_embedding_unknown_prompt_or_pipeline_rejects_without_encoding(change):
    modules, encoder, calls, _ = embedding_stack()
    if change == 'prompt': encoder.default_prompt_name = 'query'; encoder.prompts = {'query':'prefix: '}
    if change == 'pipeline': encoder.append(object())
    if change == 'noniterable': modules['sentence_transformers'].SentenceTransformer = lambda *a, **k: object()
    if change == 'lowercase': encoder[0].do_lower_case = None
    if change == 'capacity': encoder[0].auto_model.config.max_position_embeddings = int(1e30)
    if change == 'encoder_limit': encoder[0].max_seq_length = 0
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='Embedding'):
        bench.model_predict('embeddings', embedding_rows(), '/synthetic')
    assert not calls


def test_embedding_tokenization_mismatch_cannot_masquerade_as_complete_input():
    modules, encoder, calls, _ = embedding_stack()
    original = encoder.tokenize
    def altered(texts):
        result = original(texts)
        result['input_ids'].rows[0][1] = 'different'
        return result
    encoder.tokenize = altered
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='Embedding'):
        bench.model_predict('embeddings', embedding_rows(), '/synthetic')
    assert not calls


def test_embedding_unknown_token_tensor_shape_rejects_without_encoding():
    modules, encoder, calls, _ = embedding_stack()
    encoder.tokenize = lambda texts: dict(input_ids=[['not-a-tensor']], attention_mask=[[1]])
    with patch.dict(sys.modules, modules), pytest.raises(ValueError, match='Embedding'):
        bench.model_predict('embeddings', embedding_rows(), '/synthetic')
    assert not calls
