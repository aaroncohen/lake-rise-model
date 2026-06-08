import pytest

from lake_rise.artifact import load_artifact


@pytest.fixture
def art():
    return load_artifact()
