import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    environment: str
    database_url: str


@lru_cache
def get_settings() -> Settings:
    """Load settings from the environment, with local-development defaults."""
    return Settings(
        app_name=os.getenv("APP_NAME", "Open Loop Tracker"),
        environment=os.getenv("APP_ENV", "development"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./open_loop_tracker.db"),
    )
