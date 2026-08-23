from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.db import SessionLocal


def get_db_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
