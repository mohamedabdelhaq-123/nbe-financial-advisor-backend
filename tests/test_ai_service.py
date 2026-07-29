"""
Unit tests for services/ai_service/'s mock/real client implementations. Two
concerns:

1. The mock branch (MockAIServiceClient) returns exactly the shape the real
   /internal/... endpoint uses — this is what lets the rest of the app (and
   its tests) treat the two implementations as interchangeable.
2. The real branch (AIServiceClient) builds the right request and parses the
   right response, without ever hitting the network — the module-level
   requests.Session in services/ai_service/ai_client.py is monkeypatched,
   same convention tests/conftest.py uses for fake_redis/moto_storage.
"""

import json

import pytest
import requests

from core.models import (
    BankAccount,
    Budget,
    BudgetAllocation,
    Category,
    Product,
    StatementFile,
    StatementOcrResult,
    Transaction,
    User,
)
from services import ai_service
from services.ai_service import ai_client as ai_client_module
from services.ai_service.ai_client import (
    _SYNC_INGESTION_TIMEOUT_SECONDS,
    _TIMEOUT_SECONDS,
    AIServiceClient,
)
from services.ai_service.mock_client import MockAIServiceClient


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="ai-service-test@example.com", password="x", name="AI Service Test"
    )


@pytest.fixture
def statement(user):
    return StatementFile.objects.create(user=user, seaweed_file_id="raw/abc", checksum="a" * 64)


@pytest.fixture
def ocr_result(statement):
    return StatementOcrResult.objects.create(
        statement=statement,
        seaweed_file_id="pfm-statements-ocr/x/",
        ocr_engine="MinerU",
        confidence_score="0.950",
    )


@pytest.fixture
def account(user):
    return BankAccount.objects.create(
        user=user, bank_name="Test Bank", account_number="1234"
    )


@pytest.fixture
def client():
    return MockAIServiceClient()


# ============================================================================
# get_client() factory
# ============================================================================


def test_get_client_returns_mock_when_flag_true(settings):
    settings.USE_MOCK_AI_SERVICE = True
    assert isinstance(ai_service.get_client(), MockAIServiceClient)


def test_get_client_returns_real_when_flag_false(settings):
    settings.USE_MOCK_AI_SERVICE = False
    assert isinstance(ai_service.get_client(), AIServiceClient)


# ============================================================================
# Mock branch — shape must match the real contract
# ============================================================================


def test_process_statement_mock_shape(client, user):
    result = client.process_statement("some-statement-id")
    assert set(result) == {"prefix", "ocr_engine", "confidence_score"}
    assert isinstance(result["prefix"], str)


def test_normalize_statement_mock_matches_real_transaction_shape(client, ocr_result):
    result = client.normalize_statement(str(ocr_result.id))
    assert set(result) == {"normalized_json", "model_used"}

    normalized = result["normalized_json"]
    assert set(normalized) == {"bank_name", "account_number", "transactions", "extra_fields"}
    # account_number is the real, unmasked value — not a masked hint like the
    # old account_hint field.
    assert not normalized["account_number"].startswith("****")
    assert len(normalized["transactions"]) == 3
    for i, txn in enumerate(normalized["transactions"]):
        expected_keys = {
            "transaction_date",
            "merchant_raw",
            "merchant_normalized",
            "ai_description",
            "category",
            "amount",
            "transaction_type",
            "balance",
            "duplicate_of",
        }
        if i == 0:
            # Only the first fabricated transaction carries extra_fields —
            # omit-when-empty applies per-transaction, same as the real
            # contract.
            expected_keys |= {"extra_fields"}
        assert set(txn) == expected_keys
        assert txn["category"] in {"housing", "food", "transport", "savings", "lifestyle", "other"}


