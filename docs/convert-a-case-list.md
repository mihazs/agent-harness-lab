# Convert a case list into a native bank

Use this when a project already keeps its own evaluation cases as a JSON list of
"task plus text expectations" records and you want a before/after comparison on them.
The conversion writes a native, frozen task bank whose graders run offline, so a
baseline and a candidate are scored on identical inputs. For the field-by-field
contract see [Evaluation interchange](evaluation-interchange.md#case-list-conversion).

You need the case list, a reviewed mapping that you attest to, and a new private
folder. Nothing is executed beyond the generated graders; no model, provider or
network is used.

## 1. Write the case list

One JSON array, at most 1,000 records and 1 MiB. Each record has an `id`, the `task`
prompt and an `expect` object:

```json
[
  {"id": "refund-policy", "task": "Explain the refund window.",
   "expect": {"contains": ["thirty days"], "not_contains": ["guarantee"]}},
  {"id": "route-ticket", "task": "Route the ticket and name the owning team.",
   "expect": {"names": ["Billing"], "flag": true}}
]
```

`contains` and `not_contains` are phrases matched ignoring case, `names` must appear as
whole words with exactly that case, and `flag` is `true` when the response must carry
the flag marker you declare in the mapping and `false` when it must not. Each case needs
at least one thing a response must contain (a `contains` phrase, a `names` entry or
`"flag": true`); a case that an empty response would satisfy is excluded.

## 2. Write the reviewed mapping

The mapping declares the source, its license and your review. Without a complete
`review` no grader is generated.

```json
{
  "schema_version": 1,
  "format": "task-text-cases-json-v1",
  "source": {"repository": "REPOSITORY", "revision": "REVISION", "format_version": "VERSION",
             "dataset_id": "DATASET", "source_kind": "private", "permission": "approved",
             "dataset_license": {"id": "LICENSE_ID", "ref": "LICENSE_EVIDENCE"},
             "sha256": "SHA-256 OF THE CASE LIST FILE"},
  "review": {"identity": "YOUR_ID", "source": "operator_attestation",
             "evidence_refs": [{"ref": "REVIEW_EVIDENCE", "sha256": "SHA-256 OF THAT EVIDENCE"}]},
  "bank": {"project_group": "GROUP", "time_group": "VERSION", "partition": "development",
           "flag_marker": "[[FLAG]]"}
}
```

`partition` is `construction` or `development`; a converted bank never enters a
protected partition. Set `flag_marker` to `null` when no case uses `flag`.

## 3. Convert

```sh
ahl bank-interchange convert --mapping MAPPING.json --source CASES.json --output NEW_PRIVATE_BANK
```

The output folder must not exist. The command prints the report and also saves it as
`NEW_PRIVATE_BANK/conversion.json`. It exits 0 when a bank was written and 2 when the
input was rejected or no case could be graded offline; in the second case nothing is
written and the report still lists every exclusion.

## 4. Read what was left out

`converted` lists the cases that became tasks. Every other case is in `excluded` with
one typed `reason`, and `exclusion_reasons` counts them. A scoreboard on this bank
covers the converted cases only (`scores_cover`), so state the excluded count next to
any score you quote.

| Reason | What to do |
| --- | --- |
| `needs_repository_context`, `needs_hosting_service_context` | The case carries a `repo_search` or `hosting_search` hint. It cannot be graded from a text response alone; keep it out of the comparison or give it a fixture as a native task. |
| `unsupported_assertion_kind`, `unknown_record_field` | Something in the case is not understood. `unknown_fields` names each field by record and place. Remove it or map it to a supported kind; it is never ignored. |
| `invalid_assertion_value`, `invalid_record`, `invalid_case_id`, `duplicate_case_id` | Fix the case in the source. |
| `flag_marker_undeclared` | Declare `bank.flag_marker`. |
| `no_positive_assertion`, `contradictory_assertions` | The expectations accept an empty response or no response at all. Rewrite them. |

## 5. Check the graders and compare

Run the frozen graders against their controls, then export a manifest for the
comparison:

```sh
ahl bank --manifest NEW_PRIVATE_BANK/bank.json --audit --cooperative-controls
ahl bank --manifest NEW_PRIVATE_BANK/bank.json --export NEW_PRIVATE_EXPORT
```

Run the exported manifest with `ahl evaluate` ([command reference](command-reference.md)).
Each task asks the agent to write `result.json` with a `response` text field. Every
prompt ends with that same one-line instruction, so a bank of very short prompts can
list suspected near-duplicates in `bank_issues`; the issue is advisory. The
[before/after guard](before-after-guard.md) reports both scores with intervals and the
trial counts. An identical baseline and candidate is never `improved`, and a candidate
that does worse is `candidate_regressed`.

The bank keeps `independence: unverified` with one root per case, and it is not
certification eligible. If several cases are variants of one another, declare that
in a native bank rather than reading the interval as if they were independent.
