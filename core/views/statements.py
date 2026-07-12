import uuid
from datetime import date
from decimal import Decimal

from django.db import transaction as db_transaction
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import BusinessRuleError
from core.filters.statements import StatementFileFilterSet
from core.models import (
    BankAccount,
    StatementFile,
    StatementNormalized,
    StatementOcrResult,
    Transaction,
    UserPreference,
)
from core.openapi import error_responses
from core.serializers.statements import (
    StatementDetailSerializer,
    StatementFileSerializer,
    StatementOcrResultResponseSerializer,
    StatementPatchSerializer,
    StatementUploadRequestSerializer,
    TransactionApprovalRequestSerializer,
    TransactionApprovalResponseSerializer,
)
from services import ai_service, file_storage


def _run_extraction(statement: StatementFile) -> None:
    """
    Phase 1/2 of the ingestion pipeline (MinerU/OCR), synchronous today for
    the same reason noted on _run_normalization() below. On failure, status
    is left at uploaded and failure_reason/failed_phase record why —
    PATCH /statements/{id} is the only way to retry this phase (PLAN.md).
    """
    statement.is_processing = True
    statement.save(update_fields=["is_processing"])

    try:
        result = ai_service.normalize(statement)
    except Exception as exc:
        statement.failure_reason = str(exc)
        statement.failed_phase = StatementFile.PHASE_EXTRACTION
        statement.is_processing = False
        statement.save(update_fields=["failure_reason", "failed_phase", "is_processing"])
        return

    StatementOcrResult.objects.create(
        statement=statement,
        seaweed_file_id=file_storage.ocr_artifact_key(statement.user_id, statement.id),
        ocr_engine=result["ocr"]["engine"],
        confidence_score=Decimal(str(result["ocr"]["confidence_score"])),
    )

    statement.status = StatementFile.STATUS_EXTRACTED
    statement.failure_reason = None
    statement.failed_phase = None
    statement.is_processing = False
    statement.save(update_fields=["status", "failure_reason", "failed_phase", "is_processing"])


def _run_normalization(statement: StatementFile) -> None:
    """
    Phase 2/2 (Normalization Agent). Resolves bank properties and writes the
    proposed transaction array to normalized_json for the user to review —
    nothing is written to the ledger here anymore; that only happens via
    POST /statements/{id}/transactions once the user approves the whole
    batch (PLAN.md). duplicate_of is still computed here for display, but is
    re-checked at approval time rather than trusted, since time may have
    passed since this ran.

    Both this and _run_extraction() call ai_service.normalize() independently
    rather than sharing one result — the mock bundles OCR + LLM output in one
    deterministic, seeded-by-statement-id call (see its docstring), so a
    second call for the same statement reproduces the same data. This mirrors
    the two separate calls a real integration would make (Pipeline.md §2:
    MinerU and the Normalization Agent are distinct steps) without requiring
    state to be threaded between two separate HTTP requests.
    """
    statement.is_processing = True
    statement.save(update_fields=["is_processing"])

    try:
        result = ai_service.normalize(statement)
    except Exception as exc:
        statement.failure_reason = str(exc)
        statement.failed_phase = StatementFile.PHASE_NORMALIZATION
        statement.is_processing = False
        statement.save(update_fields=["failure_reason", "failed_phase", "is_processing"])
        return

    normalized = result["normalized"]

    if statement.account is None:
        # System_Architecture.md §5: "Normalization Agent maps columns...".
        # When the client didn't supply account_id upfront, the Normalization
        # Agent may resolve or create one — mocked here as a get_or_create
        # keyed on the (mock) bank_name + account_hint the AI service "found".
        statement.account, _ = BankAccount.objects.get_or_create(
            user=statement.user,
            bank_name=normalized["bank_name"],
            masked_account_number=normalized["account_hint"],
        )

    transaction_dates = []
    for txn in normalized["transactions"]:
        transaction_date = date.fromisoformat(txn["transaction_date"])
        amount = Decimal(str(txn["amount"]))
        # Preview-only duplicate check (System_Architecture.md §8) — informs
        # the user before they approve; POST /statements/{id}/transactions
        # re-runs this same lookup at commit time rather than trusting it.
        existing = Transaction.objects.filter(
            user=statement.user,
            account=statement.account,
            transaction_date=transaction_date,
            amount=amount,
            merchant_raw=txn["merchant_raw"],
        ).first()
        txn["duplicate_of"] = str(existing.id) if existing is not None else None
        transaction_dates.append(transaction_date)

    StatementNormalized.objects.create(
        statement=statement,
        normalized_json=normalized,
        model_used=result["model_used"],
    )

    statement.status = StatementFile.STATUS_NORMALIZED
    statement.failure_reason = None
    statement.failed_phase = None
    statement.is_processing = False
    if transaction_dates:
        statement.start_transaction_date = min(transaction_dates)
        statement.last_transaction_date = max(transaction_dates)
    statement.save(
        update_fields=[
            "account",
            "status",
            "failure_reason",
            "failed_phase",
            "is_processing",
            "start_transaction_date",
            "last_transaction_date",
        ]
    )


