import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    environment: str
    database_url: str
    client_reminder_1_minutes: int = 120
    client_reminder_2_minutes: int = 360
    client_escalation_minutes: int = 1440
    moderator_reminder_1_minutes: int = 30
    moderator_reminder_2_minutes: int = 120
    moderator_escalation_minutes: int = 240
    delivery_max_attempts: int = 3
    delivery_retry_delay_minutes: int = 5

    def __post_init__(self) -> None:
        client_thresholds = (
            self.client_reminder_1_minutes,
            self.client_reminder_2_minutes,
            self.client_escalation_minutes,
        )
        moderator_thresholds = (
            self.moderator_reminder_1_minutes,
            self.moderator_reminder_2_minutes,
            self.moderator_escalation_minutes,
        )
        if not 0 < client_thresholds[0] < client_thresholds[1] < client_thresholds[2]:
            raise ValueError(
                "Client SLA thresholds must be positive and strictly increasing"
            )
        if not (
            0
            < moderator_thresholds[0]
            < moderator_thresholds[1]
            < moderator_thresholds[2]
        ):
            raise ValueError(
                "Moderator SLA thresholds must be positive and strictly increasing"
            )
        if self.delivery_max_attempts < 1:
            raise ValueError("Delivery max attempts must be at least 1")
        if self.delivery_retry_delay_minutes < 0:
            raise ValueError("Delivery retry delay must not be negative")


def _environment_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


@lru_cache
def get_settings() -> Settings:
    """Load settings from the environment, with local-development defaults."""
    return Settings(
        app_name=os.getenv("APP_NAME", "Open Loop Tracker"),
        environment=os.getenv("APP_ENV", "development"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./open_loop_tracker.db"),
        client_reminder_1_minutes=_environment_int(
            "CLIENT_REMINDER_1_MINUTES", 120
        ),
        client_reminder_2_minutes=_environment_int(
            "CLIENT_REMINDER_2_MINUTES", 360
        ),
        client_escalation_minutes=_environment_int(
            "CLIENT_ESCALATION_MINUTES", 1440
        ),
        moderator_reminder_1_minutes=_environment_int(
            "MODERATOR_REMINDER_1_MINUTES", 30
        ),
        moderator_reminder_2_minutes=_environment_int(
            "MODERATOR_REMINDER_2_MINUTES", 120
        ),
        moderator_escalation_minutes=_environment_int(
            "MODERATOR_ESCALATION_MINUTES", 240
        ),
        delivery_max_attempts=_environment_int("DELIVERY_MAX_ATTEMPTS", 3),
        delivery_retry_delay_minutes=_environment_int(
            "DELIVERY_RETRY_DELAY_MINUTES", 5
        ),
    )
