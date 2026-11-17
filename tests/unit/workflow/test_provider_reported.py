"""Provider-reported block (#823): the runtime's own figures copied from its closing record, absent rather than zero; pure logic."""
import json

import pytest

from agent_harness_lab import provider_reported as reported

CLAUDE_RESULT = dict(type='result', subtype='success', is_error=False, duration_ms=4200, duration_api_ms=3900, num_turns=3,
                     total_cost_usd=0.0123, result='synthetic text', session_id='synthetic',
                     usage=dict(input_tokens=11, output_tokens=7, cache_creation_input_tokens=5, cache_read_input_tokens=3,
                                service_tier='standard', server_tool_use=dict(web_search_requests=1)))


def transcript(*events):
    return '\n'.join(event if isinstance(event, str) else json.dumps(event) for event in events) + '\n'


def claude(**changes):
    record = dict(CLAUDE_RESULT, **changes)
    for key in [key for key, value in changes.items() if value is ...]: record.pop(key)
    return reported.block(transcript(dict(type='system'), record), 'claude')


def test_a_claude_result_record_is_copied_with_its_label_and_no_verification():
    found = claude()
    assert found == dict(profile=reported.PROFILE, source='provider_reported_unverified', controller_verified=False,
                         certification_authority=False, duration_ms=4200, num_turns=3, reported_cost=0.0123, reported_cost_unit='usd',
                         usage=dict(input_tokens=11, output_tokens=7, cache_creation_input_tokens=5, cache_read_input_tokens=3))


def test_a_zero_figure_the_runtime_reported_stays_a_reported_zero():
    found = claude(total_cost_usd=0, num_turns=0, duration_ms=0)
    assert (found['reported_cost'], found['num_turns'], found['duration_ms']) == (0, 0, 0)


@pytest.mark.parametrize('missing, absent', [
    ('total_cost_usd', ('reported_cost', 'reported_cost_unit')),
    ('duration_ms', ('duration_ms',)),
    ('num_turns', ('num_turns',)),
    ('usage', ('usage',)),
], ids=['no_cost', 'no_duration', 'no_turns', 'no_usage'])
def test_a_field_the_result_record_lacks_is_absent_and_never_zero(missing, absent):
    found = claude(**{missing: ...})
    assert all(name not in found for name in absent)
    assert found['source'] == 'provider_reported_unverified'


@pytest.mark.parametrize('field, value', [
    ('total_cost_usd', None), ('total_cost_usd', '0.01'), ('total_cost_usd', -1), ('total_cost_usd', True),
    ('total_cost_usd', float('nan')), ('total_cost_usd', float('inf')), ('total_cost_usd', [0.01]),
    ('duration_ms', 'long'), ('duration_ms', -5), ('duration_ms', False),
    ('num_turns', 2.5), ('num_turns', -1), ('num_turns', True), ('num_turns', '3'),
], ids=lambda value: repr(value)[:24])
def test_a_malformed_figure_is_dropped_and_never_becomes_a_number(field, value):
    found = claude(**{field: value})
    name = 'reported_cost' if field == 'total_cost_usd' else field
    assert name not in found
    assert found['usage']['input_tokens'] == 11  # the other figures the record reported survive


@pytest.mark.parametrize('field, name', [('total_cost_usd', 'reported_cost'), ('duration_ms', 'duration_ms')])
@pytest.mark.parametrize('digits', [309, 400], ids=['first_overflowing_width', 'oversized'])
def test_an_integer_too_large_for_a_float_never_aborts_the_block_and_leaves_the_other_figures_intact(field, name, digits):
    big = '9' * digits  # a raw JSON integer: json.loads accepts it, float(big) overflows from 309 digits up (max float is 1.8e308)
    raw = json.dumps(dict(CLAUDE_RESULT, **{field: '@'})).replace('"@"', big)
    found = reported.block(transcript(raw), 'claude')
    assert found[name] == int(big) and found['num_turns'] == 3 and found['usage']['input_tokens'] == 11


def test_an_integer_beyond_the_json_digit_limit_is_not_a_record_and_gives_no_block():
    raw = '{"type":"result","total_cost_usd":1' + '0' * 5000 + '}'
    assert reported.block(transcript(raw), 'claude') is None


@pytest.mark.parametrize('usage', [None, [], 'tokens', {}, dict(input_tokens='11', output_tokens=-1, cache_read_input_tokens=1.5),
                                   dict(input_tokens=True), dict(service_tier='standard')],
                         ids=['null', 'list', 'string', 'empty', 'bad_counts', 'bool_count', 'no_counters'])