# Ordered so a target status's index can be compared against the current
# status's index to tell "forward" from "backward/same" (PATCH validation
# below) and to drive the retry cascade.
_STATUS_ORDER = [
    StatementFile.STATUS_UPLOADED,
    StatementFile.STATUS_EXTRACTED,
    StatementFile.STATUS_NORMALIZED,
    StatementFile.STATUS_APPROVED,
]

# Which phase function runs *from* a given status. STATUS_NORMALIZED and
# STATUS_APPROVED have no entry — the former only ever advances via the
# transaction-approval endpoint, never PATCH; the latter is terminal.
_PHASE_RUNNERS = {
    StatementFile.STATUS_UPLOADED: _run_extraction,
    StatementFile.STATUS_EXTRACTED: _run_normalization,
}


def advance_statement_to(statement: StatementFile, target_status: str) -> None:
    """
    The single place that drives the pipeline forward toward `target_status`
    — both create_statement_from_upload()'s initial auto-chain and
    StatementDetailView.patch()'s retry go through this, so the same guards
    apply to both instead of being duplicated per call site.

    Raises BusinessRuleError if the statement is already approved
    (`already_approved`), a phase is already running on it
    (`already_processing` — guards against two overlapping callers, e.g. a
    double-clicked retry, re-running the same phase concurrently), or
    `target_status` isn't strictly ahead of the statement's current status
    (`invalid_status_transition`).

    Otherwise resumes one phase at a time, stopping once `target_status` is
    reached or a phase fails (leaving failure_reason/failed_phase set by
    that phase's runner — a mid-cascade failure returns normally rather
    than raising; it's a valid outcome, not a request error). Requesting a
    target further out than the next phase cascades through the
    intermediate ones in this same call.
    """
    if statement.status == StatementFile.STATUS_APPROVED:
        raise BusinessRuleError(
            "This statement has already been approved and cannot be retried.",
            code="already_approved",
        )
    if statement.is_processing:
        raise BusinessRuleError(
            "This statement is currently being processed; try again shortly.",
            code="already_processing",
        )
    target_rank = _STATUS_ORDER.index(target_status)
    if target_rank <= _STATUS_ORDER.index(statement.status):
        raise BusinessRuleError(
            "Target status must be ahead of the statement's current status.",
            code="invalid_status_transition",
        )

    while _STATUS_ORDER.index(statement.status) < target_rank:
        runner = _PHASE_RUNNERS.get(statement.status)
        if runner is None:
            break
        status_before = statement.status
        runner(statement)
        if statement.status == status_before:
            # The phase attempted and failed — its runner already recorded
            # failure_reason/failed_phase and left status where it was.
            break


