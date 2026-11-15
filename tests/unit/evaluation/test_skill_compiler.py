"""Hardened experience-to-skill hypothesis compiler (#11); synthetic procedures only, no private data."""
# fixed-in: 812b9d7c -k multi_line_answer or env_assignment
import copy
import json

import pytest

from agent_harness_lab.evaluation.skills import compile_procedure

HARD_CAP = 16384


def record(**changes):
    value = dict(approved=True, verified_success=True, outcome_provenance='executable_grader', scope='project',
                 procedure='Read the failing check output\nRerun only the failing check after each edit',
                 prerequisites=['a local fixture exists'], activate_when=['a check fails after an edit'],
                 do_not_activate_when=['the task is documentation only'], counterexamples=['a control task that needs no rerun'],
                 evidence_refs=['synthetic-evidence-1'])
    value.update(changes)
    return value


def compiled(value=None, **kwargs):
    return compile_procedure(record() if value is None else value, enabled=True, **kwargs)


def size(value):
    return len(json.dumps(value, ensure_ascii=False).encode())


# Credential-shaped samples are assembled at run time so no scanner sees a literal token in the repository.
CREDENTIALS = {
    'private_key': '-----BEGIN ' + 'RSA PRIVATE KEY-----',
    'aws_access_key': 'AK' + 'IA' + 'A' * 16,
    'github_token': 'gh' + 'p_' + 'a1' * 18,
    'api_key': 's' + 'k-' + 'a1b2' * 6,
    'slack_token': 'xo' + 'xb-' + '1234567890-abcdef',
    'jwt': 'ey' + 'J' + 'a' * 10 + '.' + 'b' * 10 + '.' + 'c' * 10,
    'bearer': 'Bear' + 'er ' + 'abcdefgh12345678',
    'assignment': 'pass' + 'word = ' + 'correcthorse123',
    'prefixed_env_assignment': 'export DB_PASS' + 'WORD=' + 'hunter2' * 3,
    'suffixed_env_assignment': 'SEC' + 'RET_KEY=' + 'a1b2c3d4' * 3,
    'prefixed_and_suffixed_env_assignment': 'AWS_SEC' + 'RET_ACCESS_KEY=' + 'a1b2' * 10,
    'lowercase_prefixed_colon_assignment': 'db_pass' + 'word: ' + 'hunter2' * 3,
}


class TestDefaultOffAndProvenance:
    def test_compilation_is_disabled_by_default_and_ignores_the_input(self):
        assert compile_procedure({}) == dict(status='disabled')
        assert compile_procedure('not a record') == dict(status='disabled')

    def test_an_approved_verified_project_procedure_is_only_a_hypothesis_that_needs_independent_evaluation(self):
        result = compiled()
        assert result['status'] == 'hypothesis'
        assert result['needs_independent_evaluation'] is True
        assert result['global_install'] is False and result['automatic_promotion'] is False
        assert result['procedure']['procedure'] == record()['procedure']

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(approved=False), id='not-approved'),
        pytest.param(dict(approved='yes'), id='approved-not-boolean'),
        pytest.param(dict(verified_success=False), id='outcome-not-verified'),
        pytest.param(dict(outcome_provenance='model_hypothesis'), id='model-claimed-outcome'),
        pytest.param(dict(outcome_provenance=None), id='unknown-outcome-provenance'),
        pytest.param(dict(scope='global'), id='global-scope'),
        pytest.param(dict(scope='unknown'), id='unknown-scope'),
    ])
    def test_unapproved_or_unverified_inputs_are_rejected(self, changes):
        with pytest.raises(ValueError, match='approved verified procedures'):
            compiled(record(**changes))

    @pytest.mark.parametrize('field', ['procedure', 'prerequisites', 'activate_when', 'do_not_activate_when', 'counterexamples',
                                       'evidence_refs'])
    def test_a_missing_field_is_rejected(self, field):
        value = record()
        del value[field]
        with pytest.raises(ValueError, match='approved verified procedures'):
            compiled(value)

    @pytest.mark.parametrize('value', [pytest.param(None, id='none'), pytest.param([], id='list'), pytest.param('text', id='text')])
    def test_a_non_object_record_is_a_value_error_not_an_attribute_error(self, value):
        with pytest.raises(ValueError, match='must be an object'):
            compile_procedure(value, enabled=True)


