# Evidence behind the workflow

Methodology reviewed 2026-10-31. These primary sources motivate experiment design;
no published benchmark was independently reproduced here. The local application
column is an inference, not a claim that these skills reproduce the paper's gains.

| Primary source | Supported method | Local application and limits |
| --- | --- | --- |
| [Comparative trace clustering](https://ceur-ws.org/Vol-1527/paper7.pdf) | Compares changing processes using trace clusters; evaluated on real event data. | Stratify by task, version and capture regime. A pooled history can conceal change; case mix and logging drift confound agent conclusions. |
| [Sentence-BERT, EMNLP 2019](https://aclanthology.org/D19-1410/) | Sentence representations for similarity retrieval and clustering. | Retrieve paraphrased incidents and propose families, then verify shared mechanisms against traces and source. Similarity is not causal attribution. |
| [HDBSCAN, JOSS 2017](https://joss.theoj.org/papers/10.21105/joss.00205) | Hierarchical density-based clustering implementation. | Optional clustering with noise/outlier review; audit purity and parameter sensitivity. It does not establish harness failure labels. |
| [Lost in the Middle, TACL](https://arxiv.org/abs/2307.03172) | Positional sensitivity on long-context retrieval and question-answering tasks in the studied models. | Use bounded representative evidence packs; test early/middle/late constraints when relevant. Do not extrapolate the measured effect size to a current model. |
| [GEPA, ICLR 2026](https://arxiv.org/abs/2507.19457) | Reflective prompt evolution from execution feedback. | Test diverse mechanism-specific proposals with trace citations and fixed feedback budgets. No requirement to install GEPA; paper gains are not local proof. |
| [ACE, March 2026 revision](https://arxiv.org/abs/2510.04618) | Incremental context evolution for self-improving systems. | Retain distinct useful guidance while consolidating redundancy. Does not justify indefinitely growing instructions. |
| [Agent Lightning](https://arxiv.org/abs/2508.03680) | RL training with trajectory decomposition and credit assignment. | Separate intermediate events and final outcomes for diagnosis. Editing instructions is not RL; training needs models, rewards, compute and separate authorization. |
| [Rethinking harness evaluation, October 2026 revision](https://arxiv.org/abs/2607.12227) | Compares harness evolution with matched-budget task-level search and evaluates transfer; reports limited generalization in its coding-agent experiments. | Match inference/feedback budgets, split independent task families, retain negative results and test transfer before broad claims. |

Learning methods should answer a named uncertainty and beat a simpler reviewed
baseline. Use lexical similarity and process counts first. Use existing authorized
embedding or labeling tools if they improve grouping, uncertainty sampling or
coverage; keep raw sensitive traces off external APIs. Do not train a deep model
solely because the historical corpus is large. Clusters propose explanations;
external outcome checks and interventions evaluate them.

## Objective diagnosis criteria (2026-10-31)

Published results motivate experiments, not automatic edits. Maintain the following
source-to-decision record; the recent agent studies below are preprints, not local
replications. Classical calibration and NLP papers provide stronger established
method evidence but still require domain validation.

| Primary evidence | Decision | Limitation |
| --- | --- | --- |
| [COMPASS](https://ceur-ws.org/Vol-3996/paper-5.pdf), process-mining case study | Discover variants and check explicit process constraints; compare successes with failures. | 69 questions, ten repetitions per iteration; reduced premature querying is not itself improved final correctness. |
| [Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988), experimental preprint | Ablate redundant instructions and measure completion plus cost. | Primarily Python tasks; generated and human-written files have different effects. |
| [AGENTS.md efficiency](https://arxiv.org/abs/2601.20404), preprint | Measure tokens/runtime without assuming shorter means correct. | Comprehensive output correctness was outside its scope. |
| [Mining agent skills](https://arxiv.org/abs/2610.05777), preprint | Compare evidence selection and skill representations on unseen tasks. | Benefits depend on task domain and outcome-label availability. |
| [Skill consumption experiments](https://arxiv.org/abs/2605.23899), preprint | Test activation and downstream utility, including negative transfer. | Plausible skill wording is not a utility measurement. |
| [Calibration](https://proceedings.mlr.press/v70/guo17a.html), ICML 2017 | Measure Brier score and calibrate on a separate split. | Confidence and cosine similarity are not verified correctness probabilities. |
| [SelectiveNet](https://proceedings.mlr.press/v97/geifman19a.html), ICML 2019 | Report error versus accepted coverage; allow abstention. | Rejecting everything is not useful coverage; distribution shift changes performance. |
| [NLI classification](https://aclanthology.org/D19-1404/), EMNLP 2019 | Test local atomic criterion classification. | Entailment of supplied text does not establish truth of its premises. |
| [SetFit](https://arxiv.org/abs/2209.11055) | Consider few-shot classifier adaptation after a lexical baseline. | Small benchmark label requirements do not establish sufficient local labels. |
| [Isolation Forest](https://doi.org/10.1109/ICDM.2008.17), ICDM 2008 | Propose unusual traces for investigation. | Anomalous does not mean defective; rare successful behavior is a control. |
| [Predictive process LSTMs](https://arxiv.org/abs/1612.02130) | Consider sequence prediction only for a measured unresolved question. | Predicting common next events does not identify desirable behavior. |

### Jev-like decisions

[TypeSafe's documentation](https://docs.typesafe.ai/confidence) defines confidence
as a function of the answer distribution. Its [workflow comparisons](https://typesafe.ai/blog/introducing-system-one-models-and-jev)
use reference probabilities from other models, not independent ground truth.
These are vendor reports. Typed outputs prevent some format errors, not incorrect
judgments. Do not claim that an NLI model reproduces Jev's proprietary architecture.

Use local TF-IDF/linear, sentence-embedding/linear and NLI comparisons first.
Record model revision, preprocessing, label wording, language, truncation, calibration
split, accepted coverage, latency and memory. Hosted Jev requires a separately
configured disclosure boundary. Do not train RL or deep sequence models merely to
analyze a large corpus. SetFit, HDBSCAN and Isolation Forest remain conditional
experiments when simpler methods cannot answer a named question.
