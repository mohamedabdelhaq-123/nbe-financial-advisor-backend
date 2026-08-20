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

The submit/poll unit tests below (test_submit_failure_*, test_job_failed_*,
test_poll_*) call process_statement_pipeline directly, NOT via .delay() —
_celery_eager_mode's .delay monkeypatch is irrelevant to them since they
already have the task object in hand. This matters specifically for the
retry-branch tests: MockAIServiceClient.get_job_status() always returns
"succeeded" immediately (services/ai_service/mock_client.py), so the
self.retry() branch is never reached through the mock and would otherwise go
completely untested — these tests force it by monkeypatching
ai_service.get_client() with a fake client instead.
"""

import io
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import ConsentRecord, StatementFile, User
from core.tasks import statements as statements_module
from core.tasks.statements import (
    MAX_POLL_ATTEMPTS,
    POLL_INTERVAL_SECONDS,
    process_statement_pipeline,
)
from services.ai_service import AIServiceError


@pytest.fixture
def user(db):
    user = User.objects.create_user(
        email="statements-test@example.com", password="x", name="Statements Test"
    )
    # Uploading requires data_processing consent (core/permissions.py's
    # HasDataProcessingConsent) — granted here to match what onboarding does
    # for a real signup, so these tests exercise the normal-consent path.
    ConsentRecord.objects.create(
        user=user, consent_type="data_processing", policy_version="v2.0", granted_at=timezone.now()
    )
    return user


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def uploaded_statement(user):
    return StatementFile.objects.create(
        user=user,
        seaweed_file_id="raw/abc",
        checksum="b" * 64,
        status=StatementFile.STATUS_UPLOADED,
        is_processing=True,
    )


class _FakeClient:
    """Stand-in for ai_service.get_client()'s return value — only the
    submit_process_job/get_job_status methods the extraction phase actually
    calls are implemented, each configurable to return a fixed value or
    raise."""

    def __init__(self, *, submit_process_job=None, get_job_status=None):
        self._submit_process_job = submit_process_job
        self._get_job_status = get_job_status
        self.submit_process_job_calls = 0
        self.get_job_status_calls = 0

    def submit_process_job(self, statement_id):
        self.submit_process_job_calls += 1
        if isinstance(self._submit_process_job, Exception):
            raise self._submit_process_job
        return self._submit_process_job

    def get_job_status(self, job_id):
        self.get_job_status_calls += 1
        if isinstance(self._get_job_status, Exception):
            raise self._get_job_status
        return self._get_job_status


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


def test_submit_failure_records_reason_and_phase(uploaded_statement, fake_redis, monkeypatch):
    fake_client = _FakeClient(submit_process_job=AIServiceError("ai-service unreachable"))
    monkeypatch.setattr(statements_module.ai_service, "get_client", lambda: fake_client)
    publish = Mock()
    monkeypatch.setattr(statements_module.event_bus, "publish_user_event", publish)

    process_statement_pipeline(str(uploaded_statement.id), StatementFile.STATUS_EXTRACTED)

    uploaded_statement.refresh_from_db()
    assert uploaded_statement.status == StatementFile.STATUS_UPLOADED
    assert uploaded_statement.is_processing is False
    assert uploaded_statement.failed_phase == StatementFile.PHASE_EXTRACTION
    assert "ai-service unreachable" in uploaded_statement.failure_reason
    assert publish.call_count == 1


def test_job_failed_state_records_error_as_failure_reason(
    uploaded_statement, fake_redis, monkeypatch
):
    fake_client = _FakeClient(
        submit_process_job={
            "job_id": "job-1",
            "step": "process",
            "state": "queued",
            "submitted_at": "x",
        },
        get_job_status={"state": "failed", "error": "OCR engine timed out"},
    )
    monkeypatch.setattr(statements_module.ai_service, "get_client", lambda: fake_client)
    publish = Mock()
    monkeypatch.setattr(statements_module.event_bus, "publish_user_event", publish)

    process_statement_pipeline(str(uploaded_statement.id), StatementFile.STATUS_EXTRACTED)

    uploaded_statement.refresh_from_db()
    assert uploaded_statement.failure_reason == "OCR engine timed out"
    assert uploaded_statement.failed_phase == StatementFile.PHASE_EXTRACTION
    assert uploaded_statement.is_processing is False
    assert publish.call_count == 1


def test_poll_schedules_retry_when_job_still_running(uploaded_statement, fake_redis, monkeypatch):
    fake_client = _FakeClient(
        submit_process_job={
            "job_id": "job-1",
            "step": "process",
            "state": "queued",
            "submitted_at": "x",
        },
        get_job_status={"state": "running"},
    )
    monkeypatch.setattr(statements_module.ai_service, "get_client", lambda: fake_client)
    publish = Mock()
    monkeypatch.setattr(statements_module.event_bus, "publish_user_event", publish)
    retry_mock = Mock(side_effect=Retry("scheduling a poll"))
    monkeypatch.setattr(process_statement_pipeline, "retry", retry_mock)

    with pytest.raises(Retry):
        process_statement_pipeline(str(uploaded_statement.id), StatementFile.STATUS_EXTRACTED)

    retry_mock.assert_called_once_with(
        countdown=POLL_INTERVAL_SECONDS,
        max_retries=MAX_POLL_ATTEMPTS,
        args=(str(uploaded_statement.id), StatementFile.STATUS_EXTRACTED, "job-1", 1),
    )
    uploaded_statement.refresh_from_db()
    assert uploaded_statement.is_processing is True
    assert uploaded_statement.status == StatementFile.STATUS_UPLOADED
    publish.assert_not_called()


def test_poll_resumes_with_existing_job_id_without_resubmitting(
    uploaded_statement, fake_redis, monkeypatch
):
    fake_client = _FakeClient(
        get_job_status={
            "state": "succeeded",
            "result": {
                "prefix": "pfm-statements-ocr/x/",
                "ocr_engine": "MinerU",
                "confidence_score": 0.9,
            },
        }
    )
    monkeypatch.setattr(statements_module.ai_service, "get_client", lambda: fake_client)
    publish = Mock()
    monkeypatch.setattr(statements_module.event_bus, "publish_user_event", publish)

    process_statement_pipeline(
        str(uploaded_statement.id), StatementFile.STATUS_EXTRACTED, job_id="job-1", poll_attempt=1
    )

    assert fake_client.submit_process_job_calls == 0
    uploaded_statement.refresh_from_db()
    assert uploaded_statement.status == StatementFile.STATUS_EXTRACTED
    assert uploaded_statement.is_processing is False
    assert publish.call_count == 1


def test_poll_exhaustion_after_max_attempts_records_failure(
    uploaded_statement, fake_redis, monkeypatch
):
    fake_client = _FakeClient(get_job_status={"state": "running"})
    monkeypatch.setattr(statements_module.ai_service, "get_client", lambda: fake_client)
    publish = Mock()
    monkeypatch.setattr(statements_module.event_bus, "publish_user_event", publish)
    retry_mock = Mock(side_effect=AssertionError("retry should not be called at max attempts"))
    monkeypatch.setattr(process_statement_pipeline, "retry", retry_mock)

    process_statement_pipeline(
        str(uploaded_statement.id),
        StatementFile.STATUS_EXTRACTED,
        job_id="job-1",
        poll_attempt=MAX_POLL_ATTEMPTS,
    )

    retry_mock.assert_not_called()
    uploaded_statement.refresh_from_db()
    assert uploaded_statement.is_processing is False
    assert uploaded_statement.failed_phase == StatementFile.PHASE_EXTRACTION
    assert "did not complete" in uploaded_statement.failure_reason
    assert publish.call_count == 1
