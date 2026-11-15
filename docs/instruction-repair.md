# Instruction repair reference

`ahl instruction-repair` has two actions. `plan` reads an
[instruction-adherence](instruction-adherence.md) report and lists candidate mechanisms and repair
classes. `judge` screens one repair against recorded arms. Both are deterministic, offline and
read-only toward their inputs, and write digest-only JSON that is private and never publishable.
Neither claims a cause, a validated improvement or authority to change a file.

## plan

```text
ahl instruction-repair plan REPORT [--rules LAYER=FILE]... [--scope project|global|project+global] [--output FILE]
```

`REPORT` must be an `ahl-instruction-adherence-v1` report. `--rules` names the harness files the report
was made with, so placement facts can be read; a layer outside `--scope` is refused. The plan
(`ahl-instruction-repair-plan-v1`) has `authority: recommendation_only`, `causal_diagnosis: false`
and `change_authorized: false`, and one candidate per finding. Abstentions are counted in
`source.abstentions` and not diagnosed.

Each candidate carries `recurrence` (copied from the report: sessions, declared independent roots,
unknown-lineage sessions), `hypotheses`, `limits`, `guards`, `requires` and `disposition`. A hypothesis
has a `mechanism`, a `basis` (`observed` when the evidence shows the fact, `not_excluded` when the
evidence cannot rule it out), its `support` (the structural facts behind it), a `repair_class`, a `route`, and its `competing`
explanations, which always include `agent_error_unrelated_to_harness`.

| Mechanism | Evidence it needs | Repair class |
| --- | --- | --- |
| `not_in_context` | The finding is `not_loaded` (observed), the rule was `truncated` (observed, shorten) or loading was not recorded (not excluded) | `restore_loading`, or `shorten_dedupe_reorder` when truncated |
| `conflicting_instructions` | A `conflict` finding | `resolve_conflict` |
| `buried_or_duplicated` | The rule appears more than once, or sits in the later half of a file of at least 200 lines | `shorten_dedupe_reorder` |
| `unobservable_wording` | The sentence carries a soft cue such as `try to` or `be careful` | `reword_observable` |
| `prose_only_rule` | A loaded or possibly loaded rule that a call, order or missing step broke; the basis is always `not_excluded`, because whether a hook already enforces the rule is not read and a hook may exist and have failed. `support` records the observed fact apart: `loaded_rule_broken` or `loading_unrecorded` | `hook_or_feedback_sensor` (route `hook`) |
| `context_loss` | The finding reaches turn 200 or later, or the report says a recorded compaction fell between the instruction coming into view and the finding (`compaction: in_window`). The basis is always `not_excluded`, because whether the compaction removed the instruction is not read; `support` records the facts apart: `late_in_session` and one of `compaction_in_window`, `no_compaction_in_window` (the session records its compactions and none fell in the window) or `compaction_not_recorded` (it does not, or the report predates the field) | `re_ground_after_compaction` (route `state`) |
| `no_self_check_for_user_instruction` | The ignored instruction came from a user prompt | `pre_action_checklist_or_echo` |

A `not_loaded` or `conflict` finding gets only its own mechanism. The 200-line and 200-turn thresholds are
documented defaults, not measured limits. `skill_not_activated` is always listed under `unassessed`
because the report carries no skill activation evidence.

`guards` always include never weakening, deleting or bypassing a safety rule and never editing a protected
or out-of-scope surface. A prompt finding adds `never_edit_the_users_prompt`; a global or mixed layer adds
`global_scope_must_be_explicit`. `limits` can hold `lineage_unknown`, `single_independent_root` and
`no_supported_mechanism`.

## judge

```text
ahl instruction-repair judge --target REF --baseline-rules LAYER=FILE... --candidate-rules LAYER=FILE...
    --reproduction BASELINE CANDIDATE [--preserved BASELINE CANDIDATE] [--scope ...] [--output FILE]
```

The target is the `instruction_ref` of a harness rule of the baseline or of a prompt instruction in a
baseline reproduction session. The arms are sessions files of the detector. The verdict
(`ahl-instruction-repair-verdict-v1`) has `authority: screen_only`, `validated_improvement: false` and
`change_authorized: false`.

The grader is the adherence detector. Per session it judges every checkable rule, and a rule is credited
or faulted only when the agent had it: a harness rule not recorded `loaded`, a pass or a fault that rests only on
calls or triggers before its loading turn (both are judged over the turns from the loading turn on), and any rule of a
session with unknown capture are `unknown`. A root's outcome is the worst over its
sessions; a session with no declared `root` is excluded and counted in `unknown_lineage_excluded`.

Verdicts, decided in this order:

1. `rejected` with `rule_removed` when the candidate harness lacks a checkable rule the baseline had, and with `regression` when a rule that was satisfied in a paired root of the preserved arm, or of the reproduction arm for every other rule, is violated under the candidate.
2. `inconclusive` with `no_decided_baseline_roots` when no baseline root was decided.
3. `recommendation_only` with `not_reproduced` when the baseline arm shows no violation.
4. `inconclusive` with `no_decided_candidate_roots`, `preserved_not_supplied`, `control_incomplete` or `preserved_incomplete` (a baseline root of the reproduction arm, for the control rules, or of the preserved arm is missing or unpaired in the candidate arm), `control_undecided` or `preserved_undecided` (a root where the candidate may have lost a control rule or a rule of a preserved task: it was satisfied under the baseline and is unknown under the candidate, for example a partial capture, or a rule moved to a file the sessions never record as loaded; or the baseline could not decide it and the candidate violates it or cannot decide it either) or `interval_overlap`.
5. `supported_by_recorded_pair` when the lower 95% Wilson bound of the candidate's followed roots exceeds the upper bound of the baseline's and no control or preserved root was lost, missing or left unresolved or unproven. An unknown root counts as followed in the baseline and as not followed in the candidate, and an incomplete, unresolved or unproven control or preserved root blocks the verdict, so unknown never helps the repair. A rule the baseline could not decide blocks only when the candidate does not demonstrably follow it.

`reproduction`, `controls` and `preserved` report `lost` and `recovered` roots apart, `undecided` roots
where either side is unknown and, for `controls` and `preserved`, `unresolved` roots that were satisfied
in the baseline and are unknown in the candidate and `unproven` roots where the baseline could not decide a
rule and the candidate violates it or cannot decide it either.

## Analysis wiring

`ahl analyze` and `ahl improve` write `instruction-repair-plan.json` beside `instruction-adherence.json`
whenever the latter is written, and add `repair_plan` to the `instruction_adherence` brief section:
the candidate count, abstentions not diagnosed, counts by repair class and mechanism, and the unassessed
mechanisms. The brief never carries a digest.

## Limits

- The mechanisms are structural facts of a finding and the harness text, not measurements of why an agent deviated.
- A compaction is read only from a session that records its compactions. An empty record is not proof that none happened, and a recorded one does not show that the instruction left the context.
- Whether a hook already enforces a rule and whether a skill activated are not read.
- The screen grades recorded arms; the controlled paired experiment and the application gate still decide whether a repair is validated or applied.
- The verdict holds for the rules the lexical extraction can check; reviewable-only and unverifiable instructions produce no outcome.