def create_statement_from_upload(user, file_obj, target_status=None) -> StatementFile:
    """
    The full upload -> checksum-dedupe -> store -> auto-chained-pipeline
    flow, factored out of StatementListCreateView.post() so the
    Conversations domain's POST /chat/conversations/{id}/attachments can
    reuse it exactly — Data_Shapes_Conversations.md: "Shortcut into the
    Statements pipeline... same underlying processing as POST /statements".
    Raises the same ValidationError/BusinessRuleError/404 a direct upload
    would, so both call sites behave identically (API Design Guidelines §1:
    "one backend, one write path").

    A StatementFile row is only ever created once the file is successfully
    stored (PLAN.md) — if storage fails, this raises before anything is
    persisted, and there is nothing for the caller to retry; they re-submit
    a fresh upload. Once the row exists, the pipeline auto-chains toward
    `target_status` via advance_statement_to() — the same function
    StatementDetailView.patch() uses to retry, so a fresh upload and a
    retry drive the pipeline identically. Defaults to STATUS_NORMALIZED —
    the furthest point reachable by auto-chaining (STATUS_APPROVED is only
    reachable via the transaction-approval endpoint, never a status flag),
    and the original always-chain-to-the-end behavior — when the caller
    (e.g. the Conversations shortcut) doesn't pass one; POST /statements
    lets the client choose explicitly via StatementUploadRequestSerializer's
    optional `status` field. Stops wherever a phase fails — PATCH
    /statements/{id} is then the way to resume from there.
    """
    if not file_obj:
        raise ValidationError({"file": "This field is required."})

    file_bytes = file_obj.read()
    checksum = file_storage.compute_checksum(file_bytes)

    if StatementFile.objects.filter(user=user, checksum=checksum).exists():
        # File-level duplicate-upload check (DB_Schema.md's
        # UNIQUE(user_id, checksum)) — System_Architecture.md §8: "A
        # secondary file-level checksum check rejects a byte-identical
        # re-upload before OCR even runs."
        raise ValidationError(
            {"file": "This exact file has already been uploaded."},
            code="duplicate_statement",
        )

    # No account resolution here — the client never supplies one at upload
    # time (PLAN.md Checkpoint A). _run_normalization() infers/creates the
    # account from OCR output once extraction runs; the user confirms or
    # corrects it at approval time (StatementTransactionApprovalView).
    extension = file_obj.name.rsplit(".", 1)[-1].lower() if "." in file_obj.name else "bin"
    statement_id = uuid.uuid4()
    seaweed_file_id = file_storage.raw_statement_key(user.id, statement_id, extension)

    try:
        file_storage.store_raw_file(seaweed_file_id, file_bytes)
    except Exception as exc:
        raise BusinessRuleError(f"The file could not be stored: {exc}", code="storage_failed")

    statement = StatementFile.objects.create(
        id=statement_id,
        user=user,
        seaweed_file_id=seaweed_file_id,
        checksum=checksum,
        file_size=len(file_bytes),
        file_type=extension,
        status=StatementFile.STATUS_UPLOADED,
    )

    advance_statement_to(statement, target_status or StatementFile.STATUS_NORMALIZED)
    return statement


