from lou.core.settings import Settings
from lou.persistence.database import create_database_engine


def test_database_engine_uses_configured_url() -> None:
    settings = Settings(database_url="sqlite://")

    engine = create_database_engine(settings)

    assert str(engine.url) == "sqlite://"
