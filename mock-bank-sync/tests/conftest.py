"""
Runs against the real mock_bank_db (this service's models use
postgresql.UUID, which isn't portable to a SQLite in-memory DB), wrapping
each test in an outer transaction that's rolled back afterward — the
standard SQLAlchemy "join a session into an external transaction" recipe,
needed because the endpoint code under test calls session.commit() itself
(routes_simulate.py), which would otherwise end the outer transaction and
defeat a plain rollback-at-teardown approach.

Requires the app's normal env vars already be set in the environment this
runs in (POSTGRES_HOST, MOCK_BANK_DB_PASSWORD, etc. — already the case when
run via `docker compose exec mock-bank-sync pytest`, pointed at the same
migrated mock_bank_db the service itself uses).
"""

import pytest
from app.db import SessionLocal, engine, get_db
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import event


@pytest.fixture
def db_session():
    connection = engine.connect()
    outer_trans = connection.begin()
    session = SessionLocal(bind=connection)
    session.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def _restart_savepoint(sess, transaction):
        # routes_simulate.py's db.commit() ends the SAVEPOINT — this starts
        # a new one immediately so subsequent writes in the same test are
        # still contained within outer_trans, which is what actually gets
        # rolled back at teardown.
        if transaction.nested and not transaction._parent.nested:
            sess.begin_nested()

    yield session

    session.close()
    outer_trans.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