class TestInputBoundary:
    @pytest.mark.parametrize('field', ['held_out_tasks', 'grader_feedback', 'task_answer', 'expected_output', 'extra'])
    def test_undeclared_fields_never_reach_compilation(self, field):
        with pytest.raises(ValueError, match='undeclared'):
            compiled(record(**{field: 'anything'}))

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(procedure=123), id='procedure-number'),
        pytest.param(dict(procedure=['step']), id='procedure-list'),
        pytest.param(dict(procedure='   \n '), id='procedure-blank'),
        pytest.param(dict(prerequisites='one prerequisite'), id='list-field-as-text'),
        pytest.param(dict(activate_when=[1]), id='non-text-entry'),
        pytest.param(dict(counterexamples=['']), id='empty-entry'),
        pytest.param(dict(evidence_refs=[' ']), id='blank-entry'),
        pytest.param(dict(do_not_activate_when={'a': 'b'}), id='mapping-field'),
    ])
    def test_malformed_procedure_fields_are_rejected(self, changes):
        with pytest.raises(ValueError, match='non-empty text'):
            compiled(record(**changes))


class TestBound:
    def test_a_record_at_the_byte_bound_compiles_and_one_byte_less_is_rejected(self):
        value = record(procedure='é' * 40)
        assert compiled(value, max_bytes=size(value))['status'] == 'hypothesis'
        with pytest.raises(ValueError, match='bound'):
            compiled(value, max_bytes=size(value) - 1)

    def test_the_default_bound_is_the_hard_cap(self):
        room = HARD_CAP - size(record(procedure=''))
        assert size(record(procedure='a' * room)) == HARD_CAP
        assert compiled(record(procedure='a' * room))['status'] == 'hypothesis'
        with pytest.raises(ValueError, match='bound'):
            compiled(record(procedure='a' * (room + 1)))

    @pytest.mark.parametrize('bound', [
        pytest.param(0, id='zero'), pytest.param(-1, id='negative'), pytest.param(True, id='boolean'),
        pytest.param('100', id='text'), pytest.param(None, id='none'), pytest.param(HARD_CAP + 1, id='above-hard-cap')])
    def test_an_invalid_bound_is_rejected_before_compilation(self, bound):
        with pytest.raises(ValueError, match='max_bytes'):
            compiled(max_bytes=bound)


class TestCredentials:
    @pytest.mark.parametrize('field', ['procedure', 'prerequisites', 'counterexamples', 'evidence_refs'])
    @pytest.mark.parametrize('secret', list(CREDENTIALS.values()), ids=list(CREDENTIALS))
    def test_a_credential_shaped_string_in_any_text_field_is_rejected_without_echoing_it(self, field, secret):
        value = record(**{field: f'Use {secret} here' if field == 'procedure' else [f'Use {secret} here']})
        with pytest.raises(ValueError, match='credential') as raised:
            compiled(value)
        assert secret not in str(raised.value)

    @pytest.mark.parametrize('text', [
        pytest.param('Keep the token budget under the limit', id='token-in-prose'),
        pytest.param('Prefer a task-queue check before the risk-register edit', id='sk-inside-words'),
        pytest.param('Set the password policy field in the fixture only', id='password-word-without-value'),
        pytest.param('Record the secret: none', id='short-value'),
        pytest.param('Read DB_PASSWORD from the environment and never inline it', id='env-name-without-value'),
        pytest.param('Rotate the SECRET_KEY after each release', id='suffixed-env-name-in-prose'),
        pytest.param('Export DB_PASSWORD=changeme before the run', id='short-env-value'),
        pytest.param('Keep TOKENS: a long list of unrelated words here', id='plural-word-is-not-a-key'),
    ])
    def test_ordinary_prose_that_mentions_credential_words_compiles(self, text):
        assert compiled(record(procedure=text))['status'] == 'hypothesis'

    def test_the_credential_screen_is_reported_as_a_pattern_match_not_proof(self):
        assert compiled()['screens']['credential'] == 'no_pattern_match'


