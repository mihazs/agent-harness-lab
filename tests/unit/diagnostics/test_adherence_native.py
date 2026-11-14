"""Native Claude Code and Codex captures read as instruction-adherence sessions; synthetic files only."""
from types import SimpleNamespace

import pytest

from agent_harness_lab.diagnostics import adherence_native as native
from agent_harness_lab.diagnostics import instruction_adherence as adherence
from tests.support.native_session_fixture import claude, codex, write

RULES = [dict(layer='project', name='AGENTS.md', text='- Never run `git push --force`.\n')]


@pytest.fixture
def project(tmp_path):
    (tmp_path / 'project').mkdir()
    return tmp_path / 'project'


def turns(session):
    return session['turns']


def report(sessions, harness=RULES):
    return adherence.analyze(adherence.load_sessions(dict(profile=adherence.SESSIONS_PROFILE, sessions=sessions)), harness)


def test_claude_prompts_replies_and_calls_become_turns_and_results_and_meta_rows_do_not(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [
        ('meta', 'injected context'), ('user', 'Do not edit `notes.md`.'), ('agent', 'Understood.'),
        ('call', 'Bash', dict(command='git status')), ('result',), ('call', 'Edit', dict(file_path=str(project / 'a.py')))]))
    assert turns(native.read_session(path, 'claude', project)) == [
        dict(role='user', text='Do not edit `notes.md`.'), dict(role='agent', text='Understood.'),
        dict(role='tool_call', tool='Bash', command='git status'), dict(role='tool_call', tool='Edit', path=str(project / 'a.py'))]


def test_claude_session_is_partial_with_unknown_lineage_and_names_the_project(project, tmp_path):
    session = native.read_session(write(tmp_path / 's.jsonl', claude(project, [('user', 'hi')], session='abc')), 'claude', project)
    assert (session['session'], session['capture'], session['project_dir']) == ('abc', 'partial', str(project))
    assert 'root' not in session and session['loaded'] == {}


@pytest.mark.parametrize('name', ['AGENTS.md', 'CLAUDE.md'])
def test_claude_memory_attachment_marks_the_project_rule_file_loaded_from_that_turn(project, tmp_path, name):
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi'), ('memory', str(project / name)), ('call', 'Bash', dict(command='ls'))]))
    assert native.read_session(path, 'claude', project)['loaded'] == {f'project:{name}': dict(state='loaded', turn=1)}


@pytest.mark.parametrize('where', ['outside', 'nested'], ids=['outside the project', 'not at the project root'])
def test_claude_memory_attachment_that_is_not_a_root_rule_file_is_not_recorded(project, tmp_path, where):
    target = '/elsewhere/AGENTS.md' if where == 'outside' else str(project / 'docs' / 'AGENTS.md')
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi'), ('memory', target)]))
    assert native.read_session(path, 'claude', project)['loaded'] == {}


def test_claude_session_started_in_a_project_subdirectory_belongs_to_the_project(project, tmp_path):
    (project / 'sub').mkdir()
    path = write(tmp_path / 's.jsonl', claude(project / 'sub', [('user', 'hi')]))
    assert native.read_session(path, 'claude', project)['project_dir'] == str(project)


@pytest.mark.parametrize('rows,reason', [
    pytest.param(lambda p: claude(p, [('user', 'hi')], version='9.9.9'), 'unsupported_version', id='unverified release'),
    pytest.param(lambda p: claude(p.parent / 'other', [('user', 'hi')]), 'foreign_project', id='another project'),
    pytest.param(lambda p: claude(p.parent / 'project-sibling', [('user', 'hi')]), 'foreign_project', id='sibling prefix'),
    pytest.param(lambda p: claude(p, [('user', 'hi')], sidechain=True), 'subagent_transcript', id='subagent prompt is not a user'),
    pytest.param(lambda p: claude(p, [('user', 'a')], session='one') + claude(p, [('user', 'b')], session='two'), 'mixed_sessions', id='two sessions'),
    pytest.param(lambda p: [], 'no_session', id='empty file'),
    pytest.param(lambda p: [dict(type='queue-operation', sessionId='x')], 'no_session', id='no conversation rows'),
])
def test_claude_capture_that_cannot_be_attributed_is_skipped_with_a_typed_reason(project, tmp_path, rows, reason):
    with pytest.raises(native.Skipped) as error:
        native.read_session(write(tmp_path / 's.jsonl', rows(project)), 'claude', project)
    assert error.value.reason == reason