class StatementListCreateView(generics.ListAPIView):
    """
    List the current user's uploaded bank statements, or upload a new one.

    Uploading (multipart, `file` required) kicks off the ingestion pipeline
    immediately and returns `202 Accepted` — a statement moves through
    `uploaded -> extracted -> normalized -> approved` asynchronously from
    the caller's perspective (even though today's implementation happens to
    finish synchronously within the request). By default the upload
    auto-chains all the way to `normalized` (the furthest point reachable
    without the user's explicit approval); pass the optional `status` field
    (`extracted` or `normalized`) to stop the chain earlier. If the same
    exact file (by checksum) was already uploaded by this user, the upload
    is rejected as a duplicate rather than creating a second copy.

    Poll `GET /statements/{id}` while `is_processing` is `true` to track
    progress; `status`/`failure_reason`/`failed_phase` describe where the
    pipeline stopped and why if a phase fails.
    """

    serializer_class = StatementFileSerializer
    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = StatementFileFilterSet

    def get_queryset(self):
        # swagger_fake_view: see core/views/aggregations.py's
        # TransactionListCreateView.get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return StatementFile.objects.none()
        # select_related(account) + prefetch(normalized_records) keep the
        # newly-inlined metadata fields (bank_name/account_hint/model_used/
        # adjusted_at, all funnelling through latest_normalized_record) from
        # turning the list into an N+1 — see StatementFile.latest_normalized_record.
        return (
            StatementFile.objects.filter(user=self.request.user)
            .select_related("account")
            .prefetch_related("normalized_records")
            .order_by("-upload_date")
        )

    @extend_schema(
        request=StatementUploadRequestSerializer,
        responses={202: StatementDetailSerializer, **error_responses(422)},
    )
    def post(self, request, *args, **kwargs):
        upload = StatementUploadRequestSerializer(data=request.data)
        upload.is_valid(raise_exception=True)
        statement = create_statement_from_upload(
            request.user,
            upload.validated_data["file"],
            # Defaults to STATUS_NORMALIZED inside create_statement_from_upload
            # when omitted — the serializer's own `default` already resolves
            # that, but .get() here keeps this call site symmetrical with
            # the "no explicit target" contract other callers rely on.
            target_status=upload.validated_data.get("status"),
        )
        # Single-resource detail shape (not the lean list one above) — if the
        # auto-chain already reached normalized in this same call, the
        # proposed transactions come back here for free, no second GET needed.
        return Response(StatementDetailSerializer(statement).data, status=status.HTTP_202_ACCEPTED)


@extend_schema_view(
    get=extend_schema(responses={200: StatementDetailSerializer, **error_responses(404)}),
    delete=extend_schema(responses={204: None, **error_responses(404)}),
)
class StatementDetailView(generics.RetrieveDestroyAPIView):
    """
    Retrieve, delete, or advance a single statement.

    `status` is one of `uploaded | extracted | normalized | approved`,
    reflecting the last successfully completed pipeline phase (never a
    phase "in progress" — check `is_processing` for that). There is no
    `failed` status: a phase that fails leaves `status` at its last
    completed value and sets `failure_reason`/`failed_phase` instead,
    both cleared again on the next successful transition.

    The `transactions` field (only present once `status` is `normalized`
    or `approved`) is the proposed batch awaiting approval while
    `normalized`, and switches to the real committed ledger rows once
    `approved` — same field name, different underlying source, so a client
    never needs to know which stage produced what it's looking at.

    DELETE removes the statement and its stored file/artifacts (subject to
    the user's `retain_raw_documents` preference), but never touches
    transactions already committed to the ledger from this statement —
    those stay exactly as they are; only their link back to this statement
    is cleared.

    PATCH retries or resumes the pipeline toward a given `status` target —
    it never edits any other field. Requesting a target further out than
    the next phase cascades through the intermediate ones in the same
    call (e.g. retrying from `uploaded` straight to `normalized` runs both
    extraction and normalization before returning). Rejected with a 422
    and one of these `error.code` values if the request isn't a valid
    retry: `already_approved` (this statement is terminal), `already_processing`
    (a phase is already running on it — avoids a double-clicked retry
    re-running the same phase concurrently), or `invalid_status_transition`
    (the target isn't strictly ahead of the current status).
    """

    serializer_class = StatementDetailSerializer
    lookup_url_kwarg = "statement_id"

    def get_queryset(self):
        return StatementFile.objects.filter(user=self.request.user)

    @extend_schema(
        request=StatementPatchSerializer,
        responses={200: StatementDetailSerializer, **error_responses(404, 422)},
    )
    def patch(self, request, *args, **kwargs):
        # Retry/resume, never a general field update. Only the pipeline
        # phases, not the file upload, are retryable this way — a storage
        # failure never leaves a row to PATCH at all. All the
        # already_approved/already_processing/forward-only guards live in
        # advance_statement_to() — the same function POST /statements calls
        # for its own initial auto-chain, so both call sites enforce
        # identical rules instead of duplicating them here. Requesting a
        # target further out than the next phase cascades through the
        # intermediate ones in this same call (e.g. retrying from
        # `uploaded` straight to `normalized` runs both extraction and
        # normalization before returning).
        statement = self.get_object()
        serializer = StatementPatchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        advance_statement_to(statement, serializer.validated_data["status"])
        return Response(StatementDetailSerializer(statement).data)

    def perform_destroy(self, instance):
        # Removes the statement_files row and its raw/artifact files (subject
        # to retain_raw_documents — File_System_Structure.md §2-3). Does NOT
        # touch transactions already committed to the ledger from this
        # statement (Data_Shapes_Statements.md: transactions are the single
        # source of truth independent of their originating statement) — the
        # FK is ON DELETE SET NULL (DB_Schema.md), so that happens for free
        # at the DB level, no manual cleanup needed here.
        preferences, _ = UserPreference.objects.get_or_create(user=instance.user)
        if not preferences.retain_raw_documents:
            file_storage.delete_prefix(f"pfm-statements-raw/{instance.user_id}/{instance.id}/")
            # OCR and normalized artifacts are separate buckets, not one
            # shared "artifacts" bucket (PLAN.md's one-bucket-per-file-type
            # decision) — each needs its own delete_prefix call.
            file_storage.delete_prefix(f"pfm-statements-ocr/{instance.user_id}/{instance.id}/")
            file_storage.delete_prefix(
                f"pfm-statements-normalized/{instance.user_id}/{instance.id}/"
            )
        instance.delete()


