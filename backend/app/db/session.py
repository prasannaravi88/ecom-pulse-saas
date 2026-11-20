import logging
from collections.abc import Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

log = logging.getLogger("ecom_pulse")


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):  # enforce FK cascades on SQLite
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    return create_engine(url, pool_pre_ping=True)


engine = make_engine(get_settings().database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db(bind: Engine = engine) -> None:
    from app.db import models

    if bind.dialect.name == "postgresql":
        try:
            with bind.begin() as conn:
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception as exc:  # managed Postgres without pgvector, or no CREATE privilege
            log.warning("pgvector unavailable (%s); storing embeddings as JSON", exc.__class__.__name__)
            models.VECTOR_AVAILABLE = False
    models.Base.metadata.create_all(bind)


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def analyze(session: Session, tables: tuple[str, ...] = ("orders", "order_items", "daily_ad_spend", "products")) -> None:
    """Refresh Postgres planner statistics after bulk inserts (seed, sandbox clone). No-op elsewhere."""
    if session.get_bind().dialect.name != "postgresql":
        return
    for table in tables:
        session.execute(text(f"ANALYZE {table}"))
    session.commit()
