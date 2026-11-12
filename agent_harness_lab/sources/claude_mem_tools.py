"""Offline capability check of a saved Claude-Mem MCP tools/list result against the pinned v15.9.0 read tools.

Reads one caller-supplied JSON file. It never starts or contacts a worker, calls a tool or opens the network;
tool descriptions and unrecognized names are never echoed. A match is not authority to read memory.
"""
import json
import sys
from agent_harness_lab.evaluation.integrity import read_regular
from .claude_mem import PINNED
from .hardened import load_json
from .schema import digest

PROFILE = 'claude-mem-mcp-tools-v1'
LIMITS = dict(max_bytes=1024*1024, max_json_depth=16, max_json_containers=20000, max_json_values=100000)
# Descriptions are prose and are removed before comparing; src/servers/mcp-server.ts at the pin defines the rest.
_IDS = {'type': 'array', 'items': {'type': ['number', 'string']}}
_STR, _NUM = {'type': 'string'}, {'type': 'number'}
SHAPES = {
    'search': dict(properties=dict(query=_STR, limit=_NUM, project=_STR, projectId=_STR, platformSource=_STR, type=_STR,
        obs_type=_STR, dateStart=_STR, dateEnd=_STR, offset=_NUM, orderBy=_STR)),
    'timeline': dict(properties=dict(anchor={'type': ['number', 'string']}, query=_STR, depth_before=_NUM, depth_after=_NUM,
        project=_STR, projectId=_STR)),
    'get_observations': dict(properties=dict(ids=_IDS, projectId=_STR), required=['ids']),
    'get_tool_uses': dict(properties=dict(ids=_IDS, limit=_NUM, project=_STR, contentSessionId=_STR), required=['ids'])}
PINNED_SCHEMAS = {name: dict(type='object', additionalProperties=True, **shape) for name, shape in SHAPES.items()}
REQUIRED = ('search', 'timeline', 'get_observations')  # the three-step workflow; get_tool_uses is an optional follow-up
# Every tool the pinned server can advertise, so a name outside this set is counted but never printed.
RECOGNIZED = frozenset(('dumb_search', 'save_memory', 'important_workflow', 'search', 'timeline', 'get_observations',
    'get_tool_uses', 'work_state_write', 'work_state_read', 'team_brief', 'team_search', 'session_start_context',
    'observation_add', 'observation_record_event', 'observation_search', 'observation_context', 'observation_generation_status',
    'smart_search', 'smart_unfold', 'smart_outline', 'build_corpus', 'list_corpora', 'prime_corpus', 'query_corpus',
    'rebuild_corpus', 'reprime_corpus', 'memory_add', 'memory_search', 'memory_context'))
SERVER_ONLY = frozenset(('observation_add', 'observation_record_event', 'observation_search', 'observation_context',
    'observation_generation_status', 'memory_add', 'memory_search', 'memory_context'))


def structure(value):
    """The schema without its description prose; a property that is itself named description is kept."""
    if isinstance(value, dict):
        return {k: structure(v) for k, v in value.items() if not (k == 'description' and isinstance(v, str))}
    return [structure(v) for v in value] if isinstance(value, list) else value


def read_only(tool):
    annotations = tool.get('annotations')
    return isinstance(annotations, dict) and annotations.get('readOnlyHint') is True


def result(response):
    """The result object of a JSON-RPC response, or the object itself when it is already a result."""
    return response.get('result') if isinstance(response, dict) and 'jsonrpc' in response else response


