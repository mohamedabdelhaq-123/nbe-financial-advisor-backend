"""Dev/test-only introspection into the in-memory challenge store — lets an
integration test read a generated OTP directly instead of needing a real
email gateway. Disabled unless MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS=1 (see
app/config.py); when disabled this 404s exactly as if the route didn't
exist, rather than 403ing (no confirmation to a prober that a debug surface
exists at all)."""

from fastapi import APIRouter, HTTPException, status

from app import config, store

router = APIRouter()


@router.get("/debug/challenges/{challenge_id}")
def get_challenge_debug(challenge_id: str):
    """Returns a challenge's current OTP/customer/email state, for
    integration tests to drive /login/verify without a real email gateway."""
    if not config.DEBUG_ENDPOINTS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    challenge = store.get_challenge(challenge_id)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Challenge not found")

    return {
        "challenge_id": challenge.challenge_id,
        "customer_id": challenge.customer_id,
        "email": challenge.email,
        "otp": challenge.otp,
    }
