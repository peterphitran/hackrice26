from lou.core.settings import get_settings


def test_settings_load_local_defaults(monkeypatch) -> None:
    monkeypatch.delenv("LOU_ENVIRONMENT", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.environment == "local"
    assert settings.artifact_root.as_posix() == ".lou/artifacts"

    get_settings.cache_clear()
