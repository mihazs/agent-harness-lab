"""Bounded ATIF-v1.8 metadata importer.

It converts the bytes of one trajectory document into content-free AHL telemetry events and a loss report.
It performs no input or output itself and follows nothing the document names: media paths, URLs, subagent files
and continuation references are counted, never opened. Message bodies, reasoning, arguments, results, costs and
every `extra` object are dropped; tool names, call ids, models and agents survive only as digests. Task outcome
and lineage stay unknown: nothing in an ATIF document is a reviewed grade.
"""
import hashlib
import json
import math
import re
from datetime import datetime
from typing import NamedTuple

from ..evaluation.integrity import invalid_constant, unique_object
from ..sources.schema import digest
from ..telemetry.adapters import correlate
from ..telemetry.schema import event, input_fingerprint
from .mapping import (AGENT, ATIF_FIELDS, CALL, FINAL, METRICS, OBSERVATION, REF, REQUIRED, RESULT, STEP, TRAJECTORY,
                      ConversionReport, Rejected)

VERSION = 'ATIF-v1.8'
SOURCE_FORMAT = 'atif'
SOURCES = ('system', 'user', 'agent')
ACTIVITY = dict(user='user_message', agent='agent_message', system='context')
AGENT_ONLY = ('model_name', 'reasoning_effort', 'reasoning_content', 'tool_calls', 'metrics')
COUNT_MAX = 2**63 - 1


class Limits(NamedTuple):
    max_bytes: int = 32 * 1024 * 1024
    max_depth: int = 32
    max_steps: int = 10_000
    max_subagents: int = 64
    max_events: int = 500_000


def parse(raw, limits):
    """Strict JSON: no duplicate keys, no NaN or Infinity, bounded nesting."""
    try:
        document = json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except RecursionError:
        raise Rejected('nesting_limit_exceeded', 'document') from None
    except json.JSONDecodeError:
        raise Rejected('invalid_json', 'document') from None
    except ValueError as error:
        raise Rejected({'duplicate JSON keys': 'duplicate_json_key', 'nonfinite reproduction JSON number': 'nonfinite_number'}
                       .get(str(error), 'invalid_json'), 'document') from None
    pending = [(document, 1)]
    while pending:  # iterative: a hostile document must not choose the recursion depth
        value, depth = pending.pop()
        if depth > limits.max_depth:
            raise Rejected('nesting_limit_exceeded', 'document')
        if isinstance(value, dict):
            pending.extend((item, depth + 1) for item in value.values())
        elif isinstance(value, list):
            pending.extend((item, depth + 1) for item in value)
    return document


def text(value, where):
    if not isinstance(value, str) or not value:
        raise Rejected('invalid_structure', where)
    return value


def array(value, where):
    if not isinstance(value, list):
        raise Rejected('invalid_structure', where)
    return value


def count(value, where):
    if type(value) is not int or not 0 <= value <= COUNT_MAX:
        raise Rejected('invalid_measurement', where)
    return value


def real(value, where):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise Rejected('invalid_measurement', where)
    return value


def stamp(value, where):
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
            raise ValueError
    except ValueError:
        raise Rejected('invalid_timestamp', where) from None
    return value


def fields(value, prefix, report):
    """One object of a declared kind: required fields present, every field counted under its disposition."""
    if not isinstance(value, dict):
        raise Rejected('invalid_structure', prefix)
    optional = set(ATIF_FIELDS[prefix]) - set(REQUIRED[prefix])
    value = {key: item for key, item in value.items() if item is not None or key not in optional}
    for key, item in value.items():
        report.seen(prefix, key)
        if key == 'extra' and not isinstance(item, dict):
            raise Rejected('invalid_structure', prefix + '.extra')
    for key in REQUIRED[prefix]:
        if key not in value:
            raise Rejected('missing_field', prefix + '.' + key)
    return value


def parts(value, where, media):
    """A message or result body is text or typed parts; only the part kinds are read, never a source location."""
    if isinstance(value, str):
        return
    for part in array(value, where):
        kind = part.get('type') if isinstance(part, dict) else None
        if kind == 'text' and set(part) == {'type', 'text'} and isinstance(part['text'], str):
            continue
        source = part.get('source') if kind in ('image', 'audio') else None
        if not (isinstance(source, dict) and set(part) == {'type', 'source'} and isinstance(source.get('path'), str)
                and isinstance(source.get('media_type'), str)):
            raise Rejected('unsupported_content_part', where)
        media[kind] += 1


