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

from core.serializers.auth import (
    LoginSerializer,
    RefreshResponseSerializer,
    SignupSerializer,
    TokenPairResponseSerializer,
)


def _set_refresh_cookie(response, refresh_token: str) -> None:
    """
    The one place that sets the refresh-token cookie (PLAN.md Checkpoint E —
    hybrid httpOnly approach: only the refresh token is a cookie, the access
    token stays in the response body). See config/settings.py's
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
    Shared response shape for every endpoint that issues fresh tokens.
    There's no dedicated Data Shapes doc for the Profile/Auth domain (see
    PLAN.md §5's open items), so this mirrors the one concrete example the
    docs do give — docs/API_GUIDE/Data_Shapes_Administration.md's
    POST /admin/auth/login — for consistency across the whole API's auth
    surface, minus refresh_token: that's an httpOnly cookie now, never in
    the response body (PLAN.md Checkpoint E).
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
    """POST /auth/signup — creates a user and returns a token pair immediately."""

    permission_classes = [AllowAny]

    @extend_schema(request=SignupSerializer, responses={201: TokenPairResponseSerializer})
    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return _token_pair_response(user, status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /auth/login"""

    permission_classes = [AllowAny]

    @extend_schema(request=LoginSerializer, responses={200: TokenPairResponseSerializer})
    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return _token_pair_response(serializer.validated_data["user"], status.HTTP_200_OK)


class RefreshView(APIView):
    """
    POST /auth/refresh

    Takes no request body — the refresh token comes from the httpOnly cookie
    (PLAN.md Checkpoint E), not a client-supplied field. Delegates the actual
    rotate/blacklist logic to simplejwt's own TokenRefreshSerializer (it
    correctly mutates the token's jti/exp/iat in place per simplejwt's
    implementation — not something worth reimplementing by hand) and only
    remaps the outer field name from simplejwt's default `access` to this
    project's `access_token` convention, to stay consistent with
    signup/login above. The rotated refresh token is re-set as the cookie,
    never returned in the body.
    """

    permission_classes = [AllowAny]

    @extend_schema(request=None, responses={200: RefreshResponseSerializer})
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
    POST /auth/logout — blacklists the refresh token so it can't be
    replayed, then clears the cookie. Takes no request body — the refresh
    token comes from the httpOnly cookie (PLAN.md Checkpoint E), never a
    client-supplied field. Idempotent: no cookie present is treated as
    "already logged out" (204), not an error — a cookie-driven flow can't
    have a client "forget" to send it the way a body field could be omitted.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
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
