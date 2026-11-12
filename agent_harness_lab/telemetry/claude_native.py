"""Content-free Claude Code native records for the fixture-verified releases; native claims are not execution proofs."""
import hashlib
from collections import Counter
import re
from pathlib import Path, PurePosixPath
from .native import guarded, jsonl, number, text, timestamp
from .schema import SOURCE_ORDER, event, message_fingerprint
from ..sources.config_inventory import telemetry_server_sha256
from ..sources.schema import digest
from ..surfaces import skill_prefixes

VERSION = 'claude-jsonl-2.1.316'
RUNTIME_VERSION = '2.1.316'  # the first verified release; source-journal profiles stay exact at this version
# Declared compatibility range: every release in it needs a contract fixture (tests/fixtures/claude_contract/<version>.jsonl);
# a test refuses a version without one. Add a release by adding its fixture, then widening or merging the entries.
VERIFIED_RANGES = (('2.1.316', '2.1.316'), ('2.1.318', '2.1.318'))


def release(version):
    parts = version.split('.') if isinstance(version, str) else ()
    return tuple(int(part) for part in parts) if len(parts) == 3 and all(part.isdigit() for part in parts) else None


def expand(low, high):
    """Every patch release from low to high within one minor line; ranges never cross a major or minor boundary."""
    first, last = release(low), release(high)
    if first is None or last is None or first[:2] != last[:2] or first > last: raise ValueError('invalid verified range')
    return tuple('%d.%d.%d' % (*first[:2], patch) for patch in range(first[2], last[2]+1))


VERIFIED = tuple(version for low, high in VERIFIED_RANGES for version in expand(low, high))
NEWEST_VERIFIED = max(VERIFIED, key=release)
EXACT = (RUNTIME_VERSION,)
# Declared subordinal within one native line of one file (segment); never execution or cross-file order.
ORDER_PROFILE = 'claude-native-order-v1'
UNORDERED = frozenset(('capture_order', *SOURCE_ORDER))
UPSTREAM_COMMIT = '2146f9d8985513053e904b13200c8f744b36785a'
ENTRY_TYPES = ('user', 'assistant', 'system', 'progress', 'attachment')
# Record kinds the verified releases write that carry no conversation event; skipped, counted and typed per kind.
# Any other kind is skipped as unknown_entry_type. Extra keys on known kinds are ignored: only named fields are read.
NON_EVENT_TYPES = ('file-history-snapshot', 'queue-operation', 'last-prompt', 'ai-title', 'mode', 'atis-latch', 'api-request',
    'api-request-shape', 'api-request-blob', 'tools-used', 'file-history-delta', 'cost-state')
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,47}\Z')


def type_label(kind):
    """An entry type as a printable schema name; anything else is 'unrecognized' so no capture text passes through."""
    return kind if isinstance(kind, str) and NAME.match(kind) else 'unrecognized'


def skip_reason(label): return 'declared_non_event_entry' if label in NON_EVENT_TYPES else 'unknown_entry_type'


def skipped_types(counter):
    return {label: dict(count=count, reason=skip_reason(label)) for label, count in sorted(counter.items())}
KNOWN_TOOLS = ('Bash', 'Read', 'Write', 'Edit', 'MultiEdit', 'Glob', 'Grep', 'Agent', 'Task', 'Skill',
    'WebFetch', 'WebSearch', 'NotebookEdit', 'TodoWrite', 'AskUserQuestion', 'EnterPlanMode', 'ExitPlanMode')
# Project-owned bundle layout from the surface classifier; only an exact entry-point path under the project qualifies.
SKILL_BUNDLE_PREFIXES = tuple(PurePosixPath(root).parts for root in skill_prefixes())
# Assumed native inputs (not pinned by the SDK wire reference): Skill `skill` names a skill; Read `file_path` is
# absolute and `offset`/`limit`/`pages` select a window. Signals are declared requests, never observed activation.
CAPABILITY = dict(version=VERSION, call_ids=True, outcome=False, skill_activation=False, skill_signal='observed_declared',
    loading_signal='observed_pattern', effective_loading=False)
# Assumed attachment payloads, absent from the pinned SDK reference and verified only against synthetic fixtures:
# nested_memory carries an absolute `path` and optionally `content.content` text (a rule file arrives the same way);
# hook output attachments carry no file path. Any other attachment type stays unsupported_native_entry.
MEMORY_ATTACHMENT = 'nested_memory'
HOOK_ATTACHMENTS = ('hook_success', 'hook_additional_context', 'hook_non_blocking_error', 'hook_blocking_error')
AGENT_TOOLS = ('Agent', 'Task')


def skill_fingerprint(name):
    """Pseudonymous label shared by a declared Skill name and a bundle directory name; not anonymization."""
    return digest(('claude-skill-name-v1', name))


