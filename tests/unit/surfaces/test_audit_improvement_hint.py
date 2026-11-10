"""Only fixed instruction-hypothesis hints may leave the private audit report."""
from agent_harness_lab.audit import improvement_hint


def test_only_allowlisted_instruction_hypotheses_are_mapped():
    assert improvement_hint.from_findings([dict(dimension='instructions', status='weak', reason='UNTRUSTED_CONTENT')]) is None
    assert improvement_hint.from_findings([dict(dimension='hooks', status='weak', reason='instruction_empty_import')]) is None
    assert improvement_hint.from_findings([dict(dimension='instructions', status='unknown', reason='instruction_empty_import')]) is None
    row = dict(dimension='instructions', status='weak', reason='instruction_empty_import',
               path='SYNTHETIC_PRIVATE_PATH', measures=[['bytes', 123]], id='synthetic-private-id')
    hint = improvement_hint.from_findings([row])
    assert hint['focus'] == 'Investigate whether an empty instruction import is intentional.'
    assert all(value not in repr(hint) for value in ('SYNTHETIC_PRIVATE_PATH', 'synthetic-private-id', '123'))
    assert hint['publication_allowed'] is False and hint['authority'] == 'none'
