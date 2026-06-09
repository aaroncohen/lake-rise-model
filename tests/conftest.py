import pytest

import lake_rise.settings as settings
from lake_rise.artifact import load_artifact


@pytest.fixture
def art():
    return load_artifact()


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Keep tests isolated from a developer's .env or exported HA creds."""
    monkeypatch.setattr(settings, "_DOTENV_LOADED", True)  # _load_dotenv_once -> no-op
    for k in ("HA_URL", "HA_TOKEN", "LAKE_RISE_LAKE_SENSOR", "LAKE_RISE_RAIN_SENSOR",
              "LAKE_RISE_FORECAST_ENTITY", "LAKE_RISE_STOPLOG_HELPER", "LAKE_RISE_ARTIFACT"):
        monkeypatch.delenv(k, raising=False)