@pytest.mark.parametrize('content', ['{not json}\n', '[1, 2]\n', '{"a": 1, "a": 2}\n'], ids=['syntax', 'not an object', 'duplicate key'])
def test_malformed_capture_is_skipped_as_malformed(project, tmp_path, content):
    path = tmp_path / 's.jsonl'
    path.write_text(content)
    with pytest.raises(native.Skipped) as error:
        native.read_session(path, 'claude', project)
    assert error.value.reason == 'malformed'


def test_missing_and_linked_capture_files_are_skipped_as_unavailable(project, tmp_path):
    real = write(tmp_path / 'real.jsonl', claude(project, [('user', 'hi')]))
    link = tmp_path / 'link.jsonl'
    link.symlink_to(real)
    for path in (tmp_path / 'missing.jsonl', link):
        with pytest.raises(native.Skipped) as error:
            native.read_session(path, 'claude', project)
        assert error.value.reason == 'unavailable'


def test_claude_capture_with_an_unfinished_last_line_keeps_the_complete_prefix(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi')]), tail='{"type": "assistant", "mess')
    assert turns(native.read_session(path, 'claude', project)) == [dict(role='user', text='hi')]


def test_oversized_text_is_clipped_keeping_the_prompt_head_and_the_reply_tail_and_a_huge_command_becomes_undecidable(project, tmp_path):
    big = native.MAX_TEXT + 10
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'HEAD' + 'x' * big), ('agent', 'y' * big + 'Proceed?'),
                                                         ('call', 'Bash', dict(command='z' * big)), ('call', 'Bash', dict(command='a\0b'))]))
    user, agent, huge, nul = turns(native.read_session(path, 'claude', project))
    assert user['text'].startswith('HEAD') and len(user['text']) == native.MAX_TEXT
    assert agent['text'].endswith('Proceed?') and len(agent['text']) == native.MAX_TEXT
    assert 'command' not in huge and nul['command'] == 'ab'


def test_turns_past_the_bound_are_dropped_and_the_capture_stays_partial(project, tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'MAX_TURNS', 3)
    path = write(tmp_path / 's.jsonl', claude(project, [('user', str(n)) for n in range(5)]))
    session = native.read_session(path, 'claude', project)
    assert [t['text'] for t in turns(session)] == ['0', '1', '2'] and session['capture'] == 'partial'


def test_codex_prompts_replies_commands_and_patched_files_become_turns(project, tmp_path):
    patch = '*** Begin Patch\n*** Update File: src/a.py\n@@\n-x\n+y\n*** Add File: src/b.py\n+z\n*** Delete File: old.py\n*** End Patch\n'
    path = write(tmp_path / 'r.jsonl', codex(project, [
        ('user', 'Never edit `src/a.py`.'), ('agent', 'Ok.'), ('exec', 'git status'), ('shell', ['git', 'push', '--force']),
        ('local', ['rm', '-rf', 'build dir']), ('patch', patch)], session='thread-1'))
    session = native.read_session(path, 'codex', project)
    assert session['session'] == 'thread-1' and session['capture'] == 'partial' and 'root' not in session
    assert turns(session) == [
        dict(role='user', text='Never edit `src/a.py`.'), dict(role='agent', text='Ok.'),
        dict(role='tool_call', tool='exec_command', command='git status'), dict(role='tool_call', tool='shell', command='git push --force'),
        dict(role='tool_call', tool='shell', command="rm -rf 'build dir'"),
        dict(role='tool_call', tool='apply_patch', path='src/a.py'), dict(role='tool_call', tool='apply_patch', path='src/b.py'),
        dict(role='tool_call', tool='apply_patch', path='old.py')]


def test_codex_script_run_through_a_login_shell_wrapper_is_judged_as_the_script(project, tmp_path):
    path = write(tmp_path / 'r.jsonl', codex(project, [('shell', ['bash', '-lc', 'git push --force origin main'])]))
    assert turns(native.read_session(path, 'codex', project)) == [dict(role='tool_call', tool='shell', command='git push --force origin main')]


