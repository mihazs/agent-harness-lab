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
