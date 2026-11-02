"""Content-free Claude Code 2.1.316 records; native claims are not execution proofs."""
from pathlib import Path
from .native import guarded, jsonl, number, text, timestamp
from .schema import event
from ..sources.schema import digest

VERSION = 'claude-jsonl-2.1.316'
UPSTREAM_COMMIT = '2146f9d8985513053e904b13200c8f744b36785a'
ENTRY_TYPES = ('user', 'assistant', 'system', 'progress', 'attachment')
KNOWN_TOOLS = ('Bash', 'Read', 'Write', 'Edit', 'MultiEdit', 'Glob', 'Grep', 'Agent', 'Task', 'Skill',
    'WebFetch', 'WebSearch', 'NotebookEdit', 'TodoWrite', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode')


class Projection:
    def __init__(self, project, limit, check, revisions=None):
        self.project, self.session, self.writer = Path(project), None, None
        self.limit, self.check = limit, check
        self.revisions, self.raw_revisions = revisions, {}
        self.rows, self.seen, self.responses = [], {}, {}
        self.unsupported, self.duplicates, self.unattributed = 0, 0, 0

    def scoped(self, *values): return digest(('claude-native-id-v1', str(self.project), *values))

    def add(self, row, position):
        if row.get('version') is not None and row['version'] != '2.1.316': raise ValueError('unsupported native Claude version')
        if row.get('cwd') is not None and Path(text(row['cwd'])) != self.project: raise ValueError('native Claude project mismatch')
        if row.get('sessionId') is not None:
            session = text(row['sessionId'])
            if self.session is not None and self.session != session: raise ValueError('mixed native Claude sessions')
            self.session = session
        kind = row.get('type')
        if kind not in ENTRY_TYPES:
            self.unsupported += 1; return
        if row.get('version') != '2.1.316' or row.get('cwd') is None or row.get('sessionId') is None:
            raise ValueError('native Claude envelope lacks explicit scope/version')
        sidechain = row.get('isSidechain')
        if type(sidechain) is not bool: raise ValueError('native Claude writer type unavailable')
        agent, team = row.get('agentId'), row.get('teamName')
        if agent is not None: text(agent)
        if team is not None: text(team)
        if sidechain and agent is None: raise ValueError('native Claude sidechain owner unavailable')
        writer = (sidechain, agent, team)
        if self.writer is not None and writer != self.writer: raise ValueError('mixed native Claude writers')
        self.writer = writer
        ident = text(row.get('uuid')); stamp = timestamp(row.get('timestamp'))
        case = self.scoped(self.session, writer)
        base = dict(case_id=case, root_case_id=case, lineage_verified=False, timestamp=stamp,
            capture_complete=False, source_format='claude', source_version=VERSION,
            runtime_fingerprint=digest(('claude-code', '2.1.316')), capture_order=position,
            native_message_fingerprint=self.scoped(self.session, writer, ident))
        if row.get('parentUuid') is not None:
            base['native_parent_message_fingerprint'] = self.scoped(self.session, writer, text(row['parentUuid']))
        for flag in ('isMeta', 'isCompactSummary'):
            if flag in row and type(row[flag]) is not bool: raise ValueError('invalid native Claude context flag')
        observations, usage, stop, response = [], None, None, None
        raw_revision = digest(row) if self.revisions is not None else None
        if ident in self.raw_revisions and self.raw_revisions[ident] != raw_revision:
            raise ValueError('conflicting native Claude record revisions')
        previous = self.seen.get(ident)
        quota = previous[1] if previous is not None else self.limit-len(self.rows)
        def emit(activity, index=0, **metadata):
            self.check()
            if len(observations) >= quota: raise ValueError('native Claude normalized event budget exceeded')
            observations.append(event(dict(base, event_id=self.scoped(self.session, writer, ident, index), activity=activity, **metadata)))
        if row.get('isCompactSummary') or kind == 'system' and row.get('subtype') == 'compact_boundary':
            emit('compaction')
        elif row.get('isMeta'):
            emit('context')
        elif kind in ('user', 'assistant'):
            message = row.get('message')
            if not isinstance(message, dict) or message.get('role') != kind: raise ValueError('invalid native Claude message')
            if kind == 'assistant':
                if message.get('model') is not None: base['reported_model_fingerprint'] = digest(text(message['model']))
                if message.get('id') is not None:
                    response = self.scoped('response', text(message['id']))
                    base['native_response_fingerprint'] = response
                if message.get('stop_reason') is not None: stop = digest(text(message['stop_reason']))
                if message.get('usage') is not None:
                    raw = message['usage']
                    if not isinstance(raw, dict): raise ValueError('invalid native Claude usage')
                    for name in ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens'):
                        if name in raw: number(raw[name])
                    usage = (raw.get('input_tokens'), raw.get('output_tokens'), base.get('reported_model_fingerprint'))
                    if response is None or any(value is None for value in usage[:2]): self.unattributed += 1
            content = message.get('content')
            if isinstance(content, str): content = [dict(type='text')]
            if not isinstance(content, list) or len(content) > 4096: raise ValueError('invalid native Claude content blocks')
            for index, block in enumerate(content):
                if not isinstance(block, dict): raise ValueError('invalid native Claude content block')
                block_type = block.get('type')
                if block_type == 'text': emit('user_message' if kind == 'user' else 'agent_message', index)
                elif block_type in ('thinking', 'redacted_thinking') and kind == 'assistant': emit('reasoning', index)
                elif block_type == 'tool_use' and kind == 'assistant':
                    name = text(block.get('name')); metadata = dict(tool_name=name if name in KNOWN_TOOLS else 'tool:'+digest(name))
                    if block.get('id') is not None: metadata['call_id'] = self.scoped(self.session, writer, text(block['id']))
                    else: metadata['missing_reason'] = 'native_call_id_unavailable'
                    emit('tool_call', index, **metadata)
                elif block_type == 'tool_result' and kind == 'user':
                    metadata = dict(execution_status='unknown')
                    if block.get('tool_use_id') is not None: metadata['call_id'] = self.scoped(self.session, writer, text(block['tool_use_id']))
                    else: metadata['missing_reason'] = 'native_call_id_unavailable'
                    if 'is_error' in block:
                        if type(block['is_error']) is not bool: raise ValueError('invalid native Claude tool status')
                        metadata['execution_status'] = 'error' if block['is_error'] else 'returned'
                    emit('tool_result', index, **metadata)
                else:
                    self.unsupported += 1; emit('capture_coverage', index, missing_reason='unsupported_native_block')
            if not content: emit('capture_coverage', missing_reason='empty_native_message')
        else:
            self.unsupported += 1; emit('capture_coverage', missing_reason='unsupported_native_entry')
        signature = digest(dict(events=[{k: v for k, v in e.items() if k != 'capture_order'} for e in observations], usage=usage, stop=stop))
        if previous is not None:
            if previous[0] != signature: raise ValueError('conflicting native Claude UUID metadata')
            self.duplicates += 1; return
        self.seen[ident] = (signature, len(observations))
        if self.revisions is not None:
            self.raw_revisions[ident] = raw_revision
            for observation in observations:
                self.revisions[(observation['case_id'], observation['event_id'])] = raw_revision
        self.rows.extend(observations)
        if response is not None:
            receipt = self.responses.setdefault(response, dict(base=dict(base), usage={}, stops={}, models=set()))
            if base.get('reported_model_fingerprint') is not None: receipt['models'].add(base['reported_model_fingerprint'])
            if usage is not None: receipt['usage'].setdefault(usage, dict(base))
            if stop is not None: receipt['stops'].setdefault(stop, dict(base))

    def finish(self, check):
        if self.writer is None: raise ValueError('native Claude capture has no scoped records')
        conflicts = 0
        for response, receipt in self.responses.items():
            for facet, values in (('usage', receipt['usage']), ('stop', receipt['stops'])):
                check()
                if not values: continue
                if len(self.rows) >= self.limit: raise ValueError('native Claude response event budget exceeded')
                conflicting = len(receipt['models']) > 1
                supporting = None
                if facet == 'usage':
                    conflicting |= any(len({value[index] for value in values if value[index] is not None}) > 1 for index in (0, 1))
                    supporting = next(((value, base) for value, base in values.items() if all(v is not None for v in value[:2])), None)
                else:
                    conflicting |= len(values) > 1
                    supporting = next(iter(values.items()))
                base = dict(supporting[1]) if supporting is not None and not conflicting else {
                    k: v for k, v in receipt['base'].items() if k not in
                    ('reported_model_fingerprint', 'native_message_fingerprint', 'native_parent_message_fingerprint')}
                record = dict(base, event_id=self.scoped('response-facet', response, facet), activity='capture_coverage')
                if conflicting:
                    conflicts += 1; record['missing_reason'] = 'conflicting_native_response_'+facet
                elif supporting is None: record['missing_reason'] = 'incomplete_native_response_'+facet
                elif facet == 'usage':
                    input_tokens, output_tokens, _ = supporting[0]
                    record.update(activity='response_usage', input_tokens=input_tokens, output_tokens=output_tokens)
                else: record.update(activity='response_stop', native_stop_reason_fingerprint=supporting[0])
                self.rows.append(event(record))
        return tuple(sorted(self.rows, key=lambda r: r['capture_order'])), conflicts


def decode(folder, name, project, max_records, *, revisions=None):
    projection = Projection(project, max_records, folder.check, revisions)
    sha256, partial, count = jsonl(folder, name, max_records, projection.add)
    rows, conflicts = projection.finish(folder.check)
    if len(rows) > max_records: raise ValueError('native Claude normalized event budget exceeded')
    summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial', partial_tail=partial,
        inherited_records_skipped=0, duplicates_skipped=projection.duplicates, unsupported_records=projection.unsupported,
        input_records=count, session_sha256=projection.scoped('session', projection.session), conflicting_response_facets=conflicts, unattributed_usage_records=projection.unattributed,
        usage_semantics='observed_uncached_counts_only', served_model='unknown', effective_settings='unknown',
        independence='unknown', outcome='unknown', parent_session='unknown', source_read=True, network_used=False)
    return rows, summary, sha256


def read(path, project, max_records=100000):
    return guarded(path, project, decode, max_records)
