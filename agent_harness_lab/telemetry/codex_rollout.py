"""Content-free projection of the pinned Codex 0.166.0 rollout wire format."""
from datetime import datetime
import hashlib
import io
import json
from pathlib import Path
import time
from .adapters import correlate
from .schema import event
from ..evaluation.integrity import unique_object
from ..sources.schema import digest

VERSION = 'codex-rollout-0.166.0'
UPSTREAM_COMMIT = '6ff1e1ca3b30fc8b914720681fcf5fe62e95f856'
KNOWN_TOOLS = ('exec_command', 'write_stdin', 'apply_patch', 'shell', 'shell_command',
    'update_plan', 'view_image', 'request_user_input')
ACTIVITIES = dict(turn_started='turn_start', turn_complete='turn_stop', turn_aborted='cancelled',
    context_compacted='compaction', user_message='user_message', agent_message='agent_message',
    agent_reasoning='reasoning', agent_reasoning_raw_content='reasoning')


def text(value):
    if not isinstance(value, str) or not 0 < len(value) <= 4096: raise ValueError('invalid native metadata string')
    return value


def number(value, maximum=2**63-1):
    if type(value) is not int or not 0 <= value <= maximum: raise ValueError('invalid native metadata integer')
    return value


def invalid_constant(_): raise ValueError('nonfinite native JSON is unsupported')


class Projection:
    def __init__(self, project):
        self.project, self.meta = Path(project), None
        self.rows, self.ordinals, self.responses = [], {}, {}
        self.skipped, self.duplicates, self.unsupported = 0, 0, 0
        self.last_ordinal = -1

    def add(self, row, position):
        kind, payload = row.get('type'), row.get('payload')
        if not isinstance(kind, str) or not isinstance(payload, dict): raise ValueError('invalid native rollout envelope')
        stamp = text(row.get('timestamp'))
        try: parsed = datetime.fromisoformat(stamp)
        except ValueError: raise ValueError('invalid native rollout timestamp') from None
        if parsed.tzinfo is None: raise ValueError('native timestamp requires a timezone')
        if self.meta is None:
            if kind != 'session_meta' or payload.get('cli_version') != '0.166.0':
                raise ValueError('unsupported native Codex version or missing session metadata')
            if Path(text(payload.get('cwd'))) != self.project: raise ValueError('native capture project mismatch')
            if payload.get('history_mode', 'legacy') not in ('legacy', 'paginated'): raise ValueError('unsupported native history mode')
            for key in ('id', 'session_id'): text(payload.get(key, payload.get('id')))
            for key in ('parent_thread_id', 'forked_from_id'):
                if payload.get(key) is not None: text(payload[key])
            base = payload.get('history_base')
            if base is not None:
                if (payload.get('history_mode') != 'paginated' or not isinstance(base, dict)
                        or set(base) != {'thread_id', 'end_ordinal_exclusive', 'end_byte_offset'}):
                    raise ValueError('invalid native paginated history base')
                text(base['thread_id'])
                for key in ('end_ordinal_exclusive', 'end_byte_offset'): number(base[key], 2**64-1)
            boundary = payload.get('subagent_history_start_ordinal')
            if boundary is not None: number(boundary, 2**64-1)
            if payload.get('forked_from_ordinal_exclusive') is not None: number(payload['forked_from_ordinal_exclusive'], 2**64-1)
            if (payload.get('parent_thread_id') or payload.get('forked_from_id')) and boundary is None and base is None:
                raise ValueError('native inherited-history ownership is unavailable')
            if payload.get('history_mode') == 'paginated' and row.get('ordinal') != (base['end_ordinal_exclusive'] if base else 0):
                raise ValueError('native paginated header ordinal does not match its physical base')
            self.meta = payload
        elif kind == 'session_meta':
            raise ValueError('multiple native session headers are unsupported')
        ordinal = row.get('ordinal')
        if self.meta.get('history_mode') == 'paginated' and ordinal is None: raise ValueError('native paginated record has no ordinal')
        if ordinal is not None:
            number(ordinal, 2**64-1); fingerprint = digest(row)
            if ordinal in self.ordinals:
                if self.ordinals[ordinal] != fingerprint: raise ValueError('conflicting native rollout ordinal')
                self.duplicates += 1; return
            if ordinal < self.last_ordinal: raise ValueError('native rollout ordinal moved backwards')
            self.ordinals[ordinal] = fingerprint
            self.last_ordinal = ordinal
        boundary = self.meta.get('subagent_history_start_ordinal')
        if kind != 'session_meta' and boundary is not None and (ordinal is None or ordinal < boundary):
            self.skipped += 1; return
        thread = self.meta['id']; root = self.meta.get('session_id', thread)
        scoped = lambda value: digest(('codex-native-id-v1', str(self.project), value))
        record = dict(case_id=scoped(thread), root_case_id=scoped(root), lineage_verified=False,
            event_id=digest((thread, ('ordinal', ordinal) if ordinal is not None else ('line', position))),
            timestamp=stamp, activity='capture_coverage', capture_complete=False, source_format='codex', source_version=VERSION,
            runtime_fingerprint=digest(('codex', '0.166.0')), capture_order=position)
        if kind == 'session_meta': record['activity'] = 'session_start'
        elif kind == 'turn_context':
            if Path(text(payload.get('cwd'))) != self.project: raise ValueError('native turn project mismatch')
            record['requested_model_fingerprint'] = digest(text(payload.get('model')))
            record['activity'] = 'turn_context'
        elif kind == 'response_item':
            item = payload.get('type')
            if item in ('function_call', 'custom_tool_call', 'local_shell_call', 'function_call_output', 'custom_tool_call_output'):
                output = item.endswith('_output')
                record['activity'] = 'tool_result' if output else 'tool_call'
                if payload.get('call_id') is not None: record['call_id'] = scoped(text(payload['call_id']))
                else: record['missing_reason'] = 'native_call_id_unavailable'
                if not output:
                    name = 'shell' if item == 'local_shell_call' else text(payload.get('name'))
                    namespace = payload.get('namespace')
                    if namespace is not None: text(namespace)
                    record['tool_name'] = name if name in KNOWN_TOOLS and namespace in (None, 'functions') else 'tool:'+digest((namespace, name))
            elif item in ('message', 'reasoning'):
                record['activity'] = item
            else: self.unsupported += 1; record['missing_reason'] = 'unsupported_native_item'
        elif kind == 'token_usage_record':
            if payload.get('thread_id') != thread or payload.get('session_id') != root:
                self.skipped += 1; return
            response = text(payload.get('response_id')); usage = payload.get('usage')
            if not isinstance(usage, dict): raise ValueError('native response usage is unavailable')
            counts = (number(usage.get('input_tokens')), number(usage.get('output_tokens')))
            identity = (text(payload.get('turn_id')), text(payload.get('root_turn_id')), counts)
            if response in self.responses:
                if self.responses[response] != identity: raise ValueError('conflicting native response usage')
                self.duplicates += 1; return
            self.responses[response] = identity
            record.update(activity='response_usage', input_tokens=counts[0], output_tokens=counts[1])
        elif kind == 'compacted': record['activity'] = 'compaction'
        elif kind == 'event_msg':
            item = payload.get('type')
            if item in ACTIVITIES: record['activity'] = ACTIVITIES[item]
            elif item == 'exec_command_end':
                code = payload.get('exit_code')
                if type(code) is not int or not -(2**31) <= code < 2**31: raise ValueError('invalid native command exit code')
                record.update(activity='tool_execution', call_id=scoped(text(payload.get('call_id'))),
                    execution_status='returned' if code == 0 else 'error')
            elif item == 'token_count': record['missing_reason'] = 'cumulative_usage_not_counted'
            else: self.unsupported += 1; record['missing_reason'] = 'unsupported_native_event'
        else: self.unsupported += 1; record['missing_reason'] = 'unsupported_native_item'
        if kind in ('turn_context', 'token_usage_record') or kind == 'event_msg' and payload.get('type') in ('turn_started', 'turn_complete', 'turn_aborted', 'exec_command_end'):
            for field in ('turn_id', 'root_turn_id'):
                if payload.get(field) is not None:
                    owner = root if field == 'root_turn_id' else thread
                    record['native_'+field.removesuffix('_id')+'_fingerprint'] = digest(('codex-'+field, str(self.project), owner, text(payload[field])))
        self.rows.append(event(record))


