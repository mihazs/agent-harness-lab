---
"agent-harness-lab": minor
---

Let a bank task store the identity of its interaction contract and scripted-reply oracle. A task may declare `interaction_contract_sha256` and, with it, `interaction_oracle_sha256`; both files live in the private grader bundle (`interaction-contract.json`, `interaction-oracle.json`). With contents verified, bank validation rejects a contract whose canonical digest or `task_id` differs, oracle bytes whose digest differs, an oracle written for another task or root, and a hidden reply that appears in the agent-visible fixture; a bank with bound tasks reports an `interaction` count. Tasks without the fields keep their declaration and result shape. This binds identity and separation only: it does not run the evaluator, deliver the reply or grade an episode.
