"""The ATIF import reference spells out every field of the mapping contract and every typed rejection (#38)."""
from pathlib import Path

import pytest

from agent_harness_lab.interop.mapping import ATIF_FIELDS, CONTAINER, REASONS

DOC = (Path(__file__).resolve().parents[3] / 'docs' / 'atif-import-reference.md').read_text()
PATHS = [prefix + '.' + key for prefix, table in ATIF_FIELDS.items() for key, how in table.items() if how != CONTAINER]


@pytest.mark.parametrize('path', PATHS)
def test_every_mapped_field_has_its_full_path_in_the_reference(path):
    assert '`' + path + '`' in DOC


@pytest.mark.parametrize('reason', REASONS)
def test_every_typed_rejection_reason_is_documented(reason):
    assert '`' + reason + '`' in DOC
