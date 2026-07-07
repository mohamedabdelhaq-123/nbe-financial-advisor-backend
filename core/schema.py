"""
drf-spectacular extensions — teaches the schema generator how to represent
this project's custom pieces, so the generated OpenAPI schema/Swagger UI
(API Design Guidelines §11) is accurate rather than falling back to "unknown"
placeholders for anything that isn't a stock DRF class.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class UserJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents core.authentication.UserJWTAuthentication as a standard Bearer JWT scheme."""

    target_class = "core.authentication.UserJWTAuthentication"
    name = "userJwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "End-user token from POST /auth/login or /auth/signup. Rejected on /admin/* routes.",
        }


class AdminJWTAuthenticationScheme(OpenApiAuthenticationExtension):
    """Documents core.authentication.AdminJWTAuthentication as a standard Bearer JWT scheme."""

    target_class = "core.authentication.AdminJWTAuthentication"
    name = "adminJwtAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Admin token from POST /admin/auth/login. Never accepted on end-user routes.",
        }
