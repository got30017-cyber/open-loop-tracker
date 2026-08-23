from collections.abc import Iterator
from datetime import datetime

from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.db.models.common import utc_now


def get_db_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def get_current_time() -> datetime:
    return utc_now()
