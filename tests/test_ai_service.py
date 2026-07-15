"""
Unit tests for services/ai_service.py's mock/real dispatch. Two concerns:

1. The mock branch (default, USE_MOCK_AI_SERVICE=True) returns exactly the
   shape the real /internal/... endpoint uses — this is what lets the rest of
   the app (and its tests) treat the two branches as interchangeable.
2. The real branch (USE_MOCK_AI_SERVICE=False) builds the right request and
   parses the right response, without ever hitting the network — the
   module-level requests.Session is monkeypatched, same convention
   tests/conftest.py uses for fake_redis/moto_storage.
"""

import json

import pytest
import requests

from core.models import (
    BankAccount,
    Budget,
    BudgetAllocation,
    Product,
    StatementFile,
    StatementOcrResult,
    Transaction,
    User,
)
from services import ai_service


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


# ============================================================================
# Mock branch — shape must match the real contract
# ============================================================================


def test_process_statement_mock_shape(user):
    result = ai_service.process_statement("some-statement-id")
    assert set(result) == {"prefix", "ocr_engine", "confidence_score"}
    assert isinstance(result["prefix"], str)


def test_normalize_statement_mock_matches_real_transaction_shape(ocr_result):
    result = ai_service.normalize_statement(str(ocr_result.id))
    assert set(result) == {"normalized_json", "model_used"}

    normalized = result["normalized_json"]
    assert set(normalized) == {"bank_name", "account_hint", "transactions"}
    assert len(normalized["transactions"]) == 3
    for txn in normalized["transactions"]:
        assert set(txn) == {
            "transaction_date",
            "merchant_raw",
            "ai_description",
            "category",
            "amount",
            "transaction_type",
            "duplicate_of",
        }
        assert txn["category"] in {"housing", "food", "transport", "savings", "lifestyle", "other"}


def test_normalize_statement_flags_duplicate_within_window(ocr_result):
    statement = ocr_result.statement
    # Deterministic (seeded off statement.id) — the same call twice produces
    # the same fabricated transactions, so the first call's output tells us
    # exactly what to pre-create as a "real" duplicate before the second call.
    first = ai_service.normalize_statement(str(ocr_result.id))
    txn = first["normalized_json"]["transactions"][0]
    assert txn["duplicate_of"] is None

    account = BankAccount.objects.create(
        user=statement.user, bank_name="Test Bank", masked_account_number="1234"
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

    second = ai_service.normalize_statement(str(ocr_result.id))
    matched = second["normalized_json"]["transactions"][0]
    assert matched["duplicate_of"] is not None


def test_stream_chat_mock_yields_token_events_then_one_done_event(user):
    envelopes = list(ai_service.stream_chat(str(user.id), str(user.id), "hello there"))
    assert [e["event"] for e in envelopes[:-1]] == ["token"] * len(envelopes[:-1])
    assert envelopes[-1]["event"] == "done"
    done_data = envelopes[-1]["data"]
    assert set(done_data) == {"content", "widget", "references"}


def test_stream_chat_mock_budget_keyword_produces_allocation_widget(user):
    budget = Budget.objects.create(user=user)
    BudgetAllocation.objects.create(
        budget=budget, category="housing", allocated_percentage="30.00", allocated_amount="3000.00"
    )

    envelopes = list(ai_service.stream_chat(str(user.id), str(user.id), "show me my budget"))
    done_data = envelopes[-1]["data"]
    assert done_data["widget"]["type"] == "allocation_slider"
    assert done_data["references"] == [{"target_type": "budget", "target_id": str(budget.id)}]


def test_match_recommendations_mock_shape(user):
    Product.objects.create(
        title="Premium Savings Account", description="low-fee savings", is_active=True
    )

    result = ai_service.match_recommendations(str(user.id), "savings")
    assert set(result) == {"matches"}
    for match in result["matches"]:
        assert set(match) == {"product_id", "product_name", "similarity"}


# ============================================================================
# Real branch — request building / response parsing, no network
# ============================================================================


class _FakeResponse:
    def __init__(self, json_data=None, lines=None, status_code=200):
        self._json_data = json_data
        self._lines = lines or []
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            # requests' real raise_for_status() sets .response on the error
            # it raises — _describe() in services/ai_service.py relies on
            # that to recover the ai-service's own {"detail": ...} body.
            error = requests.exceptions.HTTPError(f"{self.status_code} error")
            error.response = self
            raise error

    def json(self):
        return self._json_data

    def iter_lines(self, decode_unicode=True):
        return iter(self._lines)

    def close(self):
        pass


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def post(self, url, json, headers, timeout, stream=False):
        self.calls.append({"url": url, "json": json, "headers": headers, "stream": stream})
        return self._response


@pytest.fixture
def real_mode(settings):
    settings.USE_MOCK_AI_SERVICE = False
    settings.AI_SERVICE_URL = "http://fake-ai-service:8001"
    settings.AI_SERVICE_TOKEN = "test-token"


def test_process_statement_real_calls_correct_endpoint(real_mode, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"prefix": "x/", "ocr_engine": "MinerU", "confidence_score": 1.0})
    )
    monkeypatch.setattr(ai_service, "_session", fake)

    result = ai_service.process_statement("stmt-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/process"
    assert fake.calls[0]["json"] == {"statement_id": "stmt-1"}
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert result["prefix"] == "x/"


def test_normalize_statement_real_calls_correct_endpoint(real_mode, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"normalized_json": {"transactions": []}, "model_used": "gpt"})
    )
    monkeypatch.setattr(ai_service, "_session", fake)

    result = ai_service.normalize_statement("ocr-1")

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/ingestion/normalize"
    assert fake.calls[0]["json"] == {"ocr_result_id": "ocr-1"}
    assert result["model_used"] == "gpt"


