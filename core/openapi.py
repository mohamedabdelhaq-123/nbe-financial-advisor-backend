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

from drf_spectacular.utils import OpenApiResponse

from core.serializers.errors import ErrorResponseSerializer

_DEFAULT_DESCRIPTIONS = {
    400: "Malformed request — invalid JSON or a required field is missing entirely.",
    401: "Missing, invalid, or expired credentials.",
    403: "Authenticated, but not permitted to perform this action.",
    404: "The resource doesn't exist, or doesn't belong to the requesting user "
    "(the API never distinguishes the two, to avoid leaking existence of other users' data).",
    409: "Conflicts with an existing resource — e.g. this already exists, use PATCH instead.",
    422: "The request is well-formed but fails a validation or business rule "
    "(e.g. budget allocations must sum to 100).",
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
