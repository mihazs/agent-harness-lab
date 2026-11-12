"""ahl sources claude-mem tool-check: a saved tools/list result compared offline with the pinned v15.9.0 read tools."""
from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from unittest.mock import patch

import pytest

from agent_harness_lab.cli import main
from agent_harness_lab.sources.claude_mem_tools import PINNED_SCHEMAS, RECOGNIZED, REQUIRED, check_tools

SECRET = 'SYNTHETIC_SECRET_TEXT'
HINT = {'readOnlyHint': True}


def tool(name, **fields):
    return dict(name=name, description='invented prose '+SECRET, inputSchema=copy.deepcopy(PINNED_SCHEMAS[name]), annotations=HINT, **fields)


def listing(*names, **extra):
    return dict(tools=[tool(name) for name in names], **extra)


FULL = listing(*PINNED_SCHEMAS)


def run(tmp_path, document, *argv, raw=None):
    path = tmp_path/'tools.json'
    path.write_text(raw if raw is not None else json.dumps(document))
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(['sources', 'claude-mem', 'tool-check', '--tools', str(path), *argv])
    return code, out.getvalue(), err.getvalue()


def test_the_pinned_read_tools_are_compatible_and_prose_and_unknown_names_are_not_printed(tmp_path):
    document = copy.deepcopy(FULL)
    document['tools'] += [dict(name='search_'+SECRET, annotations=HINT), dict(name='save_memory', description=SECRET, inputSchema={'type': 'object'})]
    document['tools'][0]['inputSchema']['properties']['query']['description'] = SECRET
    code, out, err = run(tmp_path, document)
    report = json.loads(out)
    assert code == 0 and err == '' and report['verdict'] == 'compatible' and SECRET not in out
    assert {name: row['status'] for name, row in report['tools'].items()} == dict.fromkeys(PINNED_SCHEMAS, 'matches_pin')
    assert report['tools_advertised'] == 6 and report['unrecognized_tools'] == 1
    assert report['recognized_without_read_only_hint'] == ['save_memory']
    assert report['version_attestation'] == 'caller_declared' and 'server_version_unstated' in report['warnings']
    assert (report['network_used'], report['worker_contacted'], report['tools_called'], report['memory_read_authorized']) == (False,)*4


def test_an_older_tool_set_without_the_optional_raw_follow_up_still_has_the_three_step_workflow(tmp_path):
    code, out, _ = run(tmp_path, listing(*REQUIRED))
    report = json.loads(out)
    assert code == 0 and report['verdict'] == 'compatible' and report['tools']['get_tool_uses']['status'] == 'absent'
    assert 'get_tool_uses_absent' in report['warnings']


def test_a_complete_listing_missing_a_workflow_tool_is_incompatible_but_a_partial_page_is_inconclusive(tmp_path):
    code, out, _ = run(tmp_path, listing('search', 'get_observations'))
    assert code == 3 and json.loads(out)['verdict'] == 'incompatible' and json.loads(out)['tools']['timeline']['status'] == 'absent'
    code, out, _ = run(tmp_path, listing('search', 'get_observations', nextCursor='page-2'))
    report = json.loads(out)
    assert code == 3 and report['verdict'] == 'inconclusive' and report['listing_complete'] is False and 'listing_incomplete' in report['warnings']
    assert 'page-2' not in out


def test_a_changed_input_schema_names_missing_properties_and_counts_added_ones_without_printing_them(tmp_path):
    document = copy.deepcopy(FULL)
    schema = document['tools'][0]['inputSchema']
    del schema['properties']['platformSource']
    schema['properties']['invented_'+SECRET] = {'type': 'string'}
    code, out, _ = run(tmp_path, document)
    row = json.loads(out)['tools']['search']
    assert code == 3 and json.loads(out)['verdict'] == 'incompatible' and SECRET not in out
    assert (row['status'], row['missing_properties'], row['added_property_count']) == ('differs', ['platformSource'], 1)


def test_a_property_named_description_is_structure_not_prose():
    document = copy.deepcopy(FULL)
    document['tools'][0]['inputSchema']['properties']['description'] = {'type': 'string'}
    assert check_tools(document, '15.9.0')['tools']['search']['status'] == 'differs'


