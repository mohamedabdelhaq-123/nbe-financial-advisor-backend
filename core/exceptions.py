"""
Global DRF exception handler.

Every endpoint in the system is expected to share one error response shape
(docs/API_GUIDE/API_Design_Guidelines.md §10):

    {"error": {"code": "...", "message": "...", "fields": {...} | null}}

DRF's default handler returns a bare dict/list of field errors instead, and
always uses 400 for both malformed-request-syntax errors (which the docs
reserve for genuinely bad JSON) and serializer/business-rule validation
failures (which the docs want as 422). Wiring this in once, globally
(REST_FRAMEWORK["EXCEPTION_HANDLER"] in config/settings.py), means every
current and future view gets both fixes for free instead of re-implementing
them per view.
"""

from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import exception_handler as drf_default_exception_handler


class ConflictError(APIException):
    """
    409 — for the handful of "this already exists, use PATCH instead" cases
    documented in the API (e.g. POST /budget when a plan already exists —
    Data_Shapes_Budgets.md: "Rejected 409 if a budgets row already exists").
    DRF has no built-in exception for this status code (unlike 404/403/400),
    so it's defined once here for any endpoint that needs it.
    """

    status_code = 409
    default_detail = "This resource already exists."
    default_code = "conflict"


class AIServiceUnavailable(APIException):
    """
    502 — for endpoints that call services/ai_service.py synchronously in
    the request/response cycle (e.g. GET /recommendations) rather than
    buffering the call behind a Celery task the way chat/statement-ingestion
    do. Those async flows record an ai_service failure as
    failure_reason/chat_error instead; this is for the ones with no such
    buffer, where an AIServiceError has to become the HTTP response itself.
    DRF has no built-in exception for 502.
    """

    status_code = 502
    default_detail = "The AI service failed or is unreachable."
    default_code = "ai_service_unavailable"


class NotificationServiceUnavailable(APIException):
    """
    502 — for endpoints that call services/notification_service.py
    synchronously (e.g. InternalNotificationEmailView, core/views/webhooks.py)
    and have no buffering task to instead record the failure on. Same
    reasoning as AIServiceUnavailable above, for the notification gateway
    instead of ai-service.
    """

    status_code = 502
    default_detail = "The notification service failed or is unreachable."
    default_code = "notification_service_unavailable"


class BusinessRuleError(DRFValidationError):
    """
    Raise this instead of a plain DRFValidationError when a business-rule
    failure needs BOTH a specific top-level `code` (e.g. "duplicate_transaction",
    matching docs/API_GUIDE/Data_Shapes_Aggregations.md's documented duplicate
    error) AND a `fields` payload that isn't itself a per-field validation
    map — e.g. referencing an existing resource's id, not reporting which
    input field was malformed. A plain ValidationError's `code` only affects
    a single ErrorDetail's code, which get_codes() can't cleanly surface as
    one top-level string once `detail` is a dict — this sidesteps that by
    carrying the intended `fields` payload separately on the exception itself.
    """

    def __init__(self, message, code, fields=None):
        super().__init__(detail=message, code=code)
        self.error_fields = fields


def _first_message(data):
    """Pulls one human-readable message out of DRF's (possibly nested) error data."""
    if isinstance(data, dict):
        for value in data.values():
            msg = _first_message(value)
            if msg:
                return msg
        return None
    if isinstance(data, (list, tuple)) and data:
        return _first_message(data[0])
    return str(data)


def api_exception_handler(exc, context):
    response = drf_default_exception_handler(exc, context)
    if response is None:
        # Not a DRF/Django exception DRF knows how to render (e.g. an unhandled
        # Python exception) — let it propagate as a 500, same as DRF's default.
        return None

    fields = None
    if isinstance(exc, DRFValidationError):
        # A parse-syntax error (bad JSON) never reaches serializer validation —
        # it's raised as ParseError before this point and correctly stays 400.
        # Anything that got as far as a ValidationError is a semantic/business-
        # rule failure per API Design Guidelines §10, so it becomes 422 here.
        response.status_code = 422
        if isinstance(exc, BusinessRuleError):
            # DRFValidationError.__init__ always coerces a plain string `detail`
            # into a one-item list (see its source — "details should always be
            # coerced to a list if not already"), so both get_codes() and
            # exc.detail come back as one-item lists here, not bare values —
            # _first_message() already knows how to unwrap that shape.
            codes = exc.get_codes()
            code = codes[0] if isinstance(codes, list) else codes
            fields = exc.error_fields
            response.data = {
                "error": {"code": code, "message": _first_message(exc.detail), "fields": fields}
            }
            return response
        code = "validation_error"
        if isinstance(response.data, dict):
            fields = response.data
    else:
        code = getattr(exc, "default_code", "error")

    response.data = {
        "error": {
            "code": code,
            "message": _first_message(response.data) or "An error occurred.",
            "fields": fields,
        }
    }
    return response
