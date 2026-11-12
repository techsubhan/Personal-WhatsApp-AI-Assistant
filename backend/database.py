"""
database.py
============
SQLAlchemy engine, session factory, and database lifecycle helpers.

This module is intentionally thin: it only knows how to connect to the
configured database and hand out sessions. Table definitions live in
``models.py`` to avoid circular imports and keep responsibilities separate.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, Session, DeclarativeBase

from config import settings

logger = logging.getLogger("whatsapp_ai.database")


class Base(DeclarativeBase):
    """Declarative base class shared by all ORM models."""
    pass


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# `check_same_thread=False` is required for SQLite when the same connection
# pool is shared across FastAPI's threaded request handlers.
_connect_args = {}
if settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args,
    pool_pre_ping=True,
    future=True,
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record) -> None:
    """
    Enable SQLite foreign key enforcement and WAL journaling for better
    concurrency, since FastAPI may issue overlapping requests.
    """
    if settings.database_url.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


def init_db() -> None:
    """
    Create all tables that do not yet exist.
    Import models here (not at module top-level) so that every model class
    has been registered on ``Base.metadata`` before ``create_all`` runs,
    without introducing a circular import between database.py and models.py.
    """
    import models  # noqa: F401  (import for side effect: table registration)

    Base.metadata.create_all(bind=engine)
    _run_additive_migrations()
    logger.info("Database initialized at %s", settings.database_url)


# Columns added to already-existing tables after the project's initial
# release. `Base.metadata.create_all()` above only creates missing
# TABLES - it never alters an existing table's columns - so a genuinely
# new column on a model that already has rows in a deployed database
# needs this tiny additive step, or every query against that table would
# start failing with "no such column" on any already-running deployment.
# Each entry is (table, column, column_ddl); safe to run on every
# startup - it only executes the ALTER TABLE when the column is missing.
_ADDITIVE_COLUMNS = [
    ("contacts", "detected_language", "VARCHAR(32)"),
    ("contacts", "web_search_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
]


def _run_additive_migrations() -> None:
    if not settings.database_url.startswith("sqlite"):
        # Only SQLite is used by this project (see .env.example); this
        # helper intentionally does not attempt to be a general-purpose
        # migration tool for other database engines.
        return
    with engine.connect() as conn:
        for table, column, ddl in _ADDITIVE_COLUMNS:
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                conn.commit()
                logger.info("Migration: added column %s.%s", table, column)


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a database session and guarantees
    it is closed after the request completes.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Context manager for use in non-FastAPI contexts (background jobs,
    scheduler tasks, admin scripts). Commits on success, rolls back on
    error, and always closes the session.
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
