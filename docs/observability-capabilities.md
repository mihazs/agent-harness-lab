# Observability source capability matrix

This is a planning reference for read-only observability ingestion, not a list of
implemented AHL connectors. It records public documentation checked on 2026-11-17;
verify the actual deployment and schema before enabling a future remote adapter.
The [source contracts](source-contracts.md) define the existing authorization and
private-evidence boundaries. No network access or source ingestion is added here.

| Source | Documented retrieval and scope | Pagination and compatibility | Privacy and coverage limits | AHL status |
| --- | --- | --- | --- | --- |
| Langfuse | Project-scoped [public API](https://langfuse.com/docs/api-and-data-platform/features/public-api); Observations API v2 returns observation records and supports an explicit time range. Its API uses a project public/secret key pair with Basic authentication. | V2 uses `meta.cursor`; an empty page can still have a next cursor. Self-hosted deployments from v4.36.0 expose their own OpenAPI document; check older instances separately. | Selected fields and identifiers can still be sensitive. Delayed or missing observations do not mean an error-free or complete run. | Remote adapter not implemented. |
| Opik | The [export documentation](https://www.comet.com/docs/opik/tracing/advanced/export-data) describes trace and span exports and project-scoped files. Deployment-specific credentials and authorization must be verified before use. | The documented REST trace and span endpoints are paginated and rate limited; retries must account for `429` and the documented reset header. Server and API version support remain unverified here. | Exports may contain input, output and metadata. A future importer must project an allowlist, not copy payloads by default. | Remote and offline provider adapters not implemented. |
| Sentry | [Project error events](https://docs.sentry.io/api/events/list-a-projects-error-events/) use bearer authentication and document `project:read` as one accepted scope. | [Link-header cursors](https://docs.sentry.io/api/pagination/) include a `results` indicator; verify the deployment's endpoint and retention. | Full event bodies can contain stack traces and other sensitive data. Production errors are not agent tool outcomes or task grades. | Adapter not implemented. |
| OpenTelemetry | The [GenAI semantic conventions](https://github.com/open-telemetry/semantic-conventions-genai) describe telemetry fields; the conventions alone do not define a particular provider's access API. | Schema version, transport, completeness and resource scope depend on the producer and selected export. | Spans and attributes may contain content. Nested usage must not be summed without evidence that measurements are disjoint. | Trace interoperability is separate work; no remote collector is claimed here. |
| Phoenix, LangSmith, MLflow, AgentOps and Sentry alternatives | Exact supported versions, read scopes and export schemas have not been verified in this matrix. | Unknown. | Unknown coverage stays unknown. | Not implemented. |

## Admission requirements

Future adapters must use an explicitly authorized source and a bounded time range,
page count and retry budget. They must keep credentials and raw private bodies out of
logs and public artifacts, and project only approved metadata into private evidence.
Join trace, span, dataset, item, experiment and score records only on explicit IDs;
missing joins and missing versions remain visible. A provider-reported score, error or
success is an external observation, not an independent AHL task outcome. Repeated or
revised imports must not silently overwrite immutable evidence. Rate limits, expired
data and unsupported versions are incomplete coverage, not zero events.
