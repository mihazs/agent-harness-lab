"""Strict, content-free v2 event envelope with v1 compatibility."""
import json
import math

TEXT = {'case_id', 'event_id', 'activity', 'root_case_id', 'task_id', 'call_id',
        'actor', 'timestamp', 'tool_name', 'input_fingerprint', 'result_fingerprint',
        'state_revision', 'tool_effect', 'contract_ref', 'harness_fingerprint',
        'runtime_fingerprint', 'config_fingerprint', 'objective_id', 'phase', 'family',
        'skill_id', 'skill_hash', 'skill_signal', 'execution_status', 'capture_status',
        'source_format', 'source_version', 'grader_id', 'grader_version', 'grader_status',
        'outcome', 'outcome_provenance', 'missing_reason', 'reviewer_id', 'outcome_ref', 'lineage_source', 'requested_model_fingerprint',
        'native_turn_fingerprint', 'native_root_turn_fingerprint', 'native_response_fingerprint',
        'native_parent_thread_fingerprint', 'native_fork_parent_fingerprint', 'native_parent_message_fingerprint', 'native_message_fingerprint',
        'reported_model_fingerprint', 'native_stop_reason_fingerprint'}
BOOL = {'lineage_verified', 'contract_violation', 'mutation', 'capture_complete'}
NUMERIC = {'elapsed_seconds', 'input_tokens', 'output_tokens'}
FIELDS = TEXT | BOOL | NUMERIC | {'schema_version', 'capture_order'}
PROVENANCE = {'native_field', 'executable_grader', 'reviewed_annotation', 'model_hypothesis', 'unknown'}


def event(value):
    if not isinstance(value, dict):
        raise ValueError('event must be an object')
    version = value.get('schema_version', 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError('unsupported event schema version')
    result = {key: val for key, val in value.items() if key in FIELDS and val is not None}
    for key in ('case_id', 'event_id', 'activity'):
        if key not in result:
            raise ValueError('invalid normalized case identity' if key == 'case_id' else 'missing event identity/activity')
    for key, val in result.items():
        if key in TEXT and (not isinstance(val, str) or not 0 < len(val) <= 256):
            raise ValueError('invalid normalized case identity' if key == 'case_id' else 'invalid metadata field: ' + key)
        if key in BOOL and type(val) is not bool:
            raise ValueError('invalid boolean: ' + key)
        if key in NUMERIC and (type(val) not in (float, int) or not math.isfinite(val) or val < 0):
            raise ValueError('invalid measurement: ' + key)
        if key == 'capture_order' and (type(val) is not int or val < 0):
            raise ValueError('invalid capture order')
    result['schema_version'] = 2
    result.setdefault('outcome', 'unknown')
    result.setdefault('outcome_provenance', 'unknown')
    result.setdefault('capture_status', 'unverified')
    if result['outcome_provenance'] not in PROVENANCE:
        raise ValueError('unsupported provenance')
    if result['outcome'] not in ('success', 'failure', 'unknown'):
        # v1 free-form outcomes are observations, not verified binary labels.
        if version == 1:
            result['outcome'] = 'unknown'
            result['outcome_provenance'] = 'unknown'
        else:
            raise ValueError('unsupported outcome')
    if result['outcome'] == 'unknown':
        result.setdefault('missing_reason', 'no_independent_outcome')
    return result


def verified_outcome(value):
    record = event(value)
    if record['outcome_provenance'] not in ('executable_grader', 'reviewed_annotation'):
        return None
    if record['outcome_provenance'] == 'reviewed_annotation' and (
            not record.get('reviewer_id') or not record.get('outcome_ref')):
        return None
    if record['outcome_provenance'] == 'executable_grader' and (
            record.get('grader_status') != 'valid' or not record.get('grader_id') or not record.get('grader_version')):
        return None
    return {'success': True, 'failure': False}.get(record['outcome'])


def serialize(value):
    return json.dumps(event(value), sort_keys=True, separators=(',', ':'), allow_nan=False)