def test_normalize_statement_flags_duplicate_within_window(client, ocr_result):
    statement = ocr_result.statement
    # Deterministic (seeded off statement.id) — the same call twice produces
    # the same fabricated transactions, so the first call's output tells us
    # exactly what to pre-create as a "real" duplicate before the second call.
    first = client.normalize_statement(str(ocr_result.id))
    txn = first["normalized_json"]["transactions"][0]
    assert txn["duplicate_of"] is None

    account = BankAccount.objects.create(
        user=statement.user, bank_name="Test Bank", account_number="1234"
    )
    Transaction.objects.create(
        user=statement.user,
        account=account,
        source="statement",
        transaction_date=txn["transaction_date"],
        amount=txn["amount"],
        transaction_type=txn["transaction_type"],
        merchant_raw="a completely different merchant name",
    )

    second = client.normalize_statement(str(ocr_result.id))
    matched = second["normalized_json"]["transactions"][0]
    assert matched["duplicate_of"] is not None


def test_submit_process_job_mock_is_queued(client, user):
    submission = client.submit_process_job("some-statement-id")
    assert set(submission) == {"job_id", "step", "state", "submitted_at"}
    assert submission["step"] == "process"
    assert submission["state"] == "queued"


def test_submit_normalize_job_mock_is_queued(client, ocr_result):
    submission = client.submit_normalize_job(str(ocr_result.id))
    assert submission["step"] == "normalize"
    assert submission["state"] == "queued"


def test_get_job_status_mock_process_succeeds_with_same_shape_as_process_statement(client, user):
    submission = client.submit_process_job("some-statement-id")
    status = client.get_job_status(submission["job_id"])

    assert set(status) == {
        "job_id",
        "function",
        "state",
        "submitted_at",
        "started_at",
        "finished_at",
        "result",
        "error",
    }
    assert status["function"] == "ingestion.process"
    assert status["state"] == "succeeded"
    assert status["error"] is None
    assert status["result"] == client.process_statement("some-statement-id")


def test_get_job_status_mock_normalize_succeeds_with_same_shape_as_normalize_statement(
    client, ocr_result
):
    submission = client.submit_normalize_job(str(ocr_result.id))
    status = client.get_job_status(submission["job_id"])

    assert status["function"] == "ingestion.normalize"
    assert status["state"] == "succeeded"
    assert status["result"]["normalized_json"]["transactions"]


def test_get_job_status_mock_is_stateless_across_client_instances(client, user):
    # No prior submit call in *this* client instance — job_id alone must be
    # enough to recompute the result, since a real Celery worker polling a
    # job may be a different process than the one that submitted it.
    job_id = "mock:process:some-statement-id"
    status = MockAIServiceClient().get_job_status(job_id)
    assert status["state"] == "succeeded"
    assert status["result"]["prefix"] == "pfm-statements-ocr/some-statement-id/"


def test_stream_chat_mock_yields_token_events_then_one_done_event(client, user):
    envelopes = list(client.stream_chat(str(user.id), str(user.id), "hello there"))
    assert [e["event"] for e in envelopes[:-1]] == ["token"] * len(envelopes[:-1])
    assert envelopes[-1]["event"] == "done"
    done_data = envelopes[-1]["data"]
    assert set(done_data) == {"content", "widget", "references"}


def test_stream_chat_mock_budget_keyword_produces_allocation_widget(client, user):
    budget = Budget.objects.create(user=user)
    BudgetAllocation.objects.create(
        budget=budget,
        category=Category.objects.get(name="housing"),
        allocated_percentage="30.00",
        allocated_amount="3000.00",
    )

    envelopes = list(client.stream_chat(str(user.id), str(user.id), "show me my budget"))
    done_data = envelopes[-1]["data"]
    assert done_data["widget"]["type"] == "allocation_slider"
    assert done_data["references"] == [{"target_type": "budget", "target_id": str(budget.id)}]


def test_match_recommendations_mock_shape(client, user):
    Product.objects.create(
        title="Premium Savings Account", description="low-fee savings", is_active=True
    )

    result = client.match_recommendations(str(user.id), "savings")
    assert set(result) == {"matches"}
    for match in result["matches"]:
        assert set(match) == {"product_id", "product_name", "similarity"}


def test_embed_transactions_mock_writes_vector_and_confirms(client, user, account):
    txn = Transaction.objects.create(
        user=user,
        account=account,
        transaction_date="2026-06-01",
        amount="100.00",
        transaction_type="debit",
    )

    result = client.embed_transactions([str(txn.id)])

    assert result == {"results": [{"transaction_id": str(txn.id), "status": "embedded"}]}
    txn.refresh_from_db()
    assert txn.embedding is not None
    assert len(txn.embedding) == 1536


