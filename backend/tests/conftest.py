from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.db import create_database_engine, create_tables


@pytest.fixture
def db_session(tmp_path: Path) -> Iterator[Session]:
    database_path = tmp_path / "open-loop-test.sqlite"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    create_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    with session_factory() as session:
        yield session

    engine.dispose()
