# Repair a harness whose instructions are ignored

Use this when [instruction-adherence](detect-ignored-instructions.md) found a rule or prompt
instruction the agent did not honor and you want to know which repair to try, and whether a
repair you wrote holds up against recorded runs. Everything here is offline and reads files you
name. It makes no model call and changes no project file.

The contracts are in the [instruction-repair reference](instruction-repair.md); the reasoning is
in [why a repair plan is a recommendation](instruction-adherence-rationale.md#why-a-repair-plan-is-a-recommendation).

## 1. Get the candidate repairs

Run the plan over the report and the rule files the report was made with:

```sh
ahl instruction-adherence sessions.json --rules project=AGENTS.md --output report.json
ahl instruction-repair plan report.json --rules project=AGENTS.md --output plan.json
```

`ahl analyze` and `ahl improve` already write the same plan beside `instruction-adherence.json` as
`instruction-repair-plan.json`, and put its counts in the analysis brief.

For each finding the plan lists the mechanisms the evidence leaves open, such as a rule that was not
in the agent's context, a rule buried in a long file, a hedged sentence, or a prose rule that a hook
could enforce. Each carries the repair class it would call for and the competing explanations. Read
them as questions to test, not as answers: the plan never names a cause.

## 2. Write the repair yourself

The plan does not edit anything. Make the change in a copy of your harness: reword the sentence so it
names an observable command or path, shorten or deduplicate the file, move the rule to a layer the
agent loads, or turn an enforceable rule into a hook. Keep every checkable rule. For a prompt
instruction, change the harness (for example an instruction to restate user constraints before acting),
never the user's prompt.

## 3. Record the arms

Run the reproduction task, built from the finding with synthetic or redacted input, under the old
harness and under the repaired one, several independent times each. Save each arm as a sessions file
in the [format of the detector](detect-ignored-instructions.md#1-describe-the-sessions), declaring
`capture`, `loaded` and a `root` per independent run. Also record tasks that worked before the
repair under both harnesses; they are the preserved tasks.

## 4. Screen the repair

```sh
ahl instruction-repair judge --target REF \
  --baseline-rules project=old/AGENTS.md --candidate-rules project=new/AGENTS.md \
  --reproduction baseline.json candidate.json --preserved kept-baseline.json kept-candidate.json
```

`REF` is the `instruction_ref` of the finding in the plan. Read the `verdict`:

| Verdict | Meaning | What to do |
| --- | --- | --- |
| `rejected` | The candidate dropped a checkable rule (`rule_removed`) or a task or rule that held before no longer holds (`regression`) | Fix the repair; the lost and recovered counts are reported apart |
| `recommendation_only` | The baseline arm did not reproduce the violation (`not_reproduced`) | Keep the recommendation; make no change |
| `inconclusive` | Too few decided roots, unknown captures, missing preserved evidence, a control or preserved root the candidate cannot decide (`control_undecided`, `preserved_undecided`) or arms that overlap | Record more independent runs or complete captures, and record every moved rule file as loaded |
| `supported_by_recorded_pair` | The candidate arm followed the rule where the baseline did not, with no control or preserved root lost or left undecided | Take the repair to the controlled experiment; this is not a validated improvement |

The exit status is 0 whenever a verdict was produced, including a rejection, and 2 for invalid input.