def test_embed_transactions_mock_raises_on_unknown_id(client, db):
    with pytest.raises(ai_service.AIServiceError):
        client.embed_transactions(["00000000-0000-0000-0000-000000000000"])


def test_create_embeddings_mock_shape_and_dimensions(client, user):
    result = client.create_embeddings(["hello world", "second text"], dimensions=768)

    assert set(result) == {"object", "data", "model"}
    assert len(result["data"]) == 2
    assert [datum["index"] for datum in result["data"]] == [0, 1]
    assert all(len(datum["embedding"]) == 768 for datum in result["data"])


def test_create_embeddings_mock_defaults_dimensions(client, user):
    result = client.create_embeddings(["hello"])
    assert len(result["data"][0]["embedding"]) == 768


def test_run_post_ingestion_analysis_mock_computes_live_summary(client, user, account):
    Transaction.objects.create(
        user=user,
        account=account,
        transaction_date="2026-06-05",
        amount="5000.00",
        transaction_type="credit",
    )
    for day in ("2026-06-08", "2026-06-22"):
        Transaction.objects.create(
            user=user,
            account=account,
            transaction_date=day,
            amount="200.00",
            transaction_type="debit",
            category=Category.objects.get(name="food"),
            merchant_raw="Carrefour",
        )
    Transaction.objects.create(
        user=user,
        account=account,
        transaction_date="2026-06-15",
        amount="900.00",
        transaction_type="debit",
        category=Category.objects.get(name="lifestyle"),
        merchant_raw="Electronics Store",
    )

    result = client.run_post_ingestion_analysis(str(user.id), str(account.id), "2026-06")

    assert set(result) == {"summary", "recurring_charges", "anomalies"}
    summary = result["summary"]
    assert summary["total_income"] == 5000.0
    assert summary["total_expense"] == 1300.0
    assert summary["net"] == 3700.0
    assert summary["by_category"]["food"] == 400.0
    assert len(summary["embedding"]) == 1536

    # Carrefour appears twice — the mock's simple "2+ occurrences" heuristic.
    assert any(rc["merchant"] == "Carrefour" for rc in result["recurring_charges"])
    # The 900.00 lifestyle charge is the largest debit that month.
    assert result["anomalies"][0]["amount"] == 900.0


def test_run_post_ingestion_analysis_mock_null_summary_when_no_transactions(client, user, account):
    result = client.run_post_ingestion_analysis(str(user.id), str(account.id), "2026-01")
    assert result == {"summary": None, "recurring_charges": [], "anomalies": []}


def test_next_plan_question_mock_sequence_then_exhausts(client):
    seen = []
    for i in range(4):
        result = client.next_plan_question({}, {}, i)
        seen.append(result["question"])
    assert seen[-1] is None
    assert all(q is not None for q in seen[:-1])
    assert len({q["id"] for q in seen[:-1]}) == len(seen) - 1  # no repeats


def test_generate_plan_mock_sums_to_100(client):
    result = client.generate_plan({}, {})
    allocations = result["allocations"]
    total = sum(float(a["percentage"]) for a in allocations)
    assert total == 100.0
    assert {a["category"] for a in allocations} <= {
        "housing",
        "food",
        "transport",
        "savings",
        "lifestyle",
        "other",
    }


# ============================================================================
# Real branch — request building / response parsing, no network
# ============================================================================


class _FakeResponse:
    def __init__(self, json_data=None, lines=None, status_code=200, json_raises=False):
        self._json_data = json_data
        self._lines = lines or []
        self.status_code = status_code
        self._json_raises = json_raises
        self.close_calls = 0

    def raise_for_status(self):
        if self.status_code >= 400:
            # requests' real raise_for_status() sets .response on the error
            # it raises — _describe() in services/ai_service/ai_client.py
            # relies on that to recover the ai-service's own {"detail": ...}
            # body.
            error = requests.exceptions.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self):
        if self._json_raises:
            raise json.JSONDecodeError("Expecting value", "", 0)
        return self._json_data

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        self.close_calls += 1


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, json, headers, timeout, stream=False):
        self.calls.append(
            {
                "method": "POST",
                "url": url,
                "json": json,
                "headers": headers,
                "stream": stream,
                "timeout": timeout,
            }
        )
        return self._response

    def get(self, url, headers, timeout):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout})
        return self._response


