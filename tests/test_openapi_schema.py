"""
Guards against drf-spectacular schema-generation warnings — e.g. an
authentication class or view that doesn't export cleanly to the OpenAPI
schema (see core/openapi.py's *AuthenticationScheme classes for the fix
shape: a custom authenticator needs an OpenApiAuthenticationExtension or
drf-spectacular can't represent its security scheme). CI
(.github/workflows/ci.yml) separately runs `python manage.py spectacular
--file openapi.yaml` as a generation smoke test; this covers the same
--fail-on-warn check from inside the pytest suite.
"""

import tempfile

from django.core.management import call_command


def test_schema_generates_without_warnings():
    with tempfile.NamedTemporaryFile(suffix=".yaml") as f:
        call_command("spectacular", "--file", f.name, "--fail-on-warn")