class StatementOcrResultView(APIView):
    """Retrieve the raw OCR result for a statement — the engine used, its
    confidence score, when it ran, and a link to download the extracted
    document text. Returns 404 both when the statement itself doesn't
    exist (or isn't the current user's) and when OCR hasn't completed yet
    for it (`status` is still `uploaded`) — poll `GET /statements/{id}`
    until `status` reaches at least `extracted` before calling this."""

    @extend_schema(responses={200: StatementOcrResultResponseSerializer, **error_responses(404)})
    def get(self, request, statement_id):
        statement = get_object_or_404(StatementFile, id=statement_id, user=request.user)
        ocr = statement.ocr_results.order_by("-processed_at").first()
        if ocr is None:
            raise NotFound("OCR result not available yet.")
        return Response(
            {
                "statement_id": str(statement.id),
                "ocr_engine": ocr.ocr_engine,
                "confidence_score": ocr.confidence_score,
                "processed_at": ocr.processed_at,
                # A Django-proxied download URL, not a signed SeaweedFS URL —
                # SeaweedFS is never exposed publicly (System_Architecture.md
                # §2/§10), so a client couldn't resolve a direct link to it.
                "artifact_url": request.build_absolute_uri(
                    reverse("statement-ocr-artifact-download", args=[statement.id])
                ),
            }
        )


class StatementOcrArtifactDownloadView(APIView):
    """
    Download the OCR artifact's primary human-readable output
    (`document.md`) as a file attachment.

    Proxied through Django rather than a signed storage URL — the file
    storage backend is never exposed publicly, so there's no direct link a
    client could resolve on its own. The same underlying storage location
    also holds machine-oriented OCR output (raw JSON, extracted
    images/tables), but those are inputs to the normalization step, not
    something a user downloads directly — this endpoint only ever serves
    `document.md`. Returns 404 if the statement doesn't exist/isn't the
    current user's, or if OCR hasn't produced a document yet.
    """

    @extend_schema(responses={200: OpenApiTypes.BINARY, **error_responses(404)})
    def get(self, request, statement_id):
        statement = get_object_or_404(StatementFile, id=statement_id, user=request.user)
        ocr = statement.ocr_results.order_by("-processed_at").first()
        if ocr is None:
            raise NotFound("OCR result not available yet.")

        key = file_storage.ocr_artifact_key(statement.user_id, statement.id) + "document.md"
        stream = file_storage.get_object_stream(key)
        if stream is None:
            raise NotFound("OCR document not available yet.")
        body, content_type = stream
        return FileResponse(
            body,
            content_type=content_type or "text/markdown",
            as_attachment=True,
            filename=f"{statement.id}-document.md",
        )