def project_parts(project, path):
    """Parts of an exact absolute path below project, else None; no normalization, so no equivalent spelling matches."""
    if not isinstance(path, str) or not path: return None
    candidate = PurePosixPath(path)
    if str(candidate) != path or not candidate.is_absolute() or '..' in candidate.parts: return None
    try: return candidate.relative_to(PurePosixPath(str(project))).parts
    except ValueError: return None


def attachment_signal(project, attachment):
    """Content-free observed_pattern metadata for an assumed attachment shape; None keeps the entry unsupported.

    loading_path_sha256 is schema.digest of the project-relative path, the inventory path_sha256, and
    loading_content_sha256 hashes the attached text; neither proves the runtime applied the file.
    """
    if not isinstance(attachment, dict): return None
    kind, signal = attachment.get('type'), dict(loading_evidence='observed_pattern')
    if kind in HOOK_ATTACHMENTS: return dict(signal, loading_signal='observed_hook_output')
    path, content = attachment.get('path'), attachment.get('content')
    if kind != MEMORY_ATTACHMENT or not isinstance(path, str) or not PurePosixPath(path).is_absolute(): return None
    if content is not None and (not isinstance(content, dict) or content.get('path', path) != path): return None
    signal['loading_signal'] = 'observed_attachment'
    parts = project_parts(project, path)
    if parts: signal['loading_path_sha256'] = digest(PurePosixPath(*parts).as_posix())
    body = content.get('content') if content is not None else None
    if isinstance(body, str):
        signal['loading_content_sha256'] = hashlib.sha256(body.encode('utf-8', 'surrogatepass')).hexdigest()
    return signal


def invocation_signal(tool, arguments):
    """Declared agent or MCP server request digests; the MCP tool itself stays an opaque tool digest."""
    server = telemetry_server_sha256(tool)
    if server is not None: return dict(loading_signal='declared_mcp_invocation', mcp_server_sha256=server)
    name = arguments.get('subagent_type') if tool in AGENT_TOOLS and isinstance(arguments, dict) else None
    if not isinstance(name, str) or not name: return None
    # agents_inventory records name_sha256 = digest(name) for a declared agent, so the two digests can meet.
    return dict(loading_signal='declared_agent_invocation', agent_name_sha256=digest(name))


def skill_signal(project, tool, arguments):
    """Content-free metadata for a Skill name or an exact project SKILL.md read request; otherwise None."""
    if not isinstance(arguments, dict): return None
    if tool == 'Skill':
        name = arguments.get('skill')
        if not isinstance(name, str) or not name: return None
        return dict(skill_signal='declared_invocation', skill_id=skill_fingerprint(name), skill_signal_confidence='declared_name')
    parts = project_parts(project, arguments.get('file_path') if tool == 'Read' else None)
    if parts is None or len(parts) < 3 or parts[-1] != 'SKILL.md' or parts[:-2] not in SKILL_BUNDLE_PREFIXES: return None
    partial = any(arguments.get(key) is not None for key in ('offset', 'limit', 'pages'))
    return dict(skill_signal='file_read', skill_id=skill_fingerprint(parts[-2]),
        skill_signal_confidence='partial_read_request' if partial else 'full_read_request')


