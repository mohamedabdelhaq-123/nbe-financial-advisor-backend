from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import Http404
from django.template.loader import render_to_string
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from core.auth_tokens import email_verification_token_generator, password_reset_token_generator
from core.exceptions import BusinessRuleError, NotificationServiceUnavailable
from core.models import BankConnection, User
from core.openapi import error_responses
from core.serializers.auth import (
    BankLoginCallbackSerializer,
    BankLoginInitiateResponseSerializer,
    BankLoginInitiateSerializer,
    EmailVerificationConfirmSerializer,
    LoginSerializer,
    PasswordResetConfirmSerializer,
    PasswordResetRequestSerializer,
    RefreshResponseSerializer,
    SignupSerializer,
    TokenPairResponseSerializer,
)
from services import bank_login_states, link_tickets, notification_service
from services.bank_connectors import BankConnectorError, get_connector
from services.bank_connectors.sync import apply_synced_accounts


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


def _verify_email_link(user, token):
    """
    The query string carries one opaque, single-use ticket (services/link_tickets.py)
    rather than `user_id`/`token` directly — keeps both out of the URL (browser
    history, referrer headers, email-client link-preview scanners). The
    frontend's /verify-email page reads `t` and forwards it as-is to
    EmailVerificationConfirmSerializer, which redeems it server-side back to
    the (user_id, token) pair this function used to put in the URL directly.
    """
    ticket = link_tickets.mint_link_ticket(user.id, token, settings.PASSWORD_RESET_TIMEOUT)
    return f"{settings.FRONTEND_URL}/verify-email?t={ticket}"


def _reset_password_link(user, token):
    """Same reasoning as _verify_email_link, for
    PasswordResetConfirmSerializer's body field."""
    ticket = link_tickets.mint_link_ticket(user.id, token, settings.PASSWORD_RESET_TIMEOUT)
    return f"{settings.FRONTEND_URL}/reset-password?t={ticket}"


def _send_verification_email(user):
    """
    Best-effort — same "don't fail the parent action over a notification"
    pattern as core/tasks/bank_sync.py's "new transactions synced" email.
    Signup succeeds (and email verification remains a no-op gate on login,
    PLAN.md Checkpoint 5) even if this send fails.
    """
    token = email_verification_token_generator.make_token(user)
    link = _verify_email_link(user, token)
    try:
        notification_service.send_email(
            user.email,
            "Verify your email",
            "Confirm your email address by visiting the link below:\n\n"
            f"{link}\n\n"
            "This link expires in a few days.",
            html_body=render_to_string("emails/verify_email.html", {"link": link}),
        )
    except notification_service.NotificationServiceError:
        pass


class SignupView(APIView):
    """
    Create a new end-user account and log them in immediately — email
    verification (PLAN.md Checkpoint 5) doesn't gate login or usability at
    all today; a verification email is sent best-effort in the background,
    and `email_verified` is purely informational until/unless a future
    change decides to gate something on it.

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
        _send_verification_email(user)
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


class BankLoginInitiateView(APIView):
    """
    POST /auth/bank-login/initiate/ — starts signing in as a bank customer
    rather than with an app email/password. A secondary entry point
    alongside SignupView/LoginView, not a replacement for either: a bank
    customer with no prior app account authenticates entirely through their
    bank's own OAuth+OTP flow and comes back from the callback below with a
    normal app session, same as signup/login.

    There's no app user yet at this point (that's resolved in the
    callback), so unlike the authenticated "link a bank" flow
    (BankConnectionListCreateView, core/views/bank_connections.py) there's
    no BankConnection row to persist `state` against — it's minted via
    services/bank_login_states.py (Redis-backed) instead.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=BankLoginInitiateSerializer,
        responses={201: BankLoginInitiateResponseSerializer, **error_responses(404, 422)},
    )
    def post(self, request):
        serializer = BankLoginInitiateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider_slug = serializer.validated_data["provider_slug"]

        try:
            connector = get_connector(provider_slug)
        except BankConnectorError:
            # Unknown provider — existence-leak-avoidance style (API Design
            # Guidelines §10), same reasoning as an unowned resource id 404ing
            # rather than 403ing.
            raise Http404("Unknown bank provider.")

        state = bank_login_states.mint_state(provider_slug)
        authorize_url = connector.get_authorize_url(
            state=state, redirect_uri=settings.MOCK_BANK_OAUTH_REDIRECT_URI
        )
        return Response(
            {"state": state, "authorize_url": authorize_url}, status=status.HTTP_201_CREATED
        )


