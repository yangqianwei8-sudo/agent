"""Shared pytest fixtures for Phase 2."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from backend.infrastructure.config import get_settings
from backend.models import Base


@pytest.fixture(scope="session")
def database_url() -> str:
    settings = get_settings()
    url = settings.test_database_url or settings.database_url
    if "postgresql" not in url:
        pytest.skip("PostgreSQL DATABASE_URL / TEST_DATABASE_URL not configured")
    return url


@pytest.fixture(scope="session")
def engine(database_url: str):
    eng = create_engine(database_url, pool_pre_ping=True, future=True)
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def db_session(engine) -> Session:
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(bind=connection, autoflush=False, autocommit=False, future=True)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def actor_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def owner_id() -> uuid.UUID:
    return uuid.uuid4()
