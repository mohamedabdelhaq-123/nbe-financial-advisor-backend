"""
BaseAIServiceClient — the contract every AI-service client implementation
(real HTTP, in-process mock) must satisfy, mirroring
services/bank_connectors/base.py's BankConnector(ABC): a pure interface with
no logic of its own, so callers never know or care which concrete
implementation (services/ai_service/ai_client.py's AIServiceClient or
services/ai_service/mock_client.py's MockAIServiceClient) actually ran —
services/ai_service/__init__.py's get_client() picks one based on
settings.USE_MOCK_AI_SERVICE.

Covers every /internal/... surface the ai-service exposes, not just the ones
with a real Django caller today — process_statement()/normalize_statement()
mirror the ai-service's still-existing blocking sync endpoints even though the
statements pipeline (core/tasks/statements.py) now drives ingestion through
submit_process_job()/submit_normalize_job()/get_job_status() instead.
"""

from abc import ABC, abstractmethod


class AIServiceError(Exception):
    """Raised for any AI-service request/timeout/HTTP-status failure —
    callers catch this one type instead of requests' own exception hierarchy."""


class BaseAIServiceClient(ABC):
    # ========================================================================
    # Statement ingestion (sync) — POST /internal/ingestion/process, .../normalize
    #
    # Kept for API-surface parity with the ai-service's still-existing
    # blocking endpoints — not called by the statements pipeline anymore (see
    # the async job methods below), but a legitimate direct call if anything
    # ever needs a synchronous result again.
    # ========================================================================

    @abstractmethod
    def process_statement(self, statement_id: str) -> dict:
        """
        Phase 1/2 of the ingestion pipeline (MinerU/OCR). Returns
        {"prefix", "ocr_engine", "confidence_score"} either way.
        """

    @abstractmethod
    def normalize_statement(self, ocr_result_id: str) -> dict:
        """
        Phase 2/2 (Normalization Agent). Returns {"normalized_json", "model_used"}
        either way — normalized_json = {"bank_name", "account_number", "transactions": [...],
        "extra_fields"?}, each transaction = {"transaction_date", "merchant_raw",
        "merchant_normalized", "ai_description", "category", "amount",
        "transaction_type", "balance", "duplicate_of", "extra_fields"?}.
        account_number is the real, unmasked account number as printed in the
        source (null if not determinable) — not a masked hint. balance/
        merchant_normalized are dedicated keys, always present but null when not
        determinable. extra_fields (both levels) is a plain key-value object,
        present only when non-empty. duplicate_of is computed here (both mock and
        real), not by the caller — the real AI service resolves it itself via its
        read-only Django DB connection, so the mock mirrors that instead of
        leaving it for the caller to re-derive.
        """

    # ========================================================================
    # Statement ingestion (async) — POST /internal/ingestion/jobs/process,
    # .../jobs/normalize, GET /internal/tasks/{job_id}
    #
    # What core/tasks/statements.py's process_statement_pipeline actually
    # drives: submit returns a job reference immediately, the caller polls
    # get_job_status() until it reaches a terminal state.
    # ========================================================================

    @abstractmethod
    def submit_process_job(self, statement_id: str) -> dict:
        """Returns {"job_id", "step", "state", "submitted_at"}. Resubmitting
        while a job is already in flight for the same statement returns that
        job's existing job_id/state rather than creating a duplicate."""

    @abstractmethod
    def submit_normalize_job(self, ocr_result_id: str) -> dict:
        """Returns {"job_id", "step", "state", "submitted_at"}. Same
        in-flight-dedup behavior as submit_process_job()."""

    @abstractmethod
    def get_job_status(self, job_id: str) -> dict:
        """Returns {"job_id", "function", "state", "submitted_at", "started_at",
        "finished_at", "result", "error"}. state is one of "queued", "running",
        "succeeded", "failed". result (on success) matches
        process_statement()/normalize_statement()'s return shapes exactly;
        error (on failure) is a human-readable message."""

    # ========================================================================
    # Chat — POST /internal/chat (SSE stream)
    # ========================================================================

    @abstractmethod
    def stream_chat(self, conversation_id: str, user_id: str, message: str):
        """
        Yields the shared {"event", "data"} envelope: zero or more
        {"event": "token", "data": <str>} chunks, then exactly one terminal
        {"event": "done", "data": {"content", "widget", "references", "suggestions"}}
        or {"event": "error", "data": {"message"}}.
        """

    # ========================================================================
    # Recommendations — POST /internal/recommendations/match
    # ========================================================================

    @abstractmethod
    def match_recommendations(self, user_id: str, query: str, top_k: int = 3) -> dict:
        """Returns {"matches": [{"product_id", "product_name", "similarity"}]}."""

    # ========================================================================
    # Transaction embedding — POST /internal/transactions/embed
    # ========================================================================

    @abstractmethod
    def embed_transactions(self, transaction_ids: list[str]) -> dict:
        """Returns {"results": [{"transaction_id", "status"}]}."""

    # ========================================================================
    # Generic embeddings — POST /internal/embeddings
    # ========================================================================

    @abstractmethod
    def create_embeddings(self, texts: list[str], dimensions: int | None = None) -> dict:
        """Returns {"data": [{"embedding", "index"}], "model"}."""

    # ========================================================================
    # Analytics — POST /internal/analyze/post-ingestion
    # ========================================================================

    @abstractmethod
    def run_post_ingestion_analysis(self, user_id: str, account_id: str, month: str) -> dict:
        """Returns {"summary", "recurring_charges", "anomalies"}."""

    # ========================================================================
    # Plan questionnaire — POST /internal/plan/question, .../generate
    # ========================================================================

    @abstractmethod
    def next_plan_question(self, user_context: dict, answers: dict, questions_asked: int) -> dict:
        """Returns {"question": {"id", "text"} | None}."""

    @abstractmethod
    def generate_plan(self, user_context: dict, answers: dict) -> dict:
        """Returns {"allocations": [{"category", "percentage"}]}."""
