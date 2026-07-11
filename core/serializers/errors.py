from rest_framework import serializers


class ErrorDetailSerializer(serializers.Serializer):
    """The nested `error` object itself: a machine-readable `code`, a
    human-readable `message` safe to show a user directly, and an optional
    per-field breakdown."""

    code = serializers.CharField(
        help_text='Machine-readable error identifier, e.g. "validation_error".'
    )
    message = serializers.CharField(help_text="Human-readable description of what went wrong.")
    fields = serializers.JSONField(
        allow_null=True,
        help_text="Per-field validation messages, or null when the error isn't field-shaped.",
    )


class ErrorResponseSerializer(serializers.Serializer):
    """Every error response in this API — 400/401/403/404/409/422 alike —
    shares this one envelope. Only `error.code`/`error.message`/`error.fields`
    differ between cases; the shape itself never does."""

    error = ErrorDetailSerializer()
