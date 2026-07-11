"""
Endpoint-level tests for the statement pipeline now that it runs as a
Celery task (core/tasks/statements.py). tests/conftest.py's autouse
_celery_eager_mode fixture makes .delay() run synchronously in-process, so
an ordinary APIClient request exercises the whole pipeline with no live
broker/worker — but note the task re-fetches its own StatementFile row from
the DB rather than sharing the view's in-memory instance, so even in eager
mode the HTTP response reflects the pre-enqueue state (status="uploaded",
is_processing=True); only a fresh DB read (or GET /statements/{id}) sees the
task's result. That gap is the real, intentional async contract this phase
introduced, not a test artifact — asserted explicitly below.

fake_redis is required (not just present via conftest) because
process_statement_pipeline publishes a statement_status event on every run
(core/tasks/statements.py) — without it, the task would try to reach a real
Redis and fail.
"""

import io

import pytest
from rest_framework.test import APIClient

from core.models import StatementFile, User


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="statements-test@example.com", password="x", name="Statements Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def test_upload_enqueues_pipeline_and_normalizes_synchronously_in_eager_mode(
    client, user, fake_redis, moto_storage
):
    upload = io.BytesIO(b"dummy statement bytes")
    upload.name = "statement.pdf"

    response = client.post("/statements/", {"file": upload}, format="multipart")

    assert response.status_code == 202
    # The response body reflects the view's own in-memory statement object,
    # captured before .delay() ran — never the task's mutations, even though
    # the task has, in eager mode, already fully completed by this point.
    assert response.data["status"] == StatementFile.STATUS_UPLOADED
    assert response.data["is_processing"] is True
    assert response.data["transactions"] is None

    statement = StatementFile.objects.get(id=response.data["id"])
    assert statement.status == StatementFile.STATUS_NORMALIZED
    assert statement.is_processing is False
    assert statement.account is not None
    assert statement.normalized_records.exists()


def test_patch_retry_guard_rejects_double_processing(client, user, fake_redis, moto_storage):
    upload = io.BytesIO(b"dummy statement bytes for retry test")
    upload.name = "statement.pdf"
    created = client.post("/statements/", {"file": upload}, format="multipart").data

    # Already normalized (eager mode ran the full pipeline synchronously) —
    # retrying toward "extracted" is backward, not forward.
    response = client.patch(f"/statements/{created['id']}/", {"status": "extracted"})
    assert response.status_code == 422
    assert response.data["error"]["code"] == "invalid_status_transition"
