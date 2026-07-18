"""
Shared OpenAPI/Swagger documentation helpers.

Every endpoint that can fail shares one error envelope (core/exceptions.py's
api_exception_handler: {"error": {"code", "message", "fields"}}) regardless
of which status code it returns. Without a shared helper, every view's
`@extend_schema(responses={...})` would hand-write the same
OpenApiResponse(response=ErrorResponseSerializer, description="...") four or
five times per endpoint across the whole API. error_responses() builds that
fragment once per status code instead.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from drf_spectacular.utils import OpenApiResponse

from core.serializers.errors import ErrorResponseSerializer


class SSETicketAuthenticationScheme(OpenApiAuthenticationExtension):
    """
    Documents core/authentication.py's SSETicketAuthentication for GET
    /events/stream — without this, drf-spectacular has no built-in scheme
    for a custom query-param ticket (unlike JWTAuthentication, which
    simplejwt registers its own extension for) and just drops the security
    requirement silently. target_class as a string, not an import, to avoid
    core/authentication.py <-> core/openapi.py import ordering concerns.
    """

    target_class = "core.authentication.SSETicketAuthentication"
    name = "SSETicketAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "query",
            "name": "ticket",
            "description": "Short-lived, single-use ticket minted via POST /events/ticket/.",
        }


class BankSyncServiceAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents core/authentication.py's BankSyncServiceAuthentication for
    POST /webhooks/bank-sync/ — same reasoning as SSETicketAuthenticationScheme
    above: a shared-secret header has no built-in drf-spectacular scheme."""

    target_class = "core.authentication.BankSyncServiceAuthentication"
    name = "BankSyncServiceAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-Webhook-Secret",
            "description": (
                "Shared secret presented by mock-bank-sync (later: a real "
                "bank's own sync feed) when pushing a transaction batch."
            ),
        }


class MockBankServiceAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents core/authentication.py's MockBankServiceAuthentication for
    POST /internal/notifications/email/."""

    target_class = "core.authentication.MockBankServiceAuthentication"
    name = "MockBankServiceAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "X-Service-Token",
            "description": (
                "Shared secret presented by mock-bank-oauth when delivering an OTP email."
            ),
        }


_DEFAULT_DESCRIPTIONS = {
    400: "Malformed request — invalid JSON or a required field is missing entirely.",
    401: "Missing, invalid, or expired credentials.",
    403: "Authenticated, but not permitted to perform this action.",
    404: "The resource doesn't exist, or doesn't belong to the requesting user "
    "(the API never distinguishes the two, to avoid leaking existence of other users' data).",
    409: "Conflicts with an existing resource — e.g. this already exists, use PATCH instead.",
    422: "The request is well-formed but fails a validation or business rule "
    "(e.g. budget allocations must sum to 100).",
    502: "The AI service failed or is unreachable.",
}


def error_responses(*status_codes: int) -> dict[int, OpenApiResponse]:
    """
    Build `responses={...}` entries for the given error status codes, each
    pointing at the one shared error envelope every endpoint returns, with a
    sensible default description per code.

    Usage: responses={200: MySerializer, **error_responses(400, 404)}

    To override a default description for one endpoint's specific error
    case, list the status code again afterwards in the same responses dict —
    a later key wins over the spread:
        responses={
            200: MySerializer,
            **error_responses(404, 409),
            409: OpenApiResponse(response=ErrorResponseSerializer, description="More specific."),
        }
    """
    return {
        code: OpenApiResponse(
            response=ErrorResponseSerializer, description=_DEFAULT_DESCRIPTIONS[code]
        )
        for code in status_codes
    }
