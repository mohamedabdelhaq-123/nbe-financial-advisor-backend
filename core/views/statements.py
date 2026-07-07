import uuid
from datetime import date
from decimal import Decimal

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import BankAccount, StatementFile, StatementNormalized, StatementOcrResult, Transaction, UserPreference
from core.serializers.statements import (
    StatementFileSerializer,
    StatementNormalizedResponseSerializer,
    StatementOcrResultResponseSerializer,
)
from services import ai_service, file_storage


def _run_mock_pipeline(statement: StatementFile) -> None:
    """
    Runs the OCR + normalization pipeline mock inline, synchronously, right
    inside the request/response cycle of POST /statements.

    In the real implementation this is where Django would enqueue a Celery
    job and return immediately with status="pending" (API Design Guidelines
    §9), with the AI service call happening later, out of band. No
    Celery/async worker is wired up yet (see PLAN.md §5's open items), so
    there's no async boundary to actually return across — by the time
    POST /statements sends its response, this function has already run to
    completion and the row reflects its final state. The 202 status *code*
    is kept regardless, since that's the contract the frontend is expected
    to build against once the real async pipeline lands.
    """
    result = ai_service.normalize(statement)

    StatementOcrResult.objects.create(
        statement=statement,
        seaweed_file_id=file_storage.ocr_artifact_key(statement.user_id, statement.id),
        ocr_engine=result["ocr"]["engine"],
        confidence_score=Decimal(str(result["ocr"]["confidence_score"])),
    )

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
        # Duplicate-prevention guardrail (System_Architecture.md §8): a
        # composite match on (user, account, date, amount, raw merchant text)
        # runs before insert, regardless of origin. Doing the lookup directly
        # here (rather than calling Transaction.is_duplicate(), which only
        # returns a bool) lets us record *which* existing transaction this
        # duplicates, per Data_Shapes_Statements.md's `duplicate_of` field.
        existing = Transaction.objects.filter(
            user=statement.user,
            account=statement.account,
            transaction_date=transaction_date,
            amount=amount,
            merchant_raw=txn["merchant_raw"],
        ).first()
        if existing is not None:
            txn["duplicate_of"] = str(existing.id)
            continue

        new_txn = Transaction.objects.create(
            user=statement.user,
            account=statement.account,
            statement=statement,
            transaction_date=transaction_date,
            merchant_raw=txn["merchant_raw"],
            category=txn.get("category"),
            amount=amount,
            transaction_type=txn.get("transaction_type"),
            source="statement",
        )
        transaction_dates.append(transaction_date)
        txn["duplicate_of"] = None

    StatementNormalized.objects.create(
        statement=statement,
        normalized_json=normalized,
        model_used=result["model_used"],
    )

    statement.status = "normalized"
    if transaction_dates:
        statement.start_transaction_date = min(transaction_dates)
        statement.last_transaction_date = max(transaction_dates)
    statement.save(
        update_fields=["account", "status", "start_transaction_date", "last_transaction_date"]
    )


def create_statement_from_upload(user, file_obj, account_id=None) -> StatementFile:
    """
    The full upload -> checksum-dedupe -> mock-pipeline flow, factored out of
    StatementListCreateView.post() so the Conversations domain's
    POST /chat/conversations/{id}/attachments can reuse it exactly —
    Data_Shapes_Conversations.md: "Shortcut into the Statements pipeline...
    same underlying processing as POST /statements". Raises the same
    ValidationError/404 a direct upload would, so both call sites behave
    identically (API Design Guidelines §1: "one backend, one write path").
    """
    if not file_obj:
        raise ValidationError({"file": "This field is required."})

    checksum = file_storage.compute_checksum(file_obj.read())

    if StatementFile.objects.filter(user=user, checksum=checksum).exists():
        # File-level duplicate-upload check (DB_Schema.md's
        # UNIQUE(user_id, checksum)) — System_Architecture.md §8: "A
        # secondary file-level checksum check rejects a byte-identical
        # re-upload before OCR even runs."
        raise ValidationError(
            {"file": "This exact file has already been uploaded."},
            code="duplicate_statement",
        )

    account = None
    if account_id:
        account = get_object_or_404(BankAccount, id=account_id, user=user)

    extension = file_obj.name.rsplit(".", 1)[-1].lower() if "." in file_obj.name else "bin"
    statement_id = uuid.uuid4()
    statement = StatementFile.objects.create(
        id=statement_id,
        user=user,
        account=account,
        seaweed_file_id=file_storage.raw_statement_key(user.id, statement_id, extension),
        checksum=checksum,
        status="pending",
    )

    _run_mock_pipeline(statement)
    return statement


class StatementListCreateView(generics.ListAPIView):
    """GET /statements, POST /statements (multipart upload)"""

    serializer_class = StatementFileSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        qs = StatementFile.objects.filter(user=self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        account_id = self.request.query_params.get("account_id")
        if account_id:
            qs = qs.filter(account_id=account_id)
        return qs.order_by("-upload_date")

    def post(self, request, *args, **kwargs):
        statement = create_statement_from_upload(
            request.user, request.FILES.get("file"), account_id=request.data.get("account_id")
        )
        return Response(StatementFileSerializer(statement).data, status=status.HTTP_202_ACCEPTED)


class StatementDetailView(generics.RetrieveDestroyAPIView):
    """GET/DELETE /statements/{statement_id}"""

    serializer_class = StatementFileSerializer
    lookup_url_kwarg = "statement_id"

    def get_queryset(self):
        return StatementFile.objects.filter(user=self.request.user)

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
            file_storage.delete_prefix(f"pfm-statements-artifacts/{instance.user_id}/{instance.id}/")
        instance.delete()


class StatementOcrResultView(APIView):
    """GET /statements/{statement_id}/ocr-result"""

    @extend_schema(responses={200: StatementOcrResultResponseSerializer})
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
                "artifact_url": file_storage.get_signed_url(
                    file_storage.ocr_artifact_key(statement.user_id, statement.id)
                ),
            }
        )


class StatementNormalizedView(APIView):
    """GET /statements/{statement_id}/normalized"""

    @extend_schema(responses={200: StatementNormalizedResponseSerializer})
    def get(self, request, statement_id):
        statement = get_object_or_404(StatementFile, id=statement_id, user=request.user)
        record = statement.normalized_records.order_by("-adjusted_at").first()
        if record is None:
            raise NotFound("Normalized result not available yet.")
        return Response(
            {
                "statement_id": str(statement.id),
                "model_used": record.model_used,
                "adjusted_at": record.adjusted_at,
                "transaction_count": len(record.normalized_json.get("transactions", [])),
                "normalized_json": record.normalized_json,
            }
        )
