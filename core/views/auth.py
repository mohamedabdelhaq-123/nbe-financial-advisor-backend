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
    LogoutSerializer,
    SignupSerializer,
)


def _token_pair_response(user, status_code):
    """
    Shared response shape for every endpoint that issues fresh tokens.
    There's no dedicated Data Shapes doc for the Profile/Auth domain (see
    PLAN.md §5's open items), so this mirrors the one concrete example the
    docs do give — docs/API_GUIDE/Data_Shapes_Administration.md's
    POST /admin/auth/login, which returns access_token/refresh_token plus an
    id — for consistency across the whole API's auth surface.
    """
    refresh = RefreshToken.for_user(user)
    return Response(
        {
            "access_token": str(refresh.access_token),
            "refresh_token": str(refresh),
            "user_id": str(user.id),
        },
        status=status_code,
    )


class SignupView(APIView):
    """POST /auth/signup — creates a user and returns a token pair immediately."""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = SignupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return _token_pair_response(user, status.HTTP_201_CREATED)


class LoginView(APIView):
    """POST /auth/login"""

    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return _token_pair_response(serializer.validated_data["user"], status.HTTP_200_OK)


class RefreshView(APIView):
    """
    POST /auth/refresh

    Delegates the actual rotate/blacklist logic to simplejwt's own
    TokenRefreshSerializer (it correctly mutates the token's jti/exp/iat in
    place per simplejwt's implementation — not something worth reimplementing
    by hand) and only remaps the outer field names from simplejwt's default
    `refresh`/`access` to this project's `refresh_token`/`access_token`
    convention, to stay consistent with signup/login above.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        inner = TokenRefreshSerializer(data={"refresh": request.data.get("refresh_token")})
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
        data = {"access_token": inner.validated_data["access"]}
        if "refresh" in inner.validated_data:
            # Only present when SIMPLE_JWT["ROTATE_REFRESH_TOKENS"] is True.
            data["refresh_token"] = inner.validated_data["refresh"]
        return Response(data, status=status.HTTP_200_OK)


class LogoutView(APIView):
    """POST /auth/logout — blacklists the given refresh token so it can't be replayed."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            RefreshToken(serializer.validated_data["refresh_token"]).blacklist()
        except TokenError as exc:
            raise DRFValidationError(str(exc)) from exc
        return Response(status=status.HTTP_204_NO_CONTENT)
