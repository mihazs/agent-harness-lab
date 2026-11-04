# Initial validation — 2026-10-31

This experimental release adds diagnostic tooling and research-backed criteria.
No general improvement in agent success has been established.

## Executed checks

- 19 evaluation/mining tests and 16 tool tests pass.
- Packaging installs successfully without runtime dependencies.
- Example diagnosis CLI executes on the committed synthetic input.

## Local classifier adapter smoke checks

Inputs are reproduced by `examples/classifier_fixture.py`: 40 training, 80 calibration,
40 development, and 40 held-out synthetic records. Repeated vocabulary deliberately
makes this an easy adapter test, not a generalization benchmark. All inference was local.
Reports label these rows `adapter_smoke`. They are never reviewed or gold labels.
The separate [diagnostic corpus profile](diagnostic-corpus.md#benchmark-input-profile)
reads private adjudicated development exports and is descriptive only.

| Method | Held-out Brier | Accepted / labeled | Accepted accuracy |
| --- | --- | --- | --- |
| baseline | 0.250000 | 0 / 40 | None |
| tfidf | 0.029930 | 40 / 40 | 1.0 |
| embeddings | 0.000515 | 40 / 40 | 1.0 |
| nli | 0.001150 | 40 / 40 | 1.0 |

Neural adapters used `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
and `MoritzLaurer/multilingual-MiniLMv2-L6-mnli-xnli`. Local reports retain model
directory hashes, dependency versions, timing, and memory. The constant baseline
correctly abstains with insufficient confidence. No model is promoted on this smoke test.

## Remaining evidence gaps

Real-world diagnostic accuracy and outcome-preserving intervention benefits
have not been established by the published synthetic checks.
SetFit, clustering, anomaly detection, and deep sequence training remain conditional
experiments, not implemented default dependencies. No hosted Jev evaluation was run.

Keep raw traces, model weights, environments and trial workspaces outside version
control. The ignored `artifacts/` directory is available for local-only work.

## Benchmark input size controls

`ahl benchmark FILE --method baseline` reads every input example and retains its
complete evidence text. `--max-examples N` optionally rejects input exceeding a
positive row limit chosen by the caller. Root identity, label type, nonempty
text and cross-split validation still apply.

TF-IDF retains all features extracted from training examples. Terms absent from
that training vocabulary remain unrepresented during prediction; fitting on the
validation data would change the evaluation protocol.

NLI tokenization preserves the complete evidence/hypothesis pair. The adapter
supports BERT, RoBERTa and XLM-R learned absolute-position profiles, checks the
configuration against the actual position table and applies the RoBERTa padding
offset. Its maximum is the smaller of that capacity and a finite tokenizer
declaration. Hugging Face's unspecified-length sentinel contributes no capacity.
Unsupported, missing or inconsistent capacity and overlong pairs exit 2 without
a scored report. Long inputs still cannot be scored by a model lacking enough
supported context; no window aggregation protocol is implied.

The sentence-embedding adapter admits only a stock, unprompted Transformer and
Pooling pipeline. Before either encoding call, every input is tokenized without
truncation using the checked stripping/lowercase preprocessing. Full token IDs
and masks must equal the encoder's observed tokenization, and the input must fit
both its saved encoder limit and the checked learned-position/tokenizer capacity.
Unknown modules, active default prompts, preprocessing mismatches and overlong
inputs exit 2 without a scored report. The documented multilingual MiniLM model's
saved encoder limit is 128 tokens. Longer examples remain unscorable by that
configuration; no chunk aggregation or model-setting override is applied.

These contracts follow [Transformers padding/truncation](https://huggingface.co/docs/transformers/main/pad_truncation),
the [BERT implementation](https://github.com/huggingface/transformers/blob/main/src/transformers/models/bert/modeling_bert.py),
the [RoBERTa position implementation](https://github.com/huggingface/transformers/blob/main/src/transformers/models/roberta/modeling_roberta.py),
[TF-IDF vocabulary semantics](https://scikit-learn.org/stable/modules/generated/sklearn.feature_extraction.text.TfidfVectorizer.html)
and [Sentence Transformers sequence lengths](https://www.sbert.net/examples/sentence_transformer/applications/computing-embeddings/README.html).
The stock preprocessing profile follows the [version 3 tokenizer implementation](https://github.com/huggingface/sentence-transformers/blob/v3.0.1/sentence_transformers/models/Transformer.py)
and the [current prompt/encoding API](https://www.sbert.net/docs/package_reference/sentence_transformer/model.html).
