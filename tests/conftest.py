import numpy as np
import pytest

from genidtals import BiometricEngine, EngineConfig, MemoryStore, StaticKeyProvider, generate_key, synthetic


@pytest.fixture
def rng():
    return np.random.default_rng(20261024)


@pytest.fixture(scope="session")
def specimens():
    return {i: synthetic.make_specimen(i) for i in range(1, 9)}


@pytest.fixture
def keys():
    return StaticKeyProvider([generate_key()])


@pytest.fixture
def make_engine(keys):
    def make(level="standard", store=None, **config):
        return BiometricEngine(config=EngineConfig(security_level=level, **config),
                               store=store if store is not None else MemoryStore(), keys=keys)
    return make


@pytest.fixture
def engine(make_engine):
    return make_engine()


@pytest.fixture
def captures(rng):
    def take(specimen, n, **kw):
        kw.setdefault("hard", True)
        return [synthetic.capture(specimen, rng, **kw) for _ in range(n)]
    return take
