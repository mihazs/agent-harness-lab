# Regression review record reference

[Review a private regression case](regression-review.md)

## Exact review record

Version 1 requires exactly these fields:

| Field | Contract |
| --- | --- |
| `schema_version` | Integer `1` |
| `case_id` | Exact case SHA-256 |
| `expected_event_sha256` | Current journal-head hash from `show` |
| `evidence_sha256` | Current evidence hash, or JSON `null` before evidence exists |
| `decision` | `reviewed`, `quarantined`, `rejected`, `retired`, or the interrupted-attempt `reported` decision |
| `reviewer` | Exactly `identity`, `source`, `provenance`, `basis_kind`, `basis` |
| `evidence_refs` | Bounded opaque reference/hash list in the reproduction format |
| `reason` | Nonempty bounded single-line explanation |

`reviewer.source` must be `operator_attestation`. All reviewer text is bounded;
identities have at most 128 characters. Positive review requires a different
author/reviewer identity, `basis_kind: independent_artifact_review`, a described
basis and nonempty evidence references. Fresh-session-only claims are rejected.
Other decisions can use a bounded label such as `operator_intervention`.

These are declared operator identities and provenance, not authenticated people
or cryptographic proof of independence. Different strings are insufficient proof.
The output explicitly calls the review `operator_attested`; historical
verification and task/root independence stay unverified. Private host access to
the controller store is the authority boundary. Workers do not receive that
store as an input; the kernel canary separately attempts and fails to read it.