class TestAnswerCopying:
    ANSWER = 'blue-heron-42'

    @pytest.mark.parametrize('text', [
        pytest.param('Answer with blue-heron-42', id='verbatim'),
        pytest.param('answer with  BLUE-Heron-42.', id='case-whitespace-punctuation'),
        pytest.param('Step one\nWrite "blue-heron-42" to the result', id='quoted-in-later-step'),
    ])
    def test_a_procedure_that_copies_a_held_out_answer_is_rejected(self, text):
        with pytest.raises(ValueError, match='held-out answer'):
            compiled(record(procedure=text), held_out=[self.ANSWER])

    MULTILINE_ANSWER = 'the quick brown fox\njumps over the lazy dog'

    @pytest.mark.parametrize('procedure', [
        pytest.param('the quick brown fox\njumps over the lazy dog', id='verbatim-lines'),
        pytest.param('Step one\nThe  QUICK brown fox\n\nJumps over the lazy dog.\nStep last', id='folded-lines-around-other-steps'),
        pytest.param('the quick brown fox\r\njumps over the lazy dog', id='crlf-lines'),
    ])
    def test_a_multi_line_answer_copied_across_procedure_lines_is_rejected(self, procedure):
        with pytest.raises(ValueError, match='held-out answer'):
            compiled(record(procedure=procedure), held_out=[self.MULTILINE_ANSWER])

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(activate_when=['the quick brown fox', 'jumps over the lazy dog']), id='entries-of-one-field'),
        pytest.param(dict(procedure='Read the check\nuse the quick brown fox', prerequisites=['jumps over the lazy dog']),
                     id='procedure-end-and-next-field-start'),
        pytest.param(dict(counterexamples=['the quick brown fox'], evidence_refs=['jumps over the lazy dog']), id='adjacent-fields'),
    ])
    def test_a_multi_line_answer_split_across_entries_or_fields_is_rejected(self, changes):
        with pytest.raises(ValueError, match='held-out answer'):
            compiled(record(**changes), held_out=[self.MULTILINE_ANSWER])

    @pytest.mark.parametrize('changes', [
        pytest.param(dict(procedure='the quick brown fox'), id='first-line-only'),
        pytest.param(dict(procedure='jumps over the lazy dog\nthe quick brown fox'), id='lines-in-reverse-order'),
        pytest.param(dict(procedure='the quick brown fox\nRerun the check\njumps over the lazy dog'), id='lines-with-a-step-between'),
        pytest.param(dict(procedure='the quick brown fox\njumps over the lazy dogs'), id='last-word-extended'),
    ])
    def test_a_multi_line_answer_that_is_not_present_in_order_and_whole_compiles(self, changes):
        assert compiled(record(**changes), held_out=[self.MULTILINE_ANSWER])['status'] == 'hypothesis'

    @pytest.mark.parametrize('field', ['prerequisites', 'activate_when', 'do_not_activate_when', 'counterexamples', 'evidence_refs'])
    def test_a_copied_answer_is_rejected_in_every_text_field(self, field):
        with pytest.raises(ValueError, match='held-out answer'):
            compiled(record(**{field: [f'the result is {self.ANSWER}']}), held_out=[self.ANSWER])

    def test_a_procedure_without_the_answer_compiles_and_reports_the_screen_ran(self):
        assert compiled(held_out=[self.ANSWER])['screens']['answer_copy'] == 'no_match'

    def test_the_answer_screen_is_not_run_without_held_out_answers_and_says_so(self):
        assert compiled()['screens']['answer_copy'] == 'not_run'
        assert compiled(held_out=[])['screens']['answer_copy'] == 'not_run'

    def test_an_answer_inside_a_longer_word_is_not_a_copy(self):
        assert compiled(record(procedure='Inspect the heronry log'), held_out=['heron'])['status'] == 'hypothesis'

    def test_an_answer_that_starts_and_ends_with_symbols_is_still_found_beside_other_text(self):
        with pytest.raises(ValueError, match='held-out answer'):
            compiled(record(procedure='Pay$1,000.50 first'), held_out=['$1,000.50'])

    @pytest.mark.parametrize('held_out', [
        pytest.param('blue-heron-42', id='bare-text-not-a-list'),
        pytest.param([42], id='non-text-entry'),
        pytest.param([' '], id='blank-entry'),
        pytest.param(['ab'], id='too-short-to-screen'),
        pytest.param(None, id='none'),
    ])
    def test_held_out_answers_that_cannot_be_screened_are_rejected_not_skipped(self, held_out):
        with pytest.raises(ValueError, match='held_out'):
            compiled(held_out=held_out)


class TestRedundancy:
    @pytest.mark.parametrize('changes', [
        pytest.param(dict(procedure='Rerun the check\nrerun   the CHECK'), id='procedure-step-repeated'),
        pytest.param(dict(activate_when=['a check fails', 'A  check fails']), id='activation-repeated'),
        pytest.param(dict(counterexamples=['control', 'control']), id='counterexample-repeated'),
    ])
    def test_a_repeated_entry_inside_the_record_is_rejected(self, changes):
        with pytest.raises(ValueError, match='redundant'):
            compiled(record(**changes))

    def test_a_step_already_in_the_existing_instructions_is_rejected(self):
        with pytest.raises(ValueError, match='redundant'):
            compiled(existing=['Be concise.\nRERUN only the failing check after each edit'])

    def test_new_steps_compile_against_existing_instructions_and_the_screen_is_reported(self):
        result = compiled(existing=['Be concise.'])
        assert result['screens']['redundancy'] == 'no_match'
        assert compiled()['screens']['redundancy'] == 'within_record_only'

    @pytest.mark.parametrize('existing', [
        pytest.param('Be concise.', id='bare-text-not-a-list'), pytest.param([3], id='non-text-entry'), pytest.param(None, id='none')])
    def test_existing_instructions_that_cannot_be_compared_are_rejected_not_skipped(self, existing):
        with pytest.raises(ValueError, match='existing'):
            compiled(existing=existing)


def test_compilation_does_not_modify_its_input():
    value = record()
    before = copy.deepcopy(value)
    compiled(value, held_out=['blue-heron-42'], existing=['Be concise.'])
    assert value == before
