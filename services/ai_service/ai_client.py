"""
AIServiceClient — the real HTTP implementation of BaseAIServiceClient,
talking to the AI Service (FastAPI, docs/System_Architecture.md §2). Django is
the only caller of it (§3), and this module is the one place that boundary is
crossed for real requests — services/ai_service/mock_client.py is the
in-process stand-in used whenever settings.USE_MOCK_AI_SERVICE is True.

Follows services/bank_connectors/mock_bank.py's shape: a module-level
requests.Session() singleton and free helper functions the class methods
call, rather than instance methods for the HTTP plumbing itself.
"""

import json

import requests
from django.conf import settings

from .base import AIServiceError, BaseAIServiceClient

# Module-level singleton, matching services/event_bus.py's/file_storage.py's
# convention — this is what tests monkeypatch in place of a live network call.
_session = requests.Session()

# Two-tier timeouts: (connect_seconds, read_seconds).
#
# process_statement()/normalize_statement() hit the ai-service's still-genuinely-
# slow blocking endpoints, which must cover the entire pipeline in one call:
#   - MinerU OCR: up to 600 s (ai-service's own mineru_client timeout)
#   - LLM normalization: multiple chunks, each taking up to 600 s on a slow/queued
#     free-tier model
#   - Processing overhead: buffer on top
# 3600 s gives comfortable headroom for large PDF statements in sequence.
_SYNC_INGESTION_TIMEOUT_SECONDS = (10, 3600)

# Every other call (the async submit/status endpoints, chat, embeddings,
# recommendations, plan questionnaire, post-ingestion analysis) is fast by
# design — the submit endpoints return as soon as a job is queued, and status
# reads/other endpoints do no heavy lifting server-side.
_TIMEOUT_SECONDS = (10, 60)


def _auth_headers():
    """Bearer header built from settings at call time (not import time) —
    see the module docstring for why."""
    return {"Authorization": f"Bearer {settings.AI_SERVICE_TOKEN}"}


