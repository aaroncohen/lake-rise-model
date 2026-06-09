"""The .env loader for serving config."""

import lake_rise.settings as settings


def test_dotenv_loads_and_real_env_wins(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "# comment\n"
        "export HA_URL=http://ha.local:8123\n"
        'HA_TOKEN="tok-from-file"\n'
        "LAKE_RISE_FORECAST_ENTITY=weather.custom\n"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "_DOTENV_LOADED", False)
    for k in ("HA_URL", "HA_TOKEN", "LAKE_RISE_FORECAST_ENTITY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HA_URL", "http://real-env:8123")  # real env must override the file

    cfg = settings.ha_config_from_env()
    assert cfg is not None
    assert cfg.base_url == "http://real-env:8123"          # shell env wins
    assert cfg.token == "tok-from-file"                    # quotes stripped, from file
    assert cfg.forecast_entity == "weather.custom"


def test_no_creds_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                            # no .env here
    monkeypatch.setattr(settings, "_DOTENV_LOADED", False)
    for k in ("HA_URL", "HA_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert settings.ha_config_from_env() is None
