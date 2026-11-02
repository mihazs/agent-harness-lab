# Private controller history reports

Create a report from actual current decision receipts rather than imported success
labels. Keep the input, controller store and generated report private.

The input JSON is a list of one to eight unique receipt references:

```json
[
  {
    "run_id": "REPLACE_WITH_RUN_SHA256",
    "receipt_sha256": "REPLACE_WITH_RECEIPT_SHA256"
  }
]
```

Generate JSON, Markdown and HTML in a new local directory:

```sh
python3 -m agent_harness_lab benchmark-report --history-store /private/history --input /private/receipt-refs.json --output /private/report
```

Create receipts with [the controller receipt commands](decision-receipts.md).
Changed engine code requires explicit receipt regeneration; a saved successful
verification is not authority for a later report.

The adapter reopens each receipt and its current bank/oracle/qualification
dependencies and recomputes analysis. It checks again after formatting, before
creating output files. Revoked authority or changed evidence refuses the report.
JSON, Markdown and HTML files have mode `0600`; the output directory is private.

Reports retain qualification provenance, cohort membership, endpoint dispositions,
paired recoveries and introduced failures, missing/invalid/unknown observations,
per-task/root rates, confidence intervals and their assumptions. Related variants
and overlapping cohorts do not produce additional independent observations. No
pooled model effect or verified semantic root independence is claimed.

This route always refuses `--public`, before opening private input or creating an
output directory. Synthetic-looking publication labels cannot authorize export.
HTML escapes untrusted labels and provenance; Markdown keeps them in literal
cells and indented JSON.

Python callers use `benchmark.history_summary(store_path, receipt_refs)` followed
by `benchmark.render(result)`. Results remain private analysis evidence and grant
no certification or adoption authority. Reverify receipts before later use.

Guarded optimization and corrected-oracle requalification remain separate work
under issue #122. Run `task test:history-report` for native synthetic reporting,
revocation, rendering and publication-boundary checks.