def _post(path: str, json_body: dict, *, stream: bool = False, timeout=_TIMEOUT_SECONDS):
    """Shared real-HTTP-call helper — builds the URL/auth from settings at
    call time, applies a timeout, and normalizes any failure into
    AIServiceError so caller code has one thing to catch."""
    resp = None
    try:
        resp = _session.post(
            f"{settings.AI_SERVICE_URL}{path}",
            json=json_body,
            headers=_auth_headers(),
            timeout=timeout,
            stream=stream,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # resp exists (and may hold an open connection, e.g. stream=True)
        # whenever the failure is a bad status rather than a connection-level
        # error, where _session.post() itself raised before assigning it —
        # close it either way rather than leaking the connection.
        if resp is not None:
            resp.close()
        raise AIServiceError(f"AI service call to {path} failed: {_describe(exc)}") from exc
    return resp


def _get(path: str, *, timeout=_TIMEOUT_SECONDS):
    """GET counterpart to _post() — same auth/timeout/error-wrapping shape."""
    resp = None
    try:
        resp = _session.get(
            f"{settings.AI_SERVICE_URL}{path}",
            headers=_auth_headers(),
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        if resp is not None:
            resp.close()
        raise AIServiceError(f"AI service call to {path} failed: {_describe(exc)}") from exc
    return resp


def _parse_json(resp, path: str) -> dict:
    """resp.json() wrapped so a malformed or empty successful response
    becomes an AIServiceError too, not a raw requests/json decode error —
    otherwise callers that only catch AIServiceError (e.g. RecommendationsView)
    would still see an unhandled exception on a broken response body."""
    try:
        return resp.json()
    except ValueError as exc:
        raise AIServiceError(f"AI service response from {path} was not valid JSON: {exc}") from exc


def _describe(exc: requests.exceptions.RequestException) -> str:
    """Surfaces the ai-service's own error detail (FastAPI's {"detail": ...}
    error body) when available, instead of just requests' bare status line —
    otherwise the real reason (e.g. "failed to retrieve source document: ...")
    is silently discarded and failure_reason ends up saying only "502 Bad
    Gateway", which isn't actionable."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    return f"{exc} — {detail}" if detail else str(exc)


class AIServiceClient(BaseAIServiceClient):
    def process_statement(self, statement_id: str) -> dict:
        resp = _post(
            "/internal/ingestion/process",
            {"statement_id": statement_id},
            timeout=_SYNC_INGESTION_TIMEOUT_SECONDS,
        )
        return _parse_json(resp, "/internal/ingestion/process")

    def normalize_statement(self, ocr_result_id: str) -> dict:
        resp = _post(
            "/internal/ingestion/normalize",
            {"ocr_result_id": ocr_result_id},
            timeout=_SYNC_INGESTION_TIMEOUT_SECONDS,
        )
        return _parse_json(resp, "/internal/ingestion/normalize")

    def submit_process_job(self, statement_id: str) -> dict:
        resp = _post("/internal/ingestion/jobs/process", {"statement_id": statement_id})
        return _parse_json(resp, "/internal/ingestion/jobs/process")

    def submit_normalize_job(self, ocr_result_id: str) -> dict:
        resp = _post("/internal/ingestion/jobs/normalize", {"ocr_result_id": ocr_result_id})
        return _parse_json(resp, "/internal/ingestion/jobs/normalize")

    def get_job_status(self, job_id: str) -> dict:
        resp = _get(f"/internal/tasks/{job_id}")
        return _parse_json(resp, f"/internal/tasks/{job_id}")

    def stream_chat(self, conversation_id: str, user_id: str, message: str):
        """Parses the raw SSE wire format (each frame is one
        `data: {json}\\n\\n` line) and yields the same envelope shape
        MockAIServiceClient.stream_chat() produces."""
        resp = _post(
            "/internal/chat",
            {"conversation_id": conversation_id, "user_id": user_id, "message": message},
            stream=True,
        )
        terminal_event_seen = False
        try:
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                envelope = json.loads(line[len("data: ") :])
                yield envelope
                if envelope.get("event") in ("done", "error"):
                    terminal_event_seen = True
                    return
        except (requests.exceptions.RequestException, json.JSONDecodeError) as exc:
            raise AIServiceError(f"AI service chat stream failed: {exc}") from exc
        finally:
            resp.close()
        if not terminal_event_seen:
            # The stream closed cleanly but never sent a done/error frame (e.g.
            # the ai-service crashed mid-stream) — without this, the caller's
            # loop just ends with no result, per generate_chat_reply's own
            # matching fix for the "no terminal event" case.
            raise AIServiceError("AI service chat stream ended without a terminal event")

    def match_recommendations(self, user_id: str, query: str, top_k: int = 3) -> dict:
        resp = _post(
            "/internal/recommendations/match",
            {"user_id": user_id, "query": query, "top_k": top_k},
        )
        return _parse_json(resp, "/internal/recommendations/match")

    def embed_transactions(self, transaction_ids: list[str]) -> dict:
        resp = _post("/internal/transactions/embed", {"transaction_ids": transaction_ids})
        return _parse_json(resp, "/internal/transactions/embed")

    def create_embeddings(self, texts: list[str], dimensions: int | None = None) -> dict:
        body = {"input": texts}
        if dimensions is not None:
            body["dimensions"] = dimensions
        resp = _post("/internal/embeddings", body)
        return _parse_json(resp, "/internal/embeddings")

    def run_post_ingestion_analysis(self, user_id: str, account_id: str, month: str) -> dict:
        resp = _post(
            "/internal/analyze/post-ingestion",
            {"user_id": user_id, "account_id": account_id, "month": month},
        )
        return _parse_json(resp, "/internal/analyze/post-ingestion")

    def next_plan_question(self, user_context: dict, answers: dict, questions_asked: int) -> dict:
        resp = _post(
            "/internal/plan/question",
            {"user_context": user_context, "answers": answers, "questions_asked": questions_asked},
        )
        return _parse_json(resp, "/internal/plan/question")

    def generate_plan(self, user_context: dict, answers: dict) -> dict:
        resp = _post("/internal/plan/generate", {"user_context": user_context, "answers": answers})
        return _parse_json(resp, "/internal/plan/generate")
