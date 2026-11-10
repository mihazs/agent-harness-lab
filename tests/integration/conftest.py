import pytest

from tests.support.instruction_fixture import InstructionFixture


@pytest.fixture
def ctx(tmp_path):
    return InstructionFixture(tmp_path)