def test_codex_has_no_instruction_loading_signal_so_nothing_is_loaded(project, tmp_path):
    path = write(tmp_path / 'r.jsonl', codex(project, [('user', 'hi')]))
    assert native.read_session(path, 'codex', project)['loaded'] == {}


def test_codex_call_whose_arguments_are_unreadable_keeps_the_tool_without_a_command(project, tmp_path):
    rows = codex(project, [('user', 'hi')]) + [dict(type='response_item', timestamp='2026-01-01T00:00:00Z',
        payload=dict(type='function_call', name='exec_command', call_id='c', arguments='{not json'))]
    assert turns(native.read_session(write(tmp_path / 'r.jsonl', rows), 'codex', project))[-1] == dict(role='tool_call', tool='exec_command')


@pytest.mark.parametrize('rows,reason', [
    pytest.param(lambda p: codex(p, [('user', 'hi')], version='0.1.0'), 'unsupported_version', id='other release'),
    pytest.param(lambda p: codex(p.parent / 'other', [('user', 'hi')]), 'foreign_project', id='another project'),
    pytest.param(lambda p: codex(p, [('user', 'hi')])[1:], 'no_session', id='no session header'),
    pytest.param(lambda p: codex(p, [('user', 'hi')]) + codex(p, [])[:1], 'mixed_sessions', id='second header'),
])
def test_codex_capture_that_cannot_be_attributed_is_skipped_with_a_typed_reason(project, tmp_path, rows, reason):
    with pytest.raises(native.Skipped) as error:
        native.read_session(write(tmp_path / 'r.jsonl', rows(project)), 'codex', project)
    assert error.value.reason == reason


def test_unknown_runtime_is_refused(project, tmp_path):
    with pytest.raises(ValueError):
        native.read_session(write(tmp_path / 's.jsonl', []), 'opencode', project)


def test_read_sessions_counts_each_skip_reason_and_keeps_the_first_of_a_duplicated_session(project, tmp_path):
    first = write(tmp_path / 'a.jsonl', claude(project, [('user', 'first')], session='dup'))
    again = write(tmp_path / 'b.jsonl', claude(project, [('user', 'second')], session='dup'))
    old = write(tmp_path / 'c.jsonl', claude(project, [('user', 'x')], version='9.9.9'))
    sessions, skipped = native.read_sessions([('claude', first), ('claude', again), ('claude', old), ('claude', tmp_path / 'gone.jsonl')], project)
    assert [turns(s) for s in sessions] == [[dict(role='user', text='first')]]
    assert skipped == dict(duplicate_session=1, unsupported_version=1, unavailable=1)


def test_read_sessions_reads_at_most_the_file_bound_and_counts_the_rest(project, tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'MAX_FILES', 2)
    files = [('claude', write(tmp_path / f'{n}.jsonl', claude(project, [('user', str(n))], session=f's{n}'))) for n in range(4)]
    sessions, skipped = native.read_sessions(files, project)
    assert len(sessions) == 2 and skipped == dict(over_file_limit=2)


def test_read_sessions_stops_at_the_total_byte_budget_but_always_tries_the_first_file(project, tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'MAX_TOTAL_BYTES', 1)
    files = [('claude', write(tmp_path / f'{n}.jsonl', claude(project, [('user', str(n))], session=f's{n}'))) for n in range(3)]
    sessions, skipped = native.read_sessions(files, project)
    assert len(sessions) == 1 and skipped == dict(over_byte_budget=2)


def test_read_sessions_refuses_a_capture_larger_than_the_byte_bound(project, tmp_path, monkeypatch):
    monkeypatch.setattr(native, 'MAX_FILE_BYTES', 200)
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'x' * 400)]))
    assert native.read_sessions([('claude', path)], project) == ([], dict(oversized=1))


def test_prohibited_call_after_a_loaded_rule_is_an_observed_violation(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [('memory', str(project / 'AGENTS.md')), ('user', 'push it'),
                                                         ('call', 'Bash', dict(command='git push --force origin main'))]))
    found = report([native.read_session(path, 'claude', project)])['findings']
    assert [(f['status'], f['direction']) for f in found] == [('observed_violation', 'harness')]


