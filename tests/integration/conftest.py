import pytest

from tests.support.instruction_fixture import InstructionFixture


@pytest.fixture
def ctx(tmp_path):
    return InstructionFixture(tmp_path)


@pytest.fixture
def trajectory():
    from tests.support.trajectory_fixture import trajectory_fixture

    with trajectory_fixture() as value:
        yield value