class BankLoginCallbackView(APIView):
    """
    POST /auth/bank-login/callback/ — called by the frontend once the
    provider's OAuth redirect has landed back on it with ?code&state.
    Exchanges the code for a token, resolves which app user this bank
    customer is (or provisions one, on a first-ever login), and returns a
    normal token pair — the same shape SignupView/LoginView return.

    Identity is resolved by (provider_slug, external_customer_id): a
    repeat login for the same bank customer always logs into the same app
    user. On a first-ever login, if the bank's email matches an existing
    app user, that user is reused (their manual data under this same
    bank's name is replaced by the real, synced data — see
    services/bank_connectors/sync.py's apply_synced_accounts) rather than
    creating a duplicate account.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=BankLoginCallbackSerializer,
        responses={200: TokenPairResponseSerializer, **error_responses(422)},
    )
    def post(self, request):
        serializer = BankLoginCallbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data["code"]
        state = serializer.validated_data["state"]

        provider_slug = bank_login_states.redeem_state(state)
        if provider_slug is None:
            raise BusinessRuleError("OAuth state mismatch.", code="invalid_oauth_state")

        connector = get_connector(provider_slug)
        try:
            token = connector.exchange_code_for_token(code)
        except BankConnectorError as exc:
            raise BusinessRuleError(
                "Failed to complete the bank login.", code="bank_login_failed"
            ) from exc

        connection = (
            BankConnection.objects.select_related("user")
            .filter(provider_slug=provider_slug, external_customer_id=token["external_customer_id"])
            .first()
        )

        if connection is not None:
            # Repeat login: this bank customer already has an app user and
            # real synced data. A sync hiccup here must not lock them out —
            # there's no other way in for a bank-provisioned user — so this
            # is best-effort, same tolerance the ongoing webhook push has.
            user = connection.user
            connection.access_token = token["access_token"]
            connection.refresh_token = token.get("refresh_token")
            connection.save(update_fields=["access_token", "refresh_token"])
            try:
                accounts = connector.fetch_accounts(connection.access_token)
                apply_synced_accounts(connection, accounts, connector)
            except BankConnectorError:
                pass
            return _token_pair_response(user, status.HTTP_200_OK)

        # First time this bank customer has ever logged in. Fetch accounts
        # before writing anything — if the bank can't be reached, nothing
        # should be persisted at all (no half-provisioned user).
        try:
            accounts = connector.fetch_accounts(token["access_token"])
        except BankConnectorError as exc:
            raise BusinessRuleError(
                "Failed to complete the bank login.", code="bank_login_failed"
            ) from exc

        user = User.objects.filter(email=token["email"]).first()
        new_connection = None
        try:
            with transaction.atomic():
                if user is None:
                    user = User.objects.create_user(
                        email=token["email"],
                        name=token.get("name") or "Bank Customer",
                        password=None,
                    )
                connection = BankConnection.objects.create(
                    user=user,
                    provider_slug=provider_slug,
                    external_customer_id=token["external_customer_id"],
                    status=BankConnection.STATUS_LINKED,
                    linked_at=timezone.now(),
                    access_token=token["access_token"],
                    refresh_token=token.get("refresh_token"),
                )
            new_connection = connection
        except IntegrityError:
            # A concurrent callback for the same bank customer won the
            # race — log into the winner's user rather than erroring.
            connection = BankConnection.objects.select_related("user").get(
                provider_slug=provider_slug, external_customer_id=token["external_customer_id"]
            )
            user = connection.user

        if new_connection is not None:
            # Runs only once the User/BankConnection creation above has
            # actually committed — apply_synced_accounts() dispatches a real
            # Celery task via ingest_synced_transactions.delay(), which a
            # real broker's worker can pick up and run before an
            # uncommitted transaction is visible to it, silently no-oping
            # against a BankAccount id it can't find yet.
            apply_synced_accounts(new_connection, accounts, connector)

        return _token_pair_response(user, status.HTTP_201_CREATED)


def _blacklist_all_outstanding_tokens(user):
    """
    Invalidates every refresh token ever issued to this user, not just the
    one in the current request's cookie (LogoutView only blacklists that
    one) — a password reset should end every other session too, in case the
    password was compromised and a stale refresh token is still live
    somewhere.
    """
    for outstanding in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=outstanding)


class PasswordResetRequestView(APIView):
    """
    Start a password reset for a local (email+password) account. Always
    responds 202 regardless of whether `email` matches a real account —
    same enumeration-avoidance reasoning as LoginView's generic error
    message. If it does match, emails a one-time reset link (best-effort;
    see PasswordResetConfirmView for what that link contains).

    Not for AdminUser or bank-linked accounts — see core/auth_tokens.py and
    PLAN.md Checkpoint 5's scope note.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=PasswordResetRequestSerializer,
        responses={202: None, **error_responses(422)},
    )
    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = User.objects.filter(email=serializer.validated_data["email"]).first()
        if user is not None:
            token = password_reset_token_generator.make_token(user)
            link = _reset_password_link(user, token)
            try:
                notification_service.send_email(
                    user.email,
                    "Reset your password",
                    "Reset your password by visiting the link below:\n\n"
                    f"{link}\n\n"
                    "If you didn't request this, you can ignore this email.",
                    html_body=render_to_string("emails/reset_password.html", {"link": link}),
                )
            except notification_service.NotificationServiceError:
                # Same reasoning as every other best-effort send in this
                # file — and doubly so here: a visible failure would also
                # leak whether `email` matched a real account.
                pass
        return Response(status=status.HTTP_202_ACCEPTED)