def test_prohibited_call_without_load_evidence_is_only_a_possible_violation(project, tmp_path):
    path = write(tmp_path / 'r.jsonl', codex(project, [('user', 'push it'), ('exec', 'git push --force origin main')]))
    assert [f['status'] for f in report([native.read_session(path, 'codex', project)])['findings']] == ['possible_violation']


def test_user_prohibition_ignored_in_a_native_session_is_an_observed_violation(project, tmp_path):
    path = write(tmp_path / 'r.jsonl', codex(project, [('user', 'Do not run `make deploy`.'), ('exec', 'make deploy')]))
    found = report([native.read_session(path, 'codex', project)], harness=[])['findings']
    assert [(f['status'], f['direction']) for f in found] == [('observed_violation', 'user_prompt')]


def test_native_session_never_proves_a_skipped_required_step(project, tmp_path):
    harness = [dict(layer='project', name='AGENTS.md', text='- Always run `make test`.\n')]
    path = write(tmp_path / 's.jsonl', claude(project, [('memory', str(project / 'AGENTS.md')), ('user', 'fix it'), ('call', 'Edit', dict(file_path='a.py'))]))
    result = report([native.read_session(path, 'claude', project)], harness)
    assert result['findings'] == [] and result['decisions'] == dict(undecided_capture_partial=1)



def analysis(project, **fields):
    return SimpleNamespace(project=project, **fields)


def test_analysis_over_a_project_scope_names_no_unread_layer_and_a_global_scope_names_the_global_layer(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi')]))
    data = dict(sources=[dict(format='claude', path=str(path))])
    assert native.for_analysis(analysis(project), data)[0]['rule_layers_not_read'] == []
    assert native.for_analysis(analysis(project, scope='project+global'), data)[0]['rule_layers_not_read'] == ['global']


def test_analysis_of_a_pinned_explicit_capture_reads_the_named_file_though_the_source_records_only_a_path_digest(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi')]))
    data = dict(sources=[dict(format='claude', path_sha256='0' * 64)])
    section, _ = native.for_analysis(analysis(project, events=path, format='claude', source_root=None), data)
    assert section['status'] == 'completed' and section['sessions']['read'] == 1


@pytest.mark.parametrize('sources', [
    pytest.param([], id='no sources'),
    pytest.param([dict(format='normalized', path='/x.jsonl')], id='not a native format'),
    pytest.param([dict(format='claude', path_sha256='0' * 64)], id='native source without a path'),
])
def test_analysis_without_a_native_capture_path_is_not_run_and_reports_no_counts(project, sources):
    section, report = native.for_analysis(analysis(project), dict(sources=sources))
    assert (section['status'], section['reason'], report) == ('not_run', 'no_native_capture_path', None) and 'findings' not in section


def test_analysis_of_a_source_root_discovery_does_not_read_a_file_named_by_events(project, tmp_path):
    path = write(tmp_path / 's.jsonl', claude(project, [('user', 'hi')]))
    section, _ = native.for_analysis(analysis(project, events=path, format='claude', source_root=tmp_path), dict(sources=[dict(format='claude', path_sha256='0' * 64)]))
    assert section['status'] == 'not_run'


def test_analysis_reads_only_the_project_root_rule_files_and_not_a_linked_one(project, tmp_path):
    (project / 'AGENTS.md').write_text('- Never run `make deploy`.\n')
    (tmp_path / 'outside.md').write_text('- Never run `make test`.\n')
    (project / 'CLAUDE.md').symlink_to(tmp_path / 'outside.md')
    assert [(f['layer'], f['name']) for f in native.harness_files(project)] == [('project', 'AGENTS.md')]


def test_analysis_reports_the_same_report_whatever_order_the_sources_are_listed(project, tmp_path):
    one = write(tmp_path / 'a.jsonl', claude(project, [('user', 'Never run `make deploy`.'), ('call', 'Bash', dict(command='make deploy'))], session='a'))
    two = write(tmp_path / 'b.jsonl', claude(project, [('user', 'hi')], session='b'))
    listed = [dict(format='claude', path=str(path)) for path in (one, two)]
    first = native.for_analysis(analysis(project), dict(sources=listed))
    assert native.for_analysis(analysis(project), dict(sources=listed[::-1])) == first