def test_a_workflow_tool_without_the_read_only_hint_is_incompatible_and_an_optional_one_is_only_reported(tmp_path):
    document = copy.deepcopy(FULL)
    del document['tools'][1]['annotations']
    code, out, _ = run(tmp_path, document)
    report = json.loads(out)
    assert code == 3 and report['verdict'] == 'incompatible' and report['tools']['timeline']['read_only_hint'] is False
    assert report['recognized_without_read_only_hint'] == ['timeline']
    document = copy.deepcopy(FULL)
    document['tools'][3]['annotations'] = 'readOnlyHint'
    code, out, _ = run(tmp_path, document)
    assert code == 0 and json.loads(out)['recognized_without_read_only_hint'] == ['get_tool_uses']


def test_a_duplicate_workflow_tool_is_incompatible(tmp_path):
    code, out, _ = run(tmp_path, dict(tools=[*FULL['tools'], tool('search')]))
    assert code == 3 and json.loads(out)['tools']['search']['status'] == 'duplicate'


@pytest.mark.parametrize('info, ok', ((dict(name='claude-mem', version='15.9.0'), True), (dict(name='claude-mem', version='15.8.0'), False),
                                      (dict(name='other', version='15.9.0'), False)))
def test_a_declared_server_version_must_equal_the_release(tmp_path, info, ok):
    response = dict(jsonrpc='2.0', id=1, result=dict(serverInfo=info, capabilities={}))
    code, out, _ = run(tmp_path, dict(tools_list=dict(jsonrpc='2.0', id=2, result=FULL), initialize=response))
    report = json.loads(out)
    assert (code, report['version_matches']) == ((0 if ok else 3), ok) and report['version_attestation'] == 'initialize_declared'
    assert report['verdict'] == ('compatible' if ok else 'incompatible')


def test_a_server_runtime_listing_is_flagged_because_ids_and_raw_follow_ups_differ(tmp_path):
    code, out, _ = run(tmp_path, dict(tools=[*FULL['tools'], dict(name='observation_add', inputSchema={'type': 'object'})]))
    assert code == 0 and 'server_runtime_advertised' in json.loads(out)['warnings'] and json.loads(out)['recognized_without_read_only_hint'] == ['observation_add']


@pytest.mark.parametrize('raw', ('not json', '{"tools": {}}', '[]', '{"tools": [1]}', '{"tools": [{"name": 3}]}', '{"tools": [], "tools": []}',
    '{"tools_list": {"tools": []}, "initialize": {"serverInfo": 3}}', '{"jsonrpc": "2.0", "error": {"message": "x"}}', '﻿{"tools": []}'))
def test_a_document_that_is_not_a_listing_is_invalid_input_with_no_content_in_the_error(tmp_path, raw):
    code, out, err = run(tmp_path, None, raw=raw)
    assert (code, out) == (2, '') and json.loads(err)['status'] == 'invalid_input' and set(json.loads(err)) == {'profile', 'status', 'reason'}


def test_oversized_nested_symlinked_and_missing_inputs_are_refused(tmp_path):
    assert run(tmp_path, None, raw=' '*(1024*1024+1))[0] == 2
    assert run(tmp_path, None, raw='{"tools": '+'['*40+']'*40+'}')[0] == 2
    target = tmp_path/'real.json'; target.write_text(json.dumps(FULL))
    (tmp_path/'link.json').symlink_to(target)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        assert main(['sources', 'claude-mem', 'tool-check', '--tools', str(tmp_path/'link.json')]) == 2
        assert main(['sources', 'claude-mem', 'tool-check', '--tools', str(tmp_path/'missing.json')]) == 2
    assert out.getvalue() == ''


def test_an_unpinned_release_and_a_missing_subcommand_are_refused(tmp_path):
    with redirect_stderr(io.StringIO()), pytest.raises(SystemExit) as stopped:
        run(tmp_path, FULL, '--release', '15.8.0')
    assert stopped.value.code == 2
    with pytest.raises(ValueError, match='unsupported Claude-Mem release'):
        check_tools(FULL, '15.8.0')
    err = io.StringIO()
    with redirect_stderr(err):
        assert main(['sources', 'claude-mem']) == 2
    assert 'tool-check' in err.getvalue()


def test_checking_a_listing_starts_no_process_and_opens_no_connection(tmp_path):
    with patch('subprocess.Popen', side_effect=AssertionError('no process')), patch('socket.socket.connect', side_effect=AssertionError('no network')), \
            patch('urllib.request.urlopen', side_effect=AssertionError('no network')):
        assert run(tmp_path, FULL)[0] == 0


def test_the_recognized_names_cover_the_pinned_read_tools_and_the_optional_follow_up_is_not_required():
    assert set(PINNED_SCHEMAS) <= RECOGNIZED and set(REQUIRED) == set(PINNED_SCHEMAS) - {'get_tool_uses'}
