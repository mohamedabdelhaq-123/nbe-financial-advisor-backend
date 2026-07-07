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

from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.views import exception_handler as drf_default_exception_handler


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
