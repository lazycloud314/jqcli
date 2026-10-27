import pytest


@pytest.fixture(autouse=True)
def clear_jqcli_env(monkeypatch, tmp_path):
    for key in ("JQCLI_TOKEN", "JQCLI_COOKIE", "JQCLI_USERNAME", "JQCLI_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    # Never import the developer's real account/password from a checkout .env.
    monkeypatch.setattr("jqcli.config.default_env_path", lambda: tmp_path / "absent.env")