class Projection:
    def __init__(self, project, limit, check, revisions=None, versions=VERIFIED):
        self.versions, self.recorded, self.skipped = versions, set(), Counter()
        self.project, self.session, self.writer = Path(project), None, None
        self.limit, self.check = limit, check
        self.revisions, self.raw_revisions = revisions, {}
        self.rows, self.seen, self.responses = [], {}, {}
        self.unsupported, self.duplicates, self.unattributed = 0, 0, 0
        self.segment = 0  # physical file within this projection; segments have no established order

    def scoped(self, *values): return digest(('claude-native-id-v1', str(self.project), *values))

    def add(self, row, position):
        if row.get('version') is not None and row['version'] not in self.versions: raise ValueError('unsupported native Claude version')
        if row.get('cwd') is not None and Path(text(row['cwd'])) != self.project: raise ValueError('native Claude project mismatch')
        if row.get('sessionId') is not None:
            session = text(row['sessionId'])
            if self.session is not None and self.session != session: raise ValueError('mixed native Claude sessions')
            self.session = session
        kind = row.get('type')
        if kind not in ENTRY_TYPES:
            self.unsupported += 1; self.skipped[type_label(kind)] += 1; return
        if row.get('version') not in self.versions or row.get('cwd') is None or row.get('sessionId') is None:
            raise ValueError('native Claude envelope lacks explicit scope/version')
        sidechain = row.get('isSidechain')
        if type(sidechain) is not bool: raise ValueError('native Claude writer type unavailable')
        agent, team = row.get('agentId'), row.get('teamName')
        if agent is not None: text(agent)
        if team is not None: text(team)
        if sidechain and agent is None: raise ValueError('native Claude sidechain owner unavailable')
        self.recorded.add(row['version'])
        writer = (sidechain, agent, team)
        if self.writer is not None and writer != self.writer: raise ValueError('mixed native Claude writers')
        self.writer = writer
        ident = text(row.get('uuid')); stamp = timestamp(row.get('timestamp'))
        case = self.scoped(self.session, writer)
        base = dict(case_id=case, root_case_id=case, lineage_verified=False, timestamp=stamp,
            capture_complete=False, source_format='claude', source_version=VERSION,
            runtime_fingerprint=digest(('claude-code', row['version'])), capture_order=position,
            source_order_profile=ORDER_PROFILE, source_order_segment=self.segment,
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
        quota = previous[1] if previous is not None else None if self.limit is None else self.limit-len(self.rows)
        def emit(activity, index=0, **metadata):
            self.check()
            if quota is not None and len(observations) >= quota: raise ValueError('native Claude normalized event budget exceeded')
            observations.append(event(dict(base, event_id=self.scoped(self.session, writer, ident, index), activity=activity,
                source_suborder=index, **metadata)))
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
            if isinstance(content, str): content = [dict(type='text', text=content)]
            if not isinstance(content, list): raise ValueError('invalid native Claude content blocks')
            for index, block in enumerate(content):
                if not isinstance(block, dict): raise ValueError('invalid native Claude content block')
                block_type = block.get('type')
                if block_type == 'text' and kind == 'user':
                    fingerprint = message_fingerprint(block['text'], case) if isinstance(block.get('text'), str) else None
                    emit('user_message', index, **({} if fingerprint is None else dict(message_fingerprint=fingerprint)))
                elif block_type == 'text': emit('agent_message', index)
                elif block_type in ('thinking', 'redacted_thinking') and kind == 'assistant': emit('reasoning', index)
                elif block_type == 'tool_use' and kind == 'assistant':
                    name = text(block.get('name')); metadata = dict(tool_name=name if name in KNOWN_TOOLS else 'tool:'+digest(name))
                    metadata.update(skill_signal(self.project, name, block.get('input')) or {})
                    metadata.update(invocation_signal(name, block.get('input')) or {})
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
        elif kind == 'attachment' and (loading := attachment_signal(self.project, row.get('attachment'))) is not None:
            emit('context', **loading)
        else:
            self.unsupported += 1; emit('capture_coverage', missing_reason='unsupported_native_entry')
        signature = digest(dict(events=[{k: v for k, v in e.items() if k not in UNORDERED} for e in observations], usage=usage, stop=stop))
        if previous is not None:
            if previous[0] != signature: raise ValueError('conflicting native Claude UUID metadata')
            self.duplicates += 1; return
        self.seen[ident] = (signature, len(observations))
        if self.revisions is not None:
            self.raw_revisions[ident] = raw_revision
            for observation in observations:
                self.revisions[(observation['case_id'], observation['event_id'])] = raw_revision
        self.rows.extend(observations)
        # Declared placement: response facets follow the content blocks of the line that supports them.
        base['source_suborder'] = len(observations)
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
                if self.limit is not None and len(self.rows) >= self.limit: raise ValueError('native Claude response event budget exceeded')
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
                if facet == 'stop': record['source_suborder'] += 1
                self.rows.append(event(record))
        self.rows.sort(key=lambda r: r['capture_order'])  # stable, like sorted(), without a second list
        return tuple(self.rows), conflicts


def decode(folder, name, project, max_records, *, revisions=None, versions=VERIFIED, **limits):
    projection = Projection(project, max_records, folder.check, revisions, versions)
    sha256, partial, count = jsonl(folder, name, max_records, projection.add, **limits)
    rows, conflicts = projection.finish(folder.check)
    if max_records is not None and len(rows) > max_records: raise ValueError('native Claude normalized event budget exceeded')
    summary = dict(adapter_version=VERSION, upstream_commit=UPSTREAM_COMMIT, capture_completeness='partial', partial_tail=partial,
        inherited_records_skipped=0, duplicates_skipped=projection.duplicates, unsupported_records=projection.unsupported,
        input_records=count, skipped_entry_types=skipped_types(projection.skipped), recorded_versions=sorted(projection.recorded),
        session_sha256=projection.scoped('session', projection.session), conflicting_response_facets=conflicts, unattributed_usage_records=projection.unattributed,
        usage_semantics='observed_uncached_counts_only', served_model='unknown', effective_settings='unknown',
        independence='unknown', outcome='unknown', parent_session='unknown', source_read=True, network_used=False)
    return rows, summary, sha256


def read(path, project, max_records=None, **limits):
    return guarded(path, project, decode, max_records, **limits)
