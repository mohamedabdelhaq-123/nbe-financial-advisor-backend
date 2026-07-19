"""
Stateless password-reset / email-verification tokens — no "tokens" table.

Both reuse Django's own PasswordResetTokenGenerator machinery (make_token/
check_token, timestamp-limited by settings.PASSWORD_RESET_TIMEOUT): a token
is a salted HMAC over the user's pk plus some piece of user state that's
expected to change once the token has been used, so a used token becomes
invalid on its own without a database row to delete. Password reset already
gets this for free from Django (the hash includes user.password, which
set_password() changes); EmailVerificationTokenGenerator below does the same
trick keyed on email_verified instead.
"""

from django.contrib.auth.tokens import PasswordResetTokenGenerator

# Django's own default_token_generator would also work here, but a project-
# local instance keeps this module the one place both generators are defined
# and documented together.
password_reset_token_generator = PasswordResetTokenGenerator()


class EmailVerificationTokenGenerator(PasswordResetTokenGenerator):
    """
    Same stateless/expiring/single-use mechanism as PasswordResetTokenGenerator,
    keyed on `email_verified` instead of `password`/`last_login` — a token
    is invalidated the moment the user is actually verified, so a reused
    (already-redeemed) link fails the same way an already-used password
    reset link does, with no separate "used tokens" table.
    """

    # Distinct key_salt so a password-reset token and a verification token
    # can never collide/be replayed as each other, even coincidentally.
    key_salt = "core.auth_tokens.EmailVerificationTokenGenerator"

    def _make_hash_value(self, user, timestamp):
        return f"{user.pk}{user.email_verified}{timestamp}{user.email}"


email_verification_token_generator = EmailVerificationTokenGenerator()