@pytest.fixture
def real_mode(settings):
    settings.USE_MOCK_AI_SERVICE = False
    settings.AI_SERVICE_URL = "http://fake-ai-service:8001"
    settings.AI_SERVICE_TOKEN = "test-token"


@pytest.fixture
def real_client(real_mode):
    return AIServiceClient()


def test_process_statement_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"prefix": "x/", "ocr_engine": "MinerU", "confidence_score": 1.0})
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.process_statement("stmt-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/process"
    assert fake.calls[0]["json"] == {"statement_id": "stmt-1"}
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert fake.calls[0]["timeout"] == _SYNC_INGESTION_TIMEOUT_SECONDS
    assert result["prefix"] == "x/"


def test_normalize_statement_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"normalized_json": {"transactions": []}, "model_used": "gpt"})
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.normalize_statement("ocr-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/normalize"
    assert fake.calls[0]["json"] == {"ocr_result_id": "ocr-1"}
    assert fake.calls[0]["timeout"] == _SYNC_INGESTION_TIMEOUT_SECONDS
    assert result["model_used"] == "gpt"


def test_submit_process_job_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            {"job_id": "job-1", "step": "process", "state": "queued", "submitted_at": "x"}
        )
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.submit_process_job("stmt-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/jobs/process"
    assert fake.calls[0]["json"] == {"statement_id": "stmt-1"}
    assert fake.calls[0]["timeout"] == _TIMEOUT_SECONDS
    assert result["job_id"] == "job-1"


def test_submit_normalize_job_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            {"job_id": "job-2", "step": "normalize", "state": "queued", "submitted_at": "x"}
        )
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.submit_normalize_job("ocr-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/jobs/normalize"
    assert fake.calls[0]["json"] == {"ocr_result_id": "ocr-1"}
    assert fake.calls[0]["timeout"] == _TIMEOUT_SECONDS
    assert result["job_id"] == "job-2"


def test_get_job_status_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            {
                "job_id": "job-1",
                "function": "ingestion.process",
                "state": "succeeded",
                "submitted_at": "x",
                "started_at": "x",
                "finished_at": "x",
                "result": {"prefix": "x/", "ocr_engine": "MinerU", "confidence_score": 1.0},
                "error": None,
            }
        )
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.get_job_status("job-1")

    assert fake.calls[0]["method"] == "GET"
    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/tasks/job-1"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert fake.calls[0]["timeout"] == _TIMEOUT_SECONDS
    assert result["state"] == "succeeded"


def test_get_job_status_real_404_surfaces_detail(real_client, monkeypatch):
    fake = _FakeSession(_FakeResponse(json_data={"detail": "job not found"}, status_code=404))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError, match="job not found"):
        real_client.get_job_status("unknown-job")


def test_match_recommendations_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"matches": [{"product_id": "p1", "product_name": "X", "similarity": 0.9}]})
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.match_recommendations("user-1", "savings", top_k=3)

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/recommendations/match"
    assert fake.calls[0]["json"] == {"user_id": "user-1", "query": "savings", "top_k": 3}
    assert result["matches"][0]["product_id"] == "p1"


def test_stream_chat_real_parses_sse_envelope(real_client, monkeypatch):
    done_payload = {
        "content": "Hi there",
        "widget": {"type": None, "payload": None},
        "references": [],
    }
    lines = [
        'data: {"event": "token", "data": "Hi "}',
        'data: {"event": "token", "data": "there"}',
        f'data: {json.dumps({"event": "done", "data": done_payload})}',
    ]
    fake = _FakeSession(_FakeResponse(lines=lines))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    envelopes = list(real_client.stream_chat("conv-1", "user-1", "hi"))

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/chat"
    assert fake.calls[0]["json"] == {
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "message": "hi",
    }
    assert fake.calls[0]["stream"] is True
    assert [e["event"] for e in envelopes] == ["token", "token", "done"]
    assert envelopes[-1]["data"]["content"] == "Hi there"


