import numpy as np
import pytest

from genidtals import BiometricEngine, synthetic


@pytest.fixture
def rng():
    return np.random.default_rng(20261024)


@pytest.fixture(scope="session")
def specimens():
    return {i: synthetic.make_specimen(i) for i in range(1, 9)}


@pytest.fixture
def engine(tmp_path):
    return BiometricEngine(security_level="standard", store_dir=tmp_path / "store")