class PasswordResetConfirmView(APIView):
    """
    Complete a password reset given the `user_id`/`token` from the email
    PasswordResetRequestView sent. The token is single-use in effect (not
    by row-deletion): it's a hash over the user's current password, so
    set_password() below changes the very state the token was hashed
    against, and immediately invalidates it (and any other outstanding
    reset token for this user) without a database row to track.

    Also blacklists every outstanding refresh token for this user, on the
    theory that a password reset is often prompted by a compromised
    account — a stale but still-valid refresh token shouldn't survive it.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=PasswordResetConfirmSerializer,
        responses={200: None, **error_responses(422)},
    )
    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        redeemed = link_tickets.redeem_link_ticket(data["t"])
        user = User.objects.filter(id=redeemed["user_id"]).first() if redeemed else None
        if (
            user is None
            or not password_reset_token_generator.check_token(user, redeemed["token"])
        ):
            # Doesn't distinguish "no such ticket"/"no such user"/"bad or
            # expired token" — same enumeration-avoidance reasoning as
            # everywhere else here.
            raise DRFValidationError({"t": "Invalid or expired link."})

        user.set_password(data["new_password"])
        user.save(update_fields=["password"])
        _blacklist_all_outstanding_tokens(user)
        return Response(status=status.HTTP_200_OK)


class EmailVerificationRequestView(APIView):
    """
    (Re)send the verification email to the current user — unlike
    PasswordResetRequestView, this is IsAuthenticated (you can only ever
    resend to yourself, so there's no email-enumeration surface to protect
    against here) and a genuine send failure is surfaced as a 502 rather
    than swallowed, since sending the email is this endpoint's entire job.
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=None,
        responses={202: None, **error_responses(401, 502)},
    )
    def post(self, request):
        token = email_verification_token_generator.make_token(request.user)
        link = _verify_email_link(request.user, token)
        try:
            notification_service.send_email(
                request.user.email,
                "Verify your email",
                "Confirm your email address by visiting the link below:\n\n"
                f"{link}\n\n"
                "This link expires in a few days.",
                html_body=render_to_string("emails/verify_email.html", {"link": link}),
            )
        except notification_service.NotificationServiceError as exc:
            raise NotificationServiceUnavailable(str(exc)) from exc
        return Response(status=status.HTTP_202_ACCEPTED)


class EmailVerificationConfirmView(APIView):
    """
    Complete email verification given the opaque link ticket (`t`) from the
    signup (or resend) email — see services/link_tickets.py and
    _verify_email_link's docstring for why the URL carries a ticket rather
    than `user_id`/`token` directly. Redeeming it recovers the same
    (user_id, token) pair this endpoint used to receive straight from the
    client, checked the same way: same stateless-token shape as
    PasswordResetConfirmView, keyed on `email_verified` instead of
    `password` (core/auth_tokens.py::EmailVerificationTokenGenerator) — so
    the token is invalidated by this endpoint's own effect (flipping
    email_verified to True) with no separate "used tokens" table.
    """

    permission_classes = [AllowAny]

    @extend_schema(
        request=EmailVerificationConfirmSerializer,
        responses={200: None, **error_responses(422)},
    )
    def post(self, request):
        serializer = EmailVerificationConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        redeemed = link_tickets.redeem_link_ticket(data["t"])
        user = User.objects.filter(id=redeemed["user_id"]).first() if redeemed else None
        if (
            user is None
            or not email_verification_token_generator.check_token(user, redeemed["token"])
        ):
            raise DRFValidationError({"t": "Invalid or expired link."})

        user.email_verified = True
        user.save(update_fields=["email_verified"])
        return Response(status=status.HTTP_200_OK)
