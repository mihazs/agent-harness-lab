"""Static MemPalace capability report and a names-only check of a caller-supplied `tools/list` result.

Nothing here starts, contacts or reads a MemPalace worker, backend or palace, and no model runs.
"""
import argparse
import json
import sys
from agent_harness_lab.evaluation.integrity import read_regular
from . import mempalace, mempalace_kg, mempalace_list, mempalace_search
from .hardened import load_json

PROFILE = 'ahl-mempalace-capabilities-v1'
UPSTREAM_VERSION = 'v3.7.0'  # the release named by mempalace.PIN
# Each profile: module, pinned tool, claim type, scope, then the tool's pinned input_schema property and required names.
PROFILES = (
    (mempalace, 'mempalace_get_drawer', 'drawer', 'wing', ('drawer_id',), ('drawer_id',)),
    (mempalace_list, 'mempalace_list_drawers', 'list_item', 'wing', ('before', 'limit', 'offset', 'room', 'since', 'wing'), ()),
    (mempalace_search, 'mempalace_search', 'search_hit', 'wing',
        ('before', 'context', 'limit', 'max_distance', 'query', 'room', 'since', 'source_file', 'wing'), ('query',)),
    (mempalace_kg, 'mempalace_kg_query', 'kg_fact', 'entity', ('as_of', 'direction', 'entity'), ('entity',)),
)
# Pinned `mcp_server.py` TOOLS: the `_MUTATING_TOOLS` set, the other names its read-only mode refuses or comments call
# not write-free, and every remaining tool. `read` means only that upstream does not refuse it in read-only mode.
MUTATING = frozenset('''mempalace_kg_add mempalace_kg_invalidate mempalace_kg_supersede mempalace_create_tunnel
    mempalace_delete_tunnel mempalace_delete_hallway mempalace_add_drawer mempalace_delete_drawer mempalace_checkpoint
    mempalace_delete_by_source mempalace_mine mempalace_sync mempalace_update_drawer mempalace_diary_write
    mempalace_event_append mempalace_event_ack mempalace_artifact_put mempalace_patch_submit'''.split())
STATE_CHANGING = frozenset(('mempalace_hook_settings', 'mempalace_memories_filed_away', 'mempalace_reconnect'))
READ = frozenset('''mempalace_status mempalace_list_wings mempalace_list_rooms mempalace_get_taxonomy mempalace_get_aaak_spec
    mempalace_kg_query mempalace_kg_timeline mempalace_kg_stats mempalace_traverse mempalace_find_tunnels
    mempalace_graph_stats mempalace_mesh_peers mempalace_list_tunnels mempalace_list_hallways mempalace_follow_tunnels
    mempalace_search mempalace_check_duplicate mempalace_get_drawer mempalace_list_drawers mempalace_diary_read
    mempalace_event_list mempalace_event_wait mempalace_artifact_get'''.split())
# Handlers whose live call embeds the query: `tool_check_duplicate` and, through `searcher.search_memories`, `tool_search`.
EMBEDS = ('mempalace_check_duplicate', 'mempalace_search')
SUPPORTED = {tool for _, tool, *_ in PROFILES}
WARNINGS = ['caller_supplied_listing_unverified', 'server_identity_unverified', 'result_shape_not_advertised']


def report():
    return dict(schema_version=1, profile=PROFILE, upstream=dict(version=UPSTREAM_VERSION, commit=mempalace.PIN),
        source_read=False, network_used=False, model_used=False,
        live_import=dict(status='unsupported', reason='pinned source exposes no snapshot token for paged reads, opening a palace can '
            'create files and a server holds a writer lease; AHL never starts, contacts or opens one and never reads backend files'),
        profiles=[dict(capability='local_file/%s/%s' % (module.PROVIDER, module.SOURCE_VERSION), tool=tool, claim_type=claim,
            scope=scope, profile_runs_tool=False, live_tool_embeds_query=tool in EMBEDS) for module, tool, claim, scope, *_ in PROFILES],
        tools=dict(supported_offline=sorted(SUPPORTED), unsupported_read=sorted(READ-SUPPORTED),
            refused_state_changing=sorted(STATE_CHANGING), refused_mutating=sorted(MUTATING), live_query_embedding=list(EMBEDS)),
        warnings=['pinned_classification_not_verified_against_a_server', 'complete_palace_coverage_unsupported',
            'profile_results_are_caller_supplied_unverified', 'real_server_evidence_absent'])


