"""Strict, content-free v2 event envelope with v1 compatibility."""
import hashlib
import json
import math
import re

TEXT = {'case_id', 'event_id', 'activity', 'root_case_id', 'task_id', 'call_id',
        'actor', 'timestamp', 'tool_name', 'input_fingerprint', 'result_fingerprint',
        'state_revision', 'tool_effect', 'contract_ref', 'harness_fingerprint',
        'runtime_fingerprint', 'config_fingerprint', 'objective_id', 'phase', 'family',
        'skill_id', 'skill_hash', 'skill_signal', 'skill_signal_confidence', 'execution_status', 'capture_status',
        'source_format', 'source_version', 'grader_id', 'grader_version', 'grader_status',
        'outcome', 'outcome_provenance', 'missing_reason', 'reviewer_id', 'outcome_ref', 'lineage_source', 'requested_model_fingerprint',
        'native_turn_fingerprint', 'native_root_turn_fingerprint', 'native_response_fingerprint',
        'native_parent_thread_fingerprint', 'native_fork_parent_fingerprint', 'native_parent_message_fingerprint', 'native_message_fingerprint',
        'reported_model_fingerprint', 'native_stop_reason_fingerprint',
        'message_fingerprint', 'cwd_class', 'loading_signal', 'loading_evidence', 'loading_path_sha256', 'loading_content_sha256', 'agent_name_sha256', 'mcp_server_sha256'}
# Where a row's working directory sat relative to the project that attributed the session; never a path or name.
CWD_CLASSES = ('project_root', 'project_subdirectory', 'registered_worktree_of_project', 'other_location')
BOOL = {'lineage_verified', 'contract_violation', 'mutation', 'capture_complete'}
NUMERIC = {'elapsed_seconds', 'input_tokens', 'output_tokens'}
# A declared within-record subordinal: (segment, capture_order, suborder) under a pinned profile.
# It never enters event identity, revisions or fingerprints; see docs/analysis.md.
SOURCE_ORDER = ('source_order_profile', 'source_order_segment', 'source_suborder')
# Each profile belongs to one pinned producer; other source formats cannot carry it.
SOURCE_ORDER_PRODUCERS = {'claude-native-order-v1': ('claude', 'claude-jsonl-2.1.316')}
SOURCE_ORDER_PROFILES = frozenset(SOURCE_ORDER_PRODUCERS)
FIELDS = TEXT | BOOL | NUMERIC | {'schema_version', 'capture_order', *SOURCE_ORDER}
PROVENANCE = {'native_field', 'executable_grader', 'reviewed_annotation', 'model_hypothesis', 'unknown'}


# Derived from private tool-call bodies: kept out of duplicate checks and the body-insensitive metadata fingerprint of native captures.
BODY_DERIVED = frozenset(('input_fingerprint',))
FINGERPRINT = re.compile(r'[0-9a-f]{16}\Z')
MIN_FINGERPRINT_WORDS = 4


def message_fingerprint(text, case_id):
    """Content-free fingerprint of a user message, comparable only within one case; None when too short to be structural.

    Words are case-folded and punctuation and spacing are dropped, then hashed with the case id as salt and cut to
    64 bits. See docs/telemetry.md for the collision and privacy analysis.
    """
    words = re.findall(r'\w+', text.casefold())
    if len(words) < MIN_FINGERPRINT_WORDS:
        return None
    return hashlib.sha256((case_id + '\0' + ' '.join(words)).encode()).hexdigest()[:16]


def input_fingerprint(value, case_id):
    """Content-free fingerprint of one tool call's input, comparable only within one case; None when the input is unusable.

    A string that parses as JSON is canonicalized first, so key order and spacing do not matter. Same salt and
    64-bit cut as message_fingerprint; see docs/telemetry.md.
    """
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            pass
    try:
        body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return None
    return hashlib.sha256((case_id + '\0' + body).encode()).hexdigest()[:16]


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
        if key in TEXT and (not isinstance(val, str) or not val):
            raise ValueError('invalid normalized case identity' if key == 'case_id' else 'invalid metadata field: ' + key)
        if key in BOOL and type(val) is not bool:
            raise ValueError('invalid boolean: ' + key)
        if key in NUMERIC and (type(val) not in (float, int) or not math.isfinite(val) or val < 0):
            raise ValueError('invalid measurement: ' + key)
        if key == 'capture_order' and (type(val) is not int or val < 0):
            raise ValueError('invalid capture order')
    if 'cwd_class' in result and result['cwd_class'] not in CWD_CLASSES:
        raise ValueError('invalid working-directory class')
    if 'message_fingerprint' in result and (result['activity'] != 'user_message' or not FINGERPRINT.match(result['message_fingerprint'])):
        raise ValueError('invalid message fingerprint')
    declared = [key in result for key in SOURCE_ORDER]
    if any(declared) and not (all(declared) and 'capture_order' in result
            and isinstance(result['source_order_profile'], str) and result['source_order_profile'] in SOURCE_ORDER_PROFILES
            and SOURCE_ORDER_PRODUCERS[result['source_order_profile']] == (result.get('source_format'), result.get('source_version'))
            and all(type(result[key]) is int and result[key] >= 0 for key in SOURCE_ORDER[1:])):
        raise ValueError('invalid declared source order')
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
