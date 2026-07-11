from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from core.openapi import error_responses
from core.serializers.auth import (
    LoginSerializer,
    RefreshResponseSerializer,
    SignupSerializer,
    TokenPairResponseSerializer,
)


def _set_refresh_cookie(response, refresh_token: str) -> None:
    """
    The one place that sets the refresh-token cookie: the hybrid httpOnly
    approach where only the refresh token is a cookie, the access token
    stays in the response body. See config/settings.py's
    REFRESH_TOKEN_COOKIE_* constants for the SameSite/Secure rationale.
    """
    response.set_cookie(
        settings.REFRESH_TOKEN_COOKIE_NAME,
        refresh_token,
        max_age=settings.REFRESH_TOKEN_COOKIE_MAX_AGE,
        httponly=True,
        secure=settings.REFRESH_TOKEN_COOKIE_SECURE,
        samesite=settings.REFRESH_TOKEN_COOKIE_SAMESITE,
        path="/",
    )


def _token_pair_response(user, status_code):
    """
    Shared response shape for every endpoint that issues fresh tokens:
    an `access_token` (JWT bearer, 30-minute lifetime) plus the `user_id`
    it belongs to. The refresh token is never included here — it's set as
    an httpOnly cookie instead (see _set_refresh_cookie above), so it's
    never readable by JavaScript even if the page were compromised by XSS.
    """
    refresh = RefreshToken.for_user(user)
    response = Response(
        {
            "access_token": str(refresh.access_token),
            "user_id": str(user.id),
        },
        status=status_code,
    )
    _set_refresh_cookie(response, str(refresh))
    return response


class SignupView(APIView):
    """
    Create a new end-user account and log them in immediately — there's no
    separate email-verification step before the account is usable.

    On success, returns an `access_token` (send it as
    `Authorization: Bearer <access_token>` on every subsequent request) and
    sets the refresh token as an httpOnly cookie automatically (never in the
    response body — see `POST /auth/refresh` for how it's used later).
    `email` must be unique; a duplicate signup fails validation rather than
    silently logging into the existing account.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=SignupSerializer,
        responses={201: TokenPairResponseSerializer, **error_responses(422)},
    )
    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return _token_pair_response(user, status.HTTP_201_CREATED)


class LoginView(APIView):
    """
    Authenticate with email + password and receive a fresh token pair, the
    same shape `POST /auth/signup` returns (`access_token` in the body, the
    refresh token set as an httpOnly cookie).

    On failure, the error message is deliberately the same generic
    "Invalid email or password" whether the email doesn't exist or the
    password is wrong — this prevents a login attempt from being used to
    discover which emails are registered.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=LoginSerializer,
        responses={200: TokenPairResponseSerializer, **error_responses(422)},
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return _token_pair_response(serializer.validated_data["user"], status.HTTP_200_OK)


class RefreshView(APIView):
    """
    Exchange the httpOnly refresh-token cookie for a new access token —
    call this when a request fails with 401 because the current
    `access_token` (30-minute lifetime) has expired.

    Takes **no request body at all** — the browser sends the refresh
    token automatically via its httpOnly cookie (it's never readable by
    JavaScript, so there's nothing for a client to pass explicitly).
    Refresh tokens rotate on every use: calling this endpoint invalidates
    the previous refresh token and silently re-sets a new one as the same
    cookie, so no client-side bookkeeping is needed beyond storing the new
    `access_token`. A missing, expired, or already-used (blacklisted)
    refresh cookie means the session is over — clear any in-memory auth
    state and send the user back to login rather than retrying.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=None,
        responses={200: RefreshResponseSerializer, **error_responses(401)},
    )
    def post(self, request):
        refresh_token = request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)
        if not refresh_token:
            raise InvalidToken("No refresh token cookie present.")
        inner = TokenRefreshSerializer(data={"refresh": refresh_token})
        try:
            inner.is_valid(raise_exception=True)
        except TokenError as exc:
            # TokenRefreshSerializer.validate() constructs the token directly
            # and can raise a raw TokenError (expired/blacklisted/malformed)
            # that DRF's is_valid() doesn't wrap on its own — simplejwt's own
            # TokenRefreshView catches exactly this the same way; replicated
            # here since this view can't reuse that one directly (see class
            # docstring). Without this, an invalid refresh token here would
            # leak out as an unhandled 500 instead of a clean 401 JSON error.
            raise InvalidToken(exc.args[0]) from exc
        response = Response(
            {"access_token": inner.validated_data["access"]}, status=status.HTTP_200_OK
        )
        if "refresh" in inner.validated_data:
            # Only present when SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True.
            _set_refresh_cookie(response, inner.validated_data["refresh"])
        return response


class LogoutView(APIView):
    """
    End the current session: blacklists the refresh token (so it can never
    be exchanged for a new access token again, even if it leaked) and
    clears the httpOnly cookie. Requires a currently-valid `access_token`
    on the `Authorization` header — if that's already expired, there's
    nothing meaningful left to blacklist server-side; simply drop the
    client-side access token and cookie state instead of calling refresh
    first just to log out.

    Takes no request body — the refresh token comes from the cookie, never
    a client-supplied field. Idempotent: no cookie present is treated as
    "already logged out" (204), not an error, since a cookie-driven flow
    can't have a client "forget" to send it the way a body field could be
    omitted.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={204: None, **error_responses(401, 422)},
    )
    def post(self, request):
        refresh_token = request.COOKIES.get(settings.REFRESH_TOKEN_COOKIE_NAME)
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except TokenError as exc:
                raise DRFValidationError(str(exc)) from exc
        response = Response(status=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(settings.REFRESH_TOKEN_COOKIE_NAME, path="/")
        return response