def check_tools(document, release):
    """Compare saved MCP results with the pin and return the report; ValueError for a document that is not one.

    document is a tools/list result or JSON-RPC response, or {"tools_list": ..., "initialize": ...} to add the
    server's declared name and version. Only one listing page is read; a nextCursor leaves the listing incomplete.
    """
    if release not in PINNED: raise ValueError('unsupported Claude-Mem release')
    wrapped = isinstance(document, dict) and 'tools_list' in document
    listing, init = result(document['tools_list'] if wrapped else document), result(document.get('initialize')) if wrapped else None
    if not isinstance(listing, dict) or not isinstance(listing.get('tools'), list):
        raise ValueError('expected a tools/list result with a tools list')
    info = init.get('serverInfo') if isinstance(init, dict) else None
    if init is not None and (not isinstance(info, dict) or not isinstance(info.get('version'), str)): raise ValueError('invalid initialize result')
    found = {}
    for tool in listing['tools']:
        if not isinstance(tool, dict) or not isinstance(tool.get('name'), str): raise ValueError('each tool needs a string name')
        found.setdefault(tool['name'], []).append(tool)
    version_ok = info is None or (info.get('name') == 'claude-mem' and info['version'] == release)
    warnings = {'tool_descriptions_not_read', 'read_only_hint_is_a_server_claim'}
    if info is None: warnings.add('server_version_unstated')
    tools = {}
    for name, pinned in PINNED_SCHEMAS.items():
        entries = found.get(name, [])
        schema = structure(entries[0].get('inputSchema')) if len(entries) == 1 else None
        status = ('absent' if not entries else 'duplicate' if len(entries) > 1
                  else 'matches_pin' if digest(schema) == digest(pinned) else 'differs')
        row = tools[name] = dict(status=status, read_only_hint=read_only(entries[0]) if len(entries) == 1 else None)
        if status == 'differs':
            properties = schema.get('properties') if isinstance(schema, dict) and isinstance(schema.get('properties'), dict) else {}
            row.update(missing_properties=sorted(set(pinned['properties']) - set(properties)),
                       added_property_count=len(set(properties) - set(pinned['properties'])))
    complete = listing.get('nextCursor') is None
    if not complete: warnings.add('listing_incomplete')
    if SERVER_ONLY & found.keys(): warnings.add('server_runtime_advertised')
    if 'get_tool_uses' not in found: warnings.add('get_tool_uses_absent')
    broken = any(tools[n]['status'] in ('differs', 'duplicate') or tools[n]['read_only_hint'] is False for n in REQUIRED)
    absent = any(tools[n]['status'] == 'absent' for n in REQUIRED)
    verdict = 'incompatible' if broken or not version_ok or (absent and complete) else 'inconclusive' if absent else 'compatible'
    return dict(schema_version=1, profile=PROFILE, claude_mem_version=release, upstream_commit=PINNED[release], verdict=verdict,
        version_attestation='initialize_declared' if info is not None else 'caller_declared', version_matches=version_ok,
        listing_complete=complete, tools=tools, tools_advertised=len(listing['tools']),
        unrecognized_tools=sum(name not in RECOGNIZED for name in found),
        recognized_without_read_only_hint=sorted(n for n in found if n in RECOGNIZED and not all(map(read_only, found[n]))),
        warnings=sorted(warnings), network_used=False, worker_contacted=False, tools_called=False, descriptions_stored=False,
        memory_read_authorized=False)


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='ahl sources claude-mem tool-check',
        description='Compare one saved Claude-Mem MCP tools/list result with the pinned v15.9.0 read tools (search, timeline, '
        'get_observations, get_tool_uses) by input-schema structure. Offline: no worker is started or contacted, no tool is called, '
        'and descriptions and unrecognized tool names are not printed. Exit 0: compatible; 3: incompatible or inconclusive; 2: invalid input.')
    parser.add_argument('--tools', required=True, help='One existing regular JSON file holding a tools/list result or JSON-RPC response, or {"tools_list": ..., "initialize": ...} to add the declared server version; a symlink is refused')
    parser.add_argument('--release', default='15.9.0', choices=sorted(PINNED), help='Claude-Mem release the listing is declared to come from (default: %(default)s)')
    args = parser.parse_args(argv)
    try:
        report = check_tools(load_json(read_regular(args.tools, LIMITS['max_bytes']), LIMITS, 'Claude-Mem tools listing'), args.release)
    except (OSError, ValueError) as error:
        print(json.dumps(dict(profile=PROFILE, status='invalid_input', reason=type(error).__name__)), file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report['verdict'] == 'compatible' else 3
