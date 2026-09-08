"""SQLAlchemy engine and session factory."""

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backend.infrastructure.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine, _SessionLocal
    settings = get_settings()
    db_url = url or settings.database_url
    if _engine is None or url is not None:
        engine = create_engine(db_url, pool_pre_ping=True, future=True)
        if url is None:
            _engine = engine
            _SessionLocal = sessionmaker(
                bind=engine, autoflush=False, autocommit=False, future=True
            )
            return _engine
        return engine
    return _engine


def get_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    global _SessionLocal
    if engine is not None:
        return sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    if _SessionLocal is None:
        get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


def get_db_session() -> Generator[Session, None, None]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
