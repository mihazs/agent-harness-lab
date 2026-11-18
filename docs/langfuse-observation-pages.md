# Project supplied Langfuse observation pages

`agent_harness_lab.sources.langfuse.capture` projects explicitly supplied Langfuse
Observations API v2 page bytes into private source records and candidate parent links.
It makes no network request, authenticates no provider and does not establish that the
supplied pages are genuine or complete. It is not a remote connector or CLI command.

The caller supplies an enabled `SourceSpec` for `langfuse` and `observations_v2`, a
project identifier, a time window of at most one day, and ordered tuples of request
cursor, HTTP status and raw response bytes. The source project and scope fingerprints
must match the declared project and window. Each observation must declare that project,
a trace and observation ID, an in-window start time, type and optional physical parent.
The adapter does not infer a physical parent from a logical-root flag.

An empty page with a cursor requires the next page. A non-200 response, missing page or
unresolved physical parent leaves the snapshot unable to advance a checkpoint. Invalid
identities, duplicate observations, inconsistent cursors and scope mismatches are
rejected. Inputs are bounded to 16 pages, 256 KiB per page and 2,000 observations.

The projection retains hashed identities and selected core metadata only; input, output,
status messages and other fields are not copied. A record's revision identifies this
projection, not every field of the upstream observation. Parent relationships are
unverified candidates and missing parents are unlinked. Coverage remains partial, and
no record is a task outcome or establishes independence. A caller must protect the
original page bytes and authenticate and authorize their acquisition separately.

The [upstream v2 documentation](https://langfuse.com/docs/api-and-data-platform/features/public-api)
describes the field groups, physical parent IDs and cursor behavior. Remote retrieval,
usage and score mapping, durable revision reconciliation, and #38 interoperability
remain future work.
