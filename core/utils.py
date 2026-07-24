def mask_account_number(value: str | None) -> str | None:
    """Masks all but the last 4 characters of a stored account number for
    display purposes only — never mutates what's stored. Idempotent:
    masking an already-masked value (e.g. "****1234", what bank-sync
    providers hand us) is a no-op, since its own last 4 characters are
    still "1234". Falsy input passes through unchanged."""
    if not value:
        return value
    return f"****{value[-4:]}"