def test_usage_without_a_valid_integer_counter_is_absent(usage):
    assert 'usage' not in claude(usage=usage)


def test_usage_copies_only_the_integer_counters_and_nothing_nested_or_textual():
    found = claude(usage=dict(input_tokens=1, output_tokens=2, service_tier='standard', cache_creation=dict(ephemeral_5m_input_tokens=9)))
    assert found['usage'] == dict(input_tokens=1, output_tokens=2)


def test_a_record_with_no_usable_figure_has_no_block_at_all():
    assert claude(duration_ms=..., num_turns=..., total_cost_usd=..., usage=...) is None


@pytest.mark.parametrize('text', [
    '', '\n', 'not json\n', transcript(dict(type='system')), transcript(dict(type='assistant', total_cost_usd=1.0)),
    transcript(CLAUDE_RESULT, CLAUDE_RESULT), '{"type":"result"', '[1, 2]\n', 'null\n', '[' * 100_000 + '\n',
], ids=['empty', 'blank', 'garbage', 'no_result', 'cost_on_another_event', 'two_results', 'truncated', 'array', 'null', 'deeply_nested'])
def test_a_transcript_without_exactly_one_closing_record_yields_no_block(text):
    assert reported.block(text, 'claude') is None


def test_a_bad_line_beside_one_closing_record_does_not_hide_its_figures():
    assert reported.block(transcript('{broken', CLAUDE_RESULT), 'claude')['reported_cost'] == 0.0123


def test_codex_reports_token_usage_only_and_never_a_cost_or_a_turn_count():
    turn = dict(type='turn.completed', total_cost_usd=9.0, duration_ms=5, num_turns=4,
                usage=dict(input_tokens=20, cached_input_tokens=8, output_tokens=6, reasoning_output_tokens=2))
    found = reported.block(transcript(dict(type='thread.started'), turn), 'codex')
    assert found['usage'] == dict(input_tokens=20, cached_input_tokens=8, output_tokens=6, reasoning_output_tokens=2)
    assert not {'reported_cost', 'reported_cost_unit', 'duration_ms', 'num_turns'} & found.keys()


def test_codex_with_several_turns_has_no_block_because_no_single_record_closes_the_call():
    turn = dict(type='turn.completed', usage=dict(input_tokens=1))
    assert reported.block(transcript(turn, turn), 'codex') is None


@pytest.mark.parametrize('agent', [None, 'unknown-runtime', 'grader'])
def test_an_unknown_runtime_has_no_block(agent):
    assert reported.block(transcript(CLAUDE_RESULT), agent) is None


def test_record_adds_the_block_to_the_call_and_leaves_it_out_when_the_transcript_has_none():
    calls = []
    reported.record(calls, 'preparation', 0, transcript(CLAUDE_RESULT), 'claude')
    reported.record(calls, 'preparation', 1, transcript(dict(type='system')), 'claude')
    assert calls[0]['provider_reported']['reported_cost'] == 0.0123 and calls[0]['stage'] == 'preparation'
    assert calls[1] == dict(stage='preparation', attempt=1)


def test_the_terminal_line_names_each_call_and_labels_the_figure_unverified():
    calls = []
    reported.record(calls, 'preparation', 0, transcript(CLAUDE_RESULT), 'claude')
    assert reported.line(calls) == 'ahl: provider-reported cost: preparation-0=0.0123 usd (unverified)'


def test_the_terminal_line_never_sums_and_marks_a_call_without_a_cost_unreported():
    calls = []
    reported.record(calls, 'preparation', 0, transcript(dict(CLAUDE_RESULT, total_cost_usd=0.5)), 'claude')
    reported.record(calls, 'preparation', 1, transcript(dict(CLAUDE_RESULT, total_cost_usd=None)), 'claude')
    reported.record(calls, 'preparation', 2, transcript(dict(type='system')), 'claude')
    assert reported.line(calls) == 'ahl: provider-reported cost: preparation-0=0.5 usd, preparation-1=unreported, preparation-2=unreported (unverified)'


@pytest.mark.parametrize('calls', [None, [], 'calls', [dict(stage='preparation', attempt=0)],
                                   [dict(stage='preparation', attempt=0, provider_reported=dict(usage=dict(input_tokens=1)))]],
                         ids=['none', 'empty', 'not_a_list', 'call_without_block', 'block_without_cost'])
def test_no_terminal_line_when_no_call_reported_a_cost(calls):
    assert reported.line(calls) is None