class Importer:
    def __init__(self, raw, limits):
        self.limits = limits
        self.report = ConversionReport(VERSION, hashlib.sha256(raw).hexdigest())
        self.walks = []    # one per trajectory, root first
        self.edges = {}    # trajectory key -> keys of the embedded trajectories its steps reference
        self.known = {}    # embedded trajectory_id -> key
        self.total = 0     # events emitted so far, across every trajectory

    def run(self, document):
        report = self.report
        children = document.get('subagent_trajectories')
        embedded = array([] if children is None else children, TRAJECTORY + '.subagent_trajectories')
        if len(embedded) > self.limits.max_subagents:
            raise Rejected('subagent_limit_exceeded', 'subagent_trajectories')
        identities = {document['trajectory_id']} if isinstance(document.get('trajectory_id'), str) else set()
        for child in embedded:
            ident = child.get('trajectory_id') if isinstance(child, dict) else None
            if not isinstance(ident, str) or not ident:
                raise Rejected('missing_field', TRAJECTORY + '.trajectory_id')
            if ident in identities:
                raise Rejected('duplicate_trajectory_id', TRAJECTORY + '.trajectory_id')
            identities.add(ident)
            self.known[ident] = 'embedded:' + ident
        self.trajectory(document, 'root', False)
        for child in embedded:
            self.trajectory(child, self.known[child['trajectory_id']], True)
        self.link()
        rows = [row for walk in self.walks for row in walk['events']]
        events, bases = [], {walk['case']: walk['base'] for walk in self.walks}
        for row in correlate(rows):
            events.append(event({**bases[row['case_id']], **row}) if row['case_id'] in bases and 'source_format' not in row else row)
        report.counts.update(trajectories=len(self.walks), steps=sum(walk['steps'] for walk in self.walks), events=len(events))
        report.counts.update('status:' + row['capture_status'] for row in events if row['capture_status'] != 'unverified')
        return events

    def trajectory(self, doc, key, embedded):
        report, limits = self.report, self.limits
        doc = fields(doc, TRAJECTORY, report)
        if doc['schema_version'] != VERSION:
            raise Rejected('unsupported_schema_version', 'schema_version')
        if embedded and 'subagent_trajectories' in doc:
            raise Rejected('unsupported_nesting', TRAJECTORY + '.subagent_trajectories')
        for name in ('session_id', 'trajectory_id', 'notes'):
            if name in doc:
                text(doc[name], TRAJECTORY + '.' + name)
        if 'continued_trajectory_ref' in doc:
            report.unsupported_features['continuation_reference_not_followed'] += 1
        agent = fields(doc['agent'], AGENT, report)
        for name in ('name', 'version', 'model_name'):
            if name in agent:
                text(agent[name], AGENT + '.' + name)
        if 'tool_definitions' in agent:
            array(agent['tool_definitions'], AGENT + '.tool_definitions')
        if 'final_metrics' in doc:
            for name, value in fields(doc['final_metrics'], FINAL, report).items():
                if name != 'extra':
                    (real if name == 'total_cost_usd' else count)(value, FINAL + '.' + name)
        steps = array(doc['steps'], TRAJECTORY + '.steps')
        if not steps:
            raise Rejected('invalid_structure', TRAJECTORY + '.steps')
        if len(steps) > limits.max_steps:
            raise Rejected('step_limit_exceeded', 'steps')
        case = digest(('atif-case-v1', report.source_digest, key))
        base = dict(case_id=case, root_case_id=digest(('atif-case-v1', report.source_digest, 'root')), lineage_verified=False,
            capture_complete=False, source_format=SOURCE_FORMAT, source_version=VERSION,
            runtime_fingerprint=digest(('atif-agent', agent['name'], agent['version'])))
        if 'model_name' in agent:
            base['requested_model_fingerprint'] = digest(agent['model_name'])
        else:
            report.missing_fields[AGENT + '.model_name'] += 1
        walk = dict(key=key, case=case, base=base, events=[], steps=0)
        self.walks.append(walk)
        self.edges[key] = set()
        for index, step in enumerate(steps):
            self.step(walk, step, index)

    def add(self, walk, step_id, kind, number, activity, **meta):
        self.total += 1
        if self.total > self.limits.max_events:
            raise Rejected('event_limit_exceeded', 'events')
        walk['events'].append(dict(walk['base'], event_id=f'{step_id}:{kind}:{number}', activity=activity,
                                   capture_order=len(walk['events']), **meta))

    def step(self, walk, step, index):
        report = self.report
        step = fields(step, STEP, report)
        step_id, source = step['step_id'], step['source']
        if type(step_id) is not int:
            raise Rejected('invalid_structure', STEP + '.step_id')
        if step_id != index + 1:
            raise Rejected('non_sequential_step_ids', STEP + '.step_id')
        if source not in SOURCES:
            raise Rejected('invalid_structure', STEP + '.source')
        for name in AGENT_ONLY:
            if name in step and source != 'agent':
                raise Rejected('invalid_structure', STEP + '.' + name)
        media = dict(image=0, audio=0)
        parts(step['message'], STEP + '.message', media)
        common = dict(actor=source)
        if 'timestamp' in step:
            common['timestamp'] = stamp(step['timestamp'], STEP + '.timestamp')
        else:
            report.missing_fields[STEP + '.timestamp'] += 1
        if 'model_name' in step:
            common['reported_model_fingerprint'] = digest(text(step['model_name'], STEP + '.model_name'))
        if 'llm_call_count' in step:
            count(step['llm_call_count'], STEP + '.llm_call_count')
        if step.get('is_copied_context') is not None and type(step['is_copied_context']) is not bool:
            raise Rejected('invalid_structure', STEP + '.is_copied_context')
        calls = [self.call(item) for item in array(step.get('tool_calls', []), STEP + '.tool_calls')]
        results = [self.result(item, media) for item in array(
            fields(step['observation'], OBSERVATION, report)['results'], OBSERVATION + '.results')] if 'observation' in step else []
        usage = self.usage(step['metrics'], step.get('llm_call_count')) if 'metrics' in step else None
        walk['steps'] += 1
        if step.get('is_copied_context'):  # a step copied from an earlier trajectory is not a new interaction
            report.unsupported_features['copied_context_steps_not_imported'] += 1
            return
        self.add(walk, step_id, 'message', 0, ACTIVITY[source], **common)
        if step.get('reasoning_content'):
            self.add(walk, step_id, 'reasoning', 0, 'reasoning', **common)
        for number, (call_id, name, arguments) in enumerate(calls):
            meta = dict(common, actor='agent', call_id=digest(('atif-call-v1', walk['case'], call_id)), tool_name='tool:' + digest(name))
            fingerprint = input_fingerprint(arguments, walk['case'])
            self.add(walk, step_id, 'call', number, 'tool_call', **meta, **({} if fingerprint is None else dict(input_fingerprint=fingerprint)))
        for number, (call_id, refs) in enumerate(results):
            meta = dict(common, actor='tool', execution_status='unknown')
            if call_id is None:
                meta['missing_reason'] = 'source_call_id_absent'
                report.missing_fields[RESULT + '.source_call_id'] += 1
            else:
                meta['call_id'] = digest(('atif-call-v1', walk['case'], call_id))
            self.add(walk, step_id, 'result', number, 'tool_result', **meta)
            for position, ref in enumerate(refs):
                self.reference(walk, step_id, f'{number}.{position}', ref, common)
        if usage is not None:
            self.add(walk, step_id, 'usage', 0, 'response_usage', **dict(common, **usage))
        for kind, total in media.items():
            if total:
                report.unsupported_features[kind + '_content_parts_not_loaded'] += total
                self.add(walk, step_id, 'coverage', kind, 'capture_coverage', **common, missing_reason='unsupported_media_content')

    def call(self, item):
        item = fields(item, CALL, self.report)
        if not isinstance(item['arguments'], dict):
            raise Rejected('invalid_structure', CALL + '.arguments')
        return text(item['tool_call_id'], CALL + '.tool_call_id'), text(item['function_name'], CALL + '.function_name'), item['arguments']

    def result(self, item, media):
        item = fields(item, RESULT, self.report)
        call_id = item.get('source_call_id')
        if call_id is not None:
            text(call_id, RESULT + '.source_call_id')
        if item.get('content') is not None:
            parts(item['content'], RESULT + '.content', media)
        refs = []
        for ref in array(item.get('subagent_trajectory_ref', []), RESULT + '.subagent_trajectory_ref'):
            ref = fields(ref, REF, self.report)
            ident, path = ref.get('trajectory_id'), ref.get('trajectory_path')
            for name, value in (('trajectory_id', ident), ('trajectory_path', path)):
                if value is not None:
                    text(value, REF + '.' + name)
            if ident is None and path is None:
                raise Rejected('invalid_subagent_reference', REF)
            refs.append((ident, path))
        return call_id, refs

    def usage(self, metrics, calls):
        report = self.report
        metrics = fields(metrics, METRICS, report)
        values = {name: count(metrics[name], METRICS + '.' + name) for name in ('prompt_tokens', 'completion_tokens', 'cached_tokens') if name in metrics}
        if 'cost_usd' in metrics:
            real(metrics['cost_usd'], METRICS + '.cost_usd')
        for name in ('prompt_token_ids', 'completion_token_ids', 'logprobs'):
            if name in metrics:
                array(metrics[name], METRICS + '.' + name)
        if values.get('cached_tokens', 0) > values.get('prompt_tokens', COUNT_MAX):
            raise Rejected('invalid_measurement', METRICS + '.cached_tokens')
        if calls is not None and calls > 1:
            report.warnings['aggregated_llm_calls_in_one_step'] += 1
        # AHL input_tokens are uncached counts, so a prompt total without its cached share is not one.
        usage = {}
        if 'completion_tokens' in values:
            usage['output_tokens'] = values['completion_tokens']
        if 'prompt_tokens' in values and 'cached_tokens' in values:
            usage['input_tokens'] = values['prompt_tokens'] - values['cached_tokens']
        elif 'prompt_tokens' in values:
            report.missing_fields[METRICS + '.cached_tokens'] += 1
            usage['missing_reason'] = 'cached_token_split_unavailable'
        return usage or None

    def reference(self, walk, step_id, number, ref, common):
        ident, path = ref
        child = self.known.get(ident)
        if child is not None:
            self.edges[walk['key']].add(child)
            return
        reason = 'subagent_trajectory_not_followed' if path is not None else 'subagent_trajectory_unresolved'
        self.report.unsupported_features['subagent_trajectory_path_references' if path is not None else 'unresolved_subagent_references'] += 1
        self.add(walk, step_id, 'coverage', 'ref' + number, 'capture_coverage', **common, missing_reason=reason)

    def link(self):
        """Reject a reference cycle; give an embedded trajectory a parent only when exactly one trajectory references it."""
        state = {}
        for start in self.edges:
            stack = [(start, iter(sorted(self.edges[start])))] if start not in state else []
            state.setdefault(start, 'open')
            while stack:
                node, children = stack[-1]
                child = next(children, None)
                if child is None:
                    state[node] = 'done'
                    stack.pop()
                elif state.get(child) == 'open':
                    raise Rejected('subagent_reference_cycle', 'subagent_trajectory_ref')
                elif child not in state:
                    state[child] = 'open'
                    stack.append((child, iter(sorted(self.edges[child]))))
        cases = {walk['key']: walk['case'] for walk in self.walks}
        parents = {}
        for parent, children in self.edges.items():
            for child in children:
                parents.setdefault(child, set()).add(parent)
        for walk in self.walks[1:]:
            owners = parents.get(walk['key'], ())
            if len(owners) != 1:
                self.report.warnings['subagent_parent_unknown' if not owners else 'subagent_parent_ambiguous'] += 1
                continue
            for row in walk['events']:
                row['native_parent_thread_fingerprint'] = cases[next(iter(owners))]
            walk['base']['native_parent_thread_fingerprint'] = cases[next(iter(owners))]


def version_label(value):
    """A schema version safe to echo: a recognizable ATIF label, never arbitrary input text."""
    return value if isinstance(value, str) and re.fullmatch(r'ATIF-v[0-9]{1,3}\.[0-9]{1,3}', value) else 'unrecognized'


def convert(raw, limits=Limits()):
    """Bytes of one ATIF-v1.8 document -> (normalized events, ConversionReport); raises Rejected and returns nothing partial."""
    document = parse(raw, limits)
    detected = document.get('schema_version') if isinstance(document, dict) else None
    if detected != VERSION:
        raise Rejected('unsupported_schema_version', 'schema_version', 'detected ' + version_label(detected) + '; supported ' + VERSION)
    importer = Importer(raw, limits)
    events = importer.run(document)
    return events, importer.report
