"""SQLAlchemy engine/session setup for the mock bank's own database."""

from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

from app import config

# pool_pre_ping avoids handing out dead connections after the Postgres
# container restarts underneath a long-lived process.
engine = create_engine(config.database_url(), pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db():
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db_connection() -> bool:
    """Trivial `SELECT 1` used by GET /health."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
