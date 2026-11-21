"""Field-level mapping contract and loss report shared by the trajectory converters.

The table below is the contract: a field not listed for its object is undeclared, and every field a conversion
saw is counted in the report under the disposition recorded here. docs/atif-import-reference.md renders it.
"""
from collections import Counter
from dataclasses import dataclass, field

CONVERTER = 'ahl-atif-import-v1'
TARGET = 'ahl-telemetry-event-v2'

# retained: the value reaches the ledger as typed metadata. fingerprinted: only a content-free digest does.
# presence: only the fact that the field existed does. dropped: never copied. container: descended into.
RETAINED, FINGERPRINTED, PRESENCE, DROPPED, CONTAINER = 'retained', 'fingerprinted', 'presence', 'dropped', 'container'

TRAJECTORY, AGENT, FINAL, STEP, CALL, OBSERVATION, RESULT, REF, METRICS = (
    'trajectory', 'agent', 'final_metrics', 'steps[]', 'steps[].tool_calls[]', 'steps[].observation',
    'steps[].observation.results[]', 'steps[].observation.results[].subagent_trajectory_ref[]', 'steps[].metrics')

ATIF_FIELDS = {
    TRAJECTORY: dict(schema_version=RETAINED, session_id=DROPPED, trajectory_id=FINGERPRINTED, agent=CONTAINER, steps=CONTAINER,
        notes=DROPPED, final_metrics=CONTAINER, continued_trajectory_ref=DROPPED, extra=DROPPED, subagent_trajectories=CONTAINER),
    AGENT: dict(name=FINGERPRINTED, version=FINGERPRINTED, model_name=FINGERPRINTED, tool_definitions=DROPPED, extra=DROPPED),
    FINAL: dict(total_prompt_tokens=DROPPED, total_completion_tokens=DROPPED, total_cached_tokens=DROPPED,
        total_cost_usd=DROPPED, total_steps=DROPPED, extra=DROPPED),
    STEP: dict(step_id=RETAINED, timestamp=RETAINED, source=RETAINED, model_name=FINGERPRINTED, reasoning_effort=DROPPED,
        message=DROPPED, reasoning_content=PRESENCE, tool_calls=CONTAINER, observation=CONTAINER, metrics=CONTAINER,
        extra=DROPPED, llm_call_count=DROPPED, is_copied_context=PRESENCE),
    CALL: dict(tool_call_id=FINGERPRINTED, function_name=FINGERPRINTED, arguments=FINGERPRINTED, extra=DROPPED),
    OBSERVATION: dict(results=CONTAINER),
    RESULT: dict(source_call_id=FINGERPRINTED, content=DROPPED, subagent_trajectory_ref=CONTAINER, extra=DROPPED),
    REF: dict(trajectory_id=FINGERPRINTED, trajectory_path=DROPPED, session_id=DROPPED, extra=DROPPED),
    METRICS: dict(prompt_tokens=RETAINED, completion_tokens=RETAINED, cached_tokens=RETAINED, cost_usd=DROPPED,
        prompt_token_ids=DROPPED, completion_token_ids=DROPPED, logprobs=DROPPED, extra=DROPPED),
}
REQUIRED = {
    TRAJECTORY: ('schema_version', 'agent', 'steps'), AGENT: ('name', 'version'), FINAL: (), STEP: ('step_id', 'source', 'message'),
    CALL: ('tool_call_id', 'function_name', 'arguments'), OBSERVATION: ('results',), RESULT: (), REF: (), METRICS: (),
}

REASONS = (
    'input_unavailable', 'input_too_large', 'invalid_json', 'duplicate_json_key', 'nonfinite_number', 'nesting_limit_exceeded',
    'unsupported_schema_version', 'invalid_structure', 'missing_field', 'invalid_timestamp', 'invalid_measurement',
    'non_sequential_step_ids', 'duplicate_trajectory_id', 'unsupported_content_part', 'unsupported_nesting',
    'invalid_subagent_reference', 'subagent_reference_cycle', 'step_limit_exceeded', 'subagent_limit_exceeded', 'event_limit_exceeded',
    'output_exists', 'unsafe_output', 'output_unavailable', 'output_durability_unknown', 'unsupported_format')


class Rejected(ValueError):
    """A typed refusal. `where` is a schema path or a limit name, never a value taken from the input."""
    def __init__(self, reason, where='', detail=None):
        assert reason in REASONS, reason
        super().__init__(reason + (' at ' + where if where else '') + (' (' + detail + ')' if detail else ''))
        self.reason, self.where, self.detail = reason, where, detail


@dataclass
class ConversionReport:
    """What a conversion kept, dropped and could not know. Paths and counts only: no input value is ever recorded."""
    source_version: str
    source_digest: str
    target_version: str = TARGET
    converter: str = CONVERTER
    retained_fields: Counter = field(default_factory=Counter)
    dropped_fields: Counter = field(default_factory=Counter)
    missing_fields: Counter = field(default_factory=Counter)
    unsupported_features: Counter = field(default_factory=Counter)
    warnings: Counter = field(default_factory=Counter)
    counts: Counter = field(default_factory=Counter)

    def seen(self, prefix, key):
        """Count one present field under the disposition the mapping table gives it."""
        disposition = ATIF_FIELDS[prefix].get(key)
        path = prefix + '.' + key
        if disposition is None:
            self.dropped_fields[prefix + '.<undeclared>'] += 1
            self.warnings['undeclared_fields_dropped'] += 1
        elif disposition == DROPPED:
            self.dropped_fields[path] += 1
        elif disposition != CONTAINER:
            self.retained_fields[path] += 1
        return disposition

    def to_dict(self):
        ordered = lambda counter: dict(sorted(counter.items()))
        return dict(schema_version=1, converter=self.converter, source_version=self.source_version, target_version=self.target_version,
            source_digest=self.source_digest, lossy=True, round_trip='retained_metadata_only',
            retained_fields=sorted(self.retained_fields), dropped_fields=ordered(self.dropped_fields),
            missing_fields=ordered(self.missing_fields), unsupported_features=ordered(self.unsupported_features),
            warnings=ordered(self.warnings), counts=ordered(self.counts),
            outcome='unknown', independence='unknown', lineage_verified=False, publication_allowed=False,
            network_used=False, referenced_files_read=False)