def test_match_recommendations_real_calls_correct_endpoint(real_mode, monkeypatch):
    fake = _FakeSession(
        _FakeResponse({"matches": [{"product_id": "p1", "product_name": "X", "similarity": 0.9}]})
    )
    monkeypatch.setattr(ai_service, "_session", fake)

    result = ai_service.match_recommendations("user-1", "savings", top_k=3)

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/recommendations/match"
    assert fake.calls[0]["json"] == {"user_id": "user-1", "query": "savings", "top_k": 3}
    assert result["matches"][0]["product_id"] == "p1"


def test_stream_chat_real_parses_sse_envelope(real_mode, monkeypatch):
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
    monkeypatch.setattr(ai_service, "_session", fake)

    envelopes = list(ai_service.stream_chat("conv-1", "user-1", "hi"))

    assert fake.calls[0]["url"] == "http://fake-ai-service:8001/internal/chat"
    assert fake.calls[0]["json"] == {
        "conversation_id": "conv-1",
        "user_id": "user-1",
        "message": "hi",
    }
    assert fake.calls[0]["stream"] is True
    assert [e["event"] for e in envelopes] == ["token", "token", "done"]
    assert envelopes[-1]["data"]["content"] == "Hi there"


def test_real_call_raises_ai_service_error_on_http_failure(real_mode, monkeypatch):
    fake = _FakeSession(_FakeResponse(status_code=500))
    monkeypatch.setattr(ai_service, "_session", fake)

    with pytest.raises(ai_service.AIServiceError):
        ai_service.process_statement("stmt-1")


def test_real_call_error_surfaces_ai_service_detail_body(real_mode, monkeypatch):
    fake = _FakeSession(
        _FakeResponse(
            json_data={"detail": "failed to retrieve source document: NoSuchKey"},
            status_code=502,
        )
    )
    monkeypatch.setattr(ai_service, "_session", fake)

    with pytest.raises(ai_service.AIServiceError, match="failed to retrieve source document"):
        ai_service.process_statement("stmt-1")