class StatementTransactionApprovalView(APIView):
    """
    Approve the whole proposed transaction batch for a statement, atomically
    committing it to the ledger.

    There's no per-transaction endpoint and no partial approval — the
    submitted `transactions` array must be the exact same length as the
    proposed one from `GET /statements/{id}`, matched by array position
    (there's no per-row id to match on instead). Only valid while the
    statement's `status` is `normalized`; anything else is rejected with a
    422 (`error.code: "invalid_status_transition"`). A length mismatch is
    rejected with a 422 (`error.code: "transaction_count_mismatch"`) rather
    than treated as a partial batch.

    Each row is re-checked against the ledger for duplicates at commit
    time (not trusted from the normalize-time preview, since time may have
    passed) — a duplicate is silently skipped (`transaction_id: null`,
    `duplicate_of` set to the existing row's id), not treated as an error.
    On success the statement's `status` advances straight to `approved`.

    This is also the one and only point where the account can be
    confirmed or corrected: the optional `account_id` in the request body
    overrides whatever account normalization inferred from OCR — the
    client never supplies one at upload time, only here, once they've seen
    the inferred `bank_name`/`account_hint` via `GET /statements/{id}`.
    """

    @extend_schema(
        request=TransactionApprovalRequestSerializer,
        responses={200: TransactionApprovalResponseSerializer, **error_responses(404, 422)},
    )
    def post(self, request, statement_id):
        statement = get_object_or_404(StatementFile, id=statement_id, user=request.user)

        if statement.status != StatementFile.STATUS_NORMALIZED:
            raise BusinessRuleError(
                "This statement is not awaiting transaction approval.",
                code="invalid_status_transition",
            )

        request_serializer = TransactionApprovalRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)
        submitted = request_serializer.validated_data["transactions"]
        account_id = request_serializer.validated_data.get("account_id")
        if account_id:
            statement.account = get_object_or_404(BankAccount, id=account_id, user=request.user)
            statement.save(update_fields=["account"])

        proposed = (statement.normalized_payload or {}).get("transactions", [])

        if len(submitted) != len(proposed):
            raise BusinessRuleError(
                f"Expected {len(proposed)} transactions, got {len(submitted)} — "
                "the full proposed batch must be submitted together.",
                code="transaction_count_mismatch",
            )

        resolved = []
        with db_transaction.atomic():
            for row in submitted:
                merchant_raw = row.get("merchant_raw")
                # Re-run at commit time (System_Architecture.md §8) rather than
                # trusting the normalize-time duplicate_of snapshot — another
                # statement could have inserted a colliding row since then.
                existing = Transaction.objects.filter(
                    user=statement.user,
                    account=statement.account,
                    transaction_date=row["transaction_date"],
                    amount=row["amount"],
                    merchant_raw=merchant_raw,
                ).first()
                if existing is not None:
                    resolved.append(
                        {
                            "transaction_date": row["transaction_date"],
                            "merchant_raw": merchant_raw,
                            "amount": row["amount"],
                            "transaction_id": None,
                            "duplicate_of": existing.id,
                        }
                    )
                    continue

                created = Transaction.objects.create(
                    user=statement.user,
                    account=statement.account,
                    statement=statement,
                    transaction_date=row["transaction_date"],
                    merchant_raw=merchant_raw,
                    category=row.get("category"),
                    amount=row["amount"],
                    transaction_type=row.get("transaction_type"),
                    source="statement",
                )
                resolved.append(
                    {
                        "transaction_date": row["transaction_date"],
                        "merchant_raw": merchant_raw,
                        "amount": row["amount"],
                        "transaction_id": created.id,
                        "duplicate_of": None,
                    }
                )

            statement.status = StatementFile.STATUS_APPROVED
            statement.failure_reason = None
            statement.failed_phase = None
            statement.save(update_fields=["status", "failure_reason", "failed_phase"])

        return Response(
            TransactionApprovalResponseSerializer(
                {"statement_status": statement.status, "resolved": resolved}
            ).data
        )