def read(path, project, max_records=100000):
    from ..config import supported
    if not supported(): raise NotImplementedError('native capture requires safe filesystem APIs')
    from ..sources.git_view import Folder
    if type(max_records) is not int or not 0 < max_records <= 100000: raise ValueError('invalid native capture record limit')
    path = Path(path).absolute(); projection = Projection(project)
    partial = False
    with Folder(path.parent, time.monotonic()+10) as folder:
        raw = folder.read(path.name, 64*1024*1024)
        if raw is None: raise ValueError('native capture is unavailable')
        stream = io.BytesIO(raw)
        for position in range(max_records+1):
            folder.check(); line = stream.readline(1024*1024+1)
            if not line: break
            if position == max_records or len(line) > 1024*1024: raise ValueError('native capture record budget exceeded')
            if not line.endswith(b'\n'): partial = True; break
            try: row = json.loads(line, object_pairs_hook=unique_object, parse_constant=invalid_constant)
            except (ValueError, UnicodeError, RecursionError): raise ValueError('invalid native rollout JSON') from None
            if not isinstance(row, dict): raise ValueError('invalid native rollout record')
            projection.add(row, position)
        if projection.meta is None: raise ValueError('native capture has no complete session metadata')
        folder.verify()
    common = {k: projection.rows[0][k] for k in ('case_id', 'root_case_id', 'lineage_verified', 'capture_complete',
        'source_format', 'source_version', 'runtime_fingerprint')}
    records = tuple(event({**common, **r}) for r in correlate(projection.rows))
    summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial', partial_tail=partial,
        inherited_records_skipped=projection.skipped, duplicates_skipped=projection.duplicates, unsupported_records=projection.unsupported,
        served_model='unknown', effective_settings='unknown', independence='unknown', outcome='unknown', source_read=True, network_used=False)
    return records, summary, hashlib.sha256(raw).hexdigest()
