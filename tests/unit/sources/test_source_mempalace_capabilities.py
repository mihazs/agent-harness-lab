"""Offline MemPalace capability report and names-only tools/list comparison; synthetic listings only."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources import mempalace_capabilities as capabilities
from agent_harness_lab.sources.mempalace import PIN

SECRET = 'SYNTHETIC_PRIVATE_TOOL_TEXT'


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with ExitStack() as denied:
        # No process, socket or write may be needed to report capabilities.
        for target in ('subprocess.Popen', 'socket.socket.connect', 'pathlib.Path.write_text', 'pathlib.Path.mkdir'):
            denied.enter_context(patch(target, side_effect=AssertionError('offline report performed an operation')))
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['sources', 'mempalace', 'capabilities', *argv])
    return code, out.getvalue(), err.getvalue()


def listing(tmp_path, mutate=lambda tools: None, name='tools.json'):
    tools = [dict(name=tool, description=SECRET, inputSchema=dict(type='object', required=list(required),
        properties={key: dict(description=SECRET) for key in properties})) for _, tool, _, _, properties, required in capabilities.PROFILES]
    mutate(tools)
    path = tmp_path/name; path.write_text(json.dumps(dict(tools=tools))); return str(path)


def test_report_covers_every_mempalace_profile_and_stays_offline():
    code, out, _ = run()
    report = json.loads(out)
    assert code == 0 and report['upstream']['commit'] == PIN
    assert (report['source_read'], report['network_used'], report['model_used']) == (False, False, False)
    assert report['live_import']['status'] == 'unsupported'
    declared = {row['capability'] for row in report['profiles']}
    modules = sorted(Path(capabilities.__file__).parent.glob('mempalace*.py'))
    shipped = {'local_file/%s/%s' % (module.PROVIDER, module.SOURCE_VERSION)
        for module in (__import__('agent_harness_lab.sources.'+path.stem, fromlist=['x']) for path in modules)
        if hasattr(module, 'PROVIDER')}
    assert declared == shipped and len(declared) == 4
    assert all(row['profile_runs_tool'] is False for row in report['profiles'])
    assert {row['tool'] for row in report['profiles'] if row['live_tool_embeds_query']} == {'mempalace_search'}


def test_pinned_tool_classes_are_disjoint_and_no_write_tool_is_supported():
    sets = (capabilities.MUTATING, capabilities.STATE_CHANGING, capabilities.READ)
    assert sum(map(len, sets)) == len(set().union(*sets)) == 44
    assert capabilities.SUPPORTED <= capabilities.READ
    tools = json.loads(run()[1])['tools']
    assert not set(tools['supported_offline']) & set(tools['refused_mutating']+tools['refused_state_changing'])
    assert 'mempalace_mine' in tools['refused_mutating'] and 'mempalace_check_duplicate' in tools['live_query_embedding']


def test_matching_listing_is_compatible_and_prints_no_listing_value(tmp_path):
    code, out, _ = run('--tools-list', listing(tmp_path))
    result = json.loads(out)
    assert code == 0 and result['source_read'] is True
    assert result['tools_list']['verdict'] == 'compatible' and {row['status'] for row in result['tools_list']['profile_tools']} == {'matches_pin'}
    assert SECRET not in out


def test_unknown_and_write_tools_are_counted_by_name_only(tmp_path):
    def extend(tools):
        tools.extend([dict(name='mempalace_mine', inputSchema={}), dict(name=SECRET, description=SECRET), dict(name='mempalace_new_tool')])
    code, out, _ = run('--tools-list', listing(tmp_path, extend))
    result = json.loads(out)['tools_list']
    assert code == 0 and result['verdict'] == 'compatible_with_unknown_tools' and result['unknown_tool_count'] == 2
    assert result['advertised_write_tools'] == ['mempalace_mine']
    assert SECRET not in out and 'mempalace_new_tool' not in out


@pytest.mark.parametrize('case', ['absent', 'renamed_property', 'extra_property', 'required_changed', 'no_schema'])
def test_a_tool_that_differs_from_the_pin_is_incompatible(tmp_path, case):
    def change(tools):
        search = next(tool for tool in tools if tool['name'] == 'mempalace_search')
        if case == 'absent': tools.remove(search)
        if case == 'renamed_property': search['inputSchema']['properties'].pop('query'); search['inputSchema']['properties']['q'] = {}
        if case == 'extra_property': search['inputSchema']['properties']['extra'] = {}
        if case == 'required_changed': search['inputSchema']['required'] = []
        if case == 'no_schema': del search['inputSchema']
    code, out, _ = run('--tools-list', listing(tmp_path, change))
    result = json.loads(out)['tools_list']
    assert code == 3 and result['verdict'] == 'incompatible'
    assert [row['status'] for row in result['profile_tools'] if row['tool'] == 'mempalace_search'] != ['matches_pin']
    assert SECRET not in out


@pytest.mark.parametrize('case', ['not_json', 'not_a_listing', 'duplicate_name', 'duplicate_key', 'symlink', 'missing', 'oversized',
    'required_object', 'required_list', 'required_number'])
def test_invalid_listing_is_refused_without_echoing_it(tmp_path, case):
    path = tmp_path/'tools.json'
    bad_required = lambda item: json.dumps(dict(tools=[dict(name='mempalace_search', inputSchema=dict(properties={}, required=[item]))]))
    path.write_text({'required_object': bad_required({SECRET: SECRET}), 'required_list': bad_required([SECRET]),
        'required_number': bad_required(1),
        'not_json': '{' + SECRET, 'not_a_listing': json.dumps([SECRET]),
        'duplicate_name': json.dumps(dict(tools=[dict(name=SECRET), dict(name=SECRET)])),
        'duplicate_key': '{"tools": [], "tools": []}'}.get(case, '{"tools": []}'))
    if case == 'symlink': link = tmp_path/'link.json'; link.symlink_to(path); path = link
    if case == 'missing': path = tmp_path/'absent.json'
    if case == 'oversized':
        with patch.dict(capabilities.mempalace.LIMITS, max_export_bytes=4): code, out, err = run('--tools-list', str(path))
    else: code, out, err = run('--tools-list', str(path))
    assert code == 2 and out == '' and SECRET not in err
    assert json.loads(err)['status'] == 'invalid_input'


def test_unrecognized_subcommand_is_a_usage_error():
    err = io.StringIO()
    with redirect_stderr(err):
        assert main(['sources', 'mempalace', 'mine']) == 2
    assert 'capabilities' in err.getvalue()