def compare(listing):
    """Compare names only; no value from the listing reaches the result."""
    tools = listing.get('tools') if isinstance(listing, dict) else None
    if not isinstance(tools, list) or not all(isinstance(tool, dict) and isinstance(tool.get('name'), str) for tool in tools):
        raise ValueError('not an MCP tools/list result')
    advertised = {tool['name']: tool for tool in tools}
    if len(advertised) != len(tools): raise ValueError('duplicate tool name')
    rows = []
    for _, name, _, _, properties, required in PROFILES:
        if name not in advertised: rows.append(dict(tool=name, status='absent')); continue
        schema = advertised[name].get('inputSchema')
        found = schema.get('properties') if isinstance(schema, dict) else None
        needed = schema.get('required', []) if isinstance(schema, dict) else None
        if isinstance(needed, list) and not all(isinstance(item, str) for item in needed): raise ValueError('non-string required name')
        if not isinstance(found, dict) or not isinstance(needed, list):
            rows.append(dict(tool=name, status='schema_differs', missing_properties=list(properties))); continue
        missing, unexpected = sorted(set(properties)-set(found)), set(found)-set(properties)
        same = not missing and not unexpected and set(needed) == set(required)
        rows.append(dict(tool=name, status='matches_pin' if same else 'schema_differs', missing_properties=missing,
            unexpected_property_count=len(unexpected), required_matches_pin=set(needed) == set(required)))
    known = SUPPORTED | READ | MUTATING | STATE_CHANGING
    unknown = sum(name not in known for name in advertised)
    complete = listing.get('nextCursor') is None  # only one page is read; a cursor means another page may hold more tools
    differs = any(row['status'] == 'schema_differs' for row in rows)
    absent = any(row['status'] == 'absent' for row in rows)
    verdict = ('incompatible' if differs or (absent and complete) else 'inconclusive' if absent
        else 'compatible_with_unknown_tools' if unknown else 'compatible')
    return dict(verdict=verdict, listing_complete=complete, profile_tools=rows, unknown_tool_count=unknown,
        advertised_write_tools=sorted(name for name in advertised if name in MUTATING | STATE_CHANGING),
        warnings=WARNINGS if complete else sorted(WARNINGS+['listing_incomplete']))


def main(argv):
    parser = argparse.ArgumentParser(prog='ahl sources mempalace capabilities',
        description='Print the offline MemPalace capability report: which pinned read tools have an import profile, which are '
        'unsupported, and which write tools AHL refuses. Optionally compare one caller-supplied MCP tools/list JSON file with '
        'the pinned input schemas by name only (verdict compatible, compatible_with_unknown_tools, inconclusive or incompatible). Read-only: '
        'it never starts or contacts MemPalace, opens a palace, calls a tool, uses the network or runs a model. '
        'Only one page is read: a nextCursor adds a listing_incomplete warning and makes an absent tool inconclusive. '
        'Exit 0: report printed and any listing compatible; 3: listing incompatible or inconclusive; 2: invalid input.')
    parser.add_argument('--tools-list', metavar='FILE', help='One existing regular JSON file holding a saved MCP tools/list result; '
        'a symlink is refused and no tool description or unknown tool name is printed')
    args = parser.parse_args(argv)
    result = report()
    try:
        if args.tools_list is not None:
            result['tools_list'] = compare(load_json(read_regular(args.tools_list, mempalace.LIMITS['max_export_bytes']),
                mempalace.LIMITS, 'MemPalace tools list'))
            result['source_read'] = True
    except (OSError, ValueError) as error:
        print(json.dumps(dict(profile=PROFILE, status='invalid_input', reason=type(error).__name__)), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 3 if result.get('tools_list', {}).get('verdict') in ('incompatible', 'inconclusive') else 0
