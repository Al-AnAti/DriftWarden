"""SQLite engine and session factory.

One process touches the database from FastAPI worker threads and from the
engine task, so every write is serialised behind a lock and the file runs in
WAL mode so readers never block on a writer.
"""

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from web.db.models_db import Base


class Database:
    """Owns the SQLAlchemy engine and hands out sessions.

    Args:
        database_url: SQLAlchemy URL, normally ``sqlite:///./warden.db``.
    """

    def __init__(self, database_url: str) -> None:
        self.url = database_url
        connect_args: dict[str, Any] = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self.engine: Engine = create_engine(database_url, connect_args=connect_args, future=True)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", _enable_wal)
        self._factory = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)
        self.write_lock = threading.RLock()

    def init(self) -> None:
        """Create every table that does not exist yet and apply additive migrations."""
        Base.metadata.create_all(self.engine)
        self._migrate()
        logger.info("Database ready at {}", self.url)

    def _migrate(self) -> None:
        """Add columns introduced after a table was first created."""
        if not self.url.startswith("sqlite"):
            return
        with self.engine.begin() as connection:
            rows = connection.exec_driver_sql("PRAGMA table_info(pending_adoptions)").fetchall()
            columns = {str(row[1]) for row in rows}
            if columns and "kind" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE pending_adoptions "
                    "ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'adopt'"
                )
                logger.info("Migrated pending_adoptions: added kind column")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Read-only session context manager."""
        with self._factory() as session:
            yield session

    @contextmanager
    def write(self) -> Iterator[Session]:
        """Serialised write session: commits on success, rolls back on error."""
        with self.write_lock:
            with self._factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

    def close(self) -> None:
        """Dispose of pooled connections."""
        self.engine.dispose()


def _enable_wal(dbapi_connection: Any, _record: Any) -> None:
    """Switch a fresh SQLite connection to WAL with a sane busy timeout."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