def test_real_call_raises_ai_service_error_on_http_failure(real_client, monkeypatch):
    fake = _FakeSession(_FakeResponse(status_code=500))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError):
        real_client.process_statement("stmt-1")


def test_real_call_error_surfaces_ai_service_detail_body(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            json_data={"detail": "failed to retrieve source document: NoSuchKey"},
            status_code=502,
        )
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError, match="failed to retrieve source document"):
        real_client.process_statement("stmt-1")


def test_post_closes_response_on_http_failure(real_client, monkeypatch):
    response = _FakeResponse(status_code=500)
    fake = _FakeSession(response)
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError):
        real_client.process_statement("stmt-1")

    assert response.close_calls == 1


@pytest.mark.parametrize(
    "real_call",
    [
        lambda c: c.process_statement("stmt-1"),
        lambda c: c.normalize_statement("ocr-1"),
        lambda c: c.submit_process_job("stmt-1"),
        lambda c: c.get_job_status("job-1"),
        lambda c: c.match_recommendations("user-1", "savings"),
    ],
)
def test_real_call_raises_ai_service_error_on_malformed_json(real_client, monkeypatch, real_call):
    fake = _FakeSession(_FakeResponse(json_raises=True))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError, match="not valid JSON"):
        real_call(real_client)


def test_real_stream_chat_raises_when_stream_ends_without_terminal_event(real_client, monkeypatch):
    lines = ['data: {"event": "token", "data": "Hi "}']
    response = _FakeResponse(lines=lines)
    fake = _FakeSession(response)
    monkeypatch.setattr(ai_client_module, "_session", fake)

    with pytest.raises(ai_service.AIServiceError, match="without a terminal event"):
        list(real_client.stream_chat("conv-1", "user-1", "hi"))

    assert response.close_calls == 1


def test_embed_transactions_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"results": [{"transaction_id": "t1", "status": "embedded"}]})
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.embed_transactions(["t1"])

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/transactions/embed"
    assert fake.calls[0]["json"] == {"transaction_ids": ["t1"]}
    assert result["results"][0]["status"] == "embedded"


def test_create_embeddings_real_calls_correct_endpoint_with_dimensions(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            {
                "object": "list",
                "data": [{"object": "embedding", "embedding": [0.1], "index": 0}],
                "model": "real-embedder",
            }
        )
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.create_embeddings(["hello"], dimensions=768)

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/embeddings"
    assert fake.calls[0]["json"] == {"input": ["hello"], "dimensions": 768}
    assert result["model"] == "real-embedder"


def test_create_embeddings_real_omits_dimensions_when_not_given(real_client, monkeypatch):
    fake = _FakeSession(_FakeResponse({"object": "list", "data": [], "model": "x"}))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    real_client.create_embeddings(["hello"])

    assert fake.calls[0]["json"] == {"input": ["hello"]}


def test_run_post_ingestion_analysis_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(_FakeResponse({"summary": None, "recurring_charges": [], "anomalies": []}))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.run_post_ingestion_analysis("user-1", "acct-1", "2026-06")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/analyze/post-ingestion"
    assert fake.calls[0]["json"] == {
        "user_id": "user-1",
        "account_id": "acct-1",
        "month": "2026-06",
    }
    assert result == {"summary": None, "recurring_charges": [], "anomalies": []}


def test_next_plan_question_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(_FakeResponse({"question": {"id": "housing_cost", "text": "?"}}))
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.next_plan_question({"monthly_income": 100}, {}, 0)

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/plan/question"
    assert fake.calls[0]["json"] == {
        "user_context": {"monthly_income": 100},
        "answers": {},
        "questions_asked": 0,
    }
    assert result["question"]["id"] == "housing_cost"


def test_generate_plan_real_calls_correct_endpoint(real_client, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"allocations": [{"category": "housing", "percentage": "30.0"}]})
    )
    monkeypatch.setattr(ai_client_module, "_session", fake)

    result = real_client.generate_plan({"monthly_income": 100}, {"housing_cost": 3000})

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/plan/generate"
    assert fake.calls[0]["json"] == {
        "user_context": {"monthly_income": 100},
        "answers": {"housing_cost": 3000},
    }
    assert result["allocations"][0]["category"] == "housing"
