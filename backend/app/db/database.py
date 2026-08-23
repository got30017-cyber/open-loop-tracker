from sqlite3 import Connection as SQLiteConnection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    """Declarative base shared by all persistence models."""


def create_database_engine(database_url: str) -> Engine:
    """Create an engine with SQLite settings suitable for local use and tests."""
    connect_args = (
        {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    )
    database_engine = create_engine(database_url, connect_args=connect_args)

    if database_url.startswith("sqlite"):

        @event.listens_for(database_engine, "connect")
        def enable_sqlite_foreign_keys(
            dbapi_connection: SQLiteConnection, _connection_record: object
        ) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return database_engine


def create_tables(bind: Engine) -> None:
    """Create all currently registered tables for local/demo use."""
    import app.db.models  # noqa: F401 - registers models with Base metadata

    Base.metadata.create_all(bind)


settings = get_settings()
engine = create_database_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
