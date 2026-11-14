"""Preparation outcome block (#822): allow-listed codes and counts from a proposal; pure logic, no free text."""
import pytest

from agent_harness_lab import preparation

BRIEF = dict(missing_evidence=[dict(id='verified_outcomes'), dict(id='targeted_probe')])
CLASSES = preparation.check_classes()
CHECK = CLASSES[0]


def build(proposal, **more):
    return preparation.block(proposal, brief=BRIEF, **more)


def test_check_classes_are_the_conventions_the_static_audit_evaluates():
    assert 'skill_routing_collision' in CLASSES and 'instruction_procedure_section_detected' in CLASSES


@pytest.mark.parametrize('proposal, identical, outcome', [
    (dict(status='candidate'), False, 'hypothesis_supplied'),
    (dict(status='candidate'), True, 'no_hypothesis'),
    (dict(status='no_change', reason_codes=['baseline_conforms']), False, 'no_change_supported'),
    (dict(status='no_change'), False, 'no_hypothesis'),
    (dict(status='no_change', reason_codes=[]), False, 'no_hypothesis'),
    (dict(status='no_change', reason_codes=['not_a_code']), False, 'no_hypothesis'),
    (dict(status='no_change', reason='free text only'), False, 'no_hypothesis'),
    (None, False, 'invalid_proposal'),
    (['no_change'], False, 'invalid_proposal'),
    (dict(status='maybe', reason_codes=['baseline_conforms']), False, 'invalid_proposal'),
    (dict(reason_codes=['baseline_conforms']), False, 'invalid_proposal'),
], ids=['candidate', 'identical_candidate', 'typed_no_change', 'bare_no_change', 'empty_codes', 'only_unknown_codes',
        'free_text_only', 'no_proposal', 'not_an_object', 'unknown_status', 'missing_status'])
def test_the_outcome_follows_the_proposal_status_and_never_counts_an_unknown_code_as_support(proposal, identical, outcome):
    assert build(proposal, identical=identical)['outcome'] == outcome
    assert outcome in preparation.OUTCOMES


@pytest.mark.parametrize('declared, kept, unrecognized', [
    (None, [], 0), ([], [], 0), ('baseline_conforms', [], 1), ({'baseline_conforms': 1}, [], 1), (7, [], 1),
    (['baseline_conforms', 'baseline_conforms'], ['baseline_conforms'], 0),
    (['evidence_ambiguous', 'baseline_conforms'], ['baseline_conforms', 'evidence_ambiguous'], 0),
    (['baseline_conforms', 'free text', 3, None, ['baseline_conforms'], {'a': 1}], ['baseline_conforms'], 5),
], ids=['absent', 'empty', 'string', 'mapping', 'number', 'duplicates', 'sorted', 'mixed_garbage'])
def test_reason_codes_keep_only_exact_vocabulary_names_and_count_the_rest(declared, kept, unrecognized):
    block = build(dict(status='no_change', reason_codes=declared))
    assert (block['reason_codes'], block['unrecognized']) == (kept, unrecognized)


def test_an_oversized_list_is_read_up_to_the_bound_and_the_rest_counts_as_unrecognized():
    declared = ['baseline_conforms'] * 3 + [f'x{number}' for number in range(10_000)]
    block = build(dict(status='no_change', reason_codes=declared))
    assert block['reason_codes'] == ['baseline_conforms'] and block['unrecognized'] == len(declared) - 3
    assert preparation.MAX_LISTED < len(declared)


def test_a_name_beyond_the_read_bound_is_never_recognized():
    declared = ['evidence_ambiguous'] * preparation.MAX_LISTED + ['baseline_conforms']
    block = build(dict(status='no_change', reason_codes=declared))
    assert block['reason_codes'] == ['evidence_ambiguous'] and block['unrecognized'] == 1


@pytest.mark.parametrize('brief, kept, unrecognized', [
    (BRIEF, ['verified_outcomes'], 1), (None, [], 2), (dict(), [], 2), (dict(missing_evidence=[]), [], 2),
    (dict(missing_evidence=['verified_outcomes']), [], 2),
], ids=['listed_only', 'no_brief', 'empty_brief', 'nothing_listed', 'malformed_rows'])
def test_missing_evidence_is_limited_to_the_ids_the_proposer_was_shown(brief, kept, unrecognized):
    block = preparation.block(dict(status='no_change', missing_evidence=['verified_outcomes', 'event_order']), brief=brief)
    assert (block['missing_evidence'], block['unrecognized']) == (kept, unrecognized)


def test_baseline_checks_report_a_count_and_the_class_names_only():
    block = build(dict(status='no_change', baseline_checks_passed=[CHECK, 'a free sentence about the project']))
    assert block['baseline_checks_passed'] == dict(count=1, classes=[CHECK]) and block['unrecognized'] == 1


def test_an_invalid_proposal_reads_no_declared_field():
    block = build(dict(status='maybe', reason_codes=['baseline_conforms'], missing_evidence=['verified_outcomes'],
                       baseline_checks_passed=[CHECK]))
    assert (block['reason_codes'], block['missing_evidence'], block['unrecognized']) == ([], [], 0)
    assert block['baseline_checks_passed'] == dict(count=0, classes=[])


def test_the_block_says_the_fields_are_declared_by_the_proposer_and_not_verified():
    block = build(dict(status='no_change', reason_codes=['baseline_conforms']))
    assert (block['profile'], block['declared_by'], block['controller_verified']) == (preparation.PROFILE, 'proposer', False)


def test_the_block_never_carries_the_proposal_free_text():
    secret = 'synthetic-private-sentence'
    block = build(dict(status='no_change', reason=secret, mechanisms=[secret], falsifiers=[secret], note=secret,
                       reason_codes=[secret, 'baseline_conforms']))
    assert secret not in repr(block)


def test_the_terminal_line_is_one_line_of_allow_listed_names():
    block = build(dict(status='no_change', reason_codes=['baseline_conforms', 'evidence_ambiguous'],
                       missing_evidence=['verified_outcomes'], baseline_checks_passed=[CHECK]))
    assert preparation.line(block) == ('ahl: preparation outcome=no_change_supported reasons=baseline_conforms,evidence_ambiguous '
                                       'missing_evidence=verified_outcomes baseline_checks_passed=1')


def test_the_terminal_line_names_none_for_an_empty_list():
    assert preparation.line(build(None)) == 'ahl: preparation outcome=invalid_proposal reasons=none missing_evidence=none baseline_checks_passed=0'


def test_the_report_section_names_the_outcome_and_states_the_fields_are_unverified():
    text = preparation.section(build(dict(status='no_change', reason_codes=['baseline_conforms'])))
    assert text.startswith('\n## Preparation outcome\n') and '`no_change_supported`' in text and 'did not verify' in text
    assert '`baseline_conforms`' in text


def test_the_guidance_lists_every_allowed_name():
    text = preparation.guidance()
    assert all(name in text for name in (*preparation.REASON_CODES, *CLASSES, 'missing_evidence'))
