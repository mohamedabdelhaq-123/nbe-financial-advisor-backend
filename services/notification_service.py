"""
Outbound email client — Gmail SMTP via Django's own django.core.mail, not a
raw requests-based REST client like services/ai_service.py (nothing to build:
Django ships SMTP support directly). Kept as one thin function so the vendor
(Gmail SMTP today) can be swapped for a dedicated transactional-email
provider later without touching any call site — see EMAIL_* settings in
config/settings.py.

SMS is intentionally not implemented here — the OTP/notification flow is
email-only; there's no Gmail-equivalent free SMS option, so there's no
reason to take on a paid provider's setup cost before it's actually needed.
"""

from smtplib import SMTPException

from django.core.mail import send_mail


class NotificationServiceError(Exception):
    """Raised for any email-send failure — callers catch this one type
    instead of smtplib's own exception hierarchy."""


def send_email(to: str, subject: str, body: str) -> None:
    """Sends a plain-text email via the configured Gmail SMTP account.
    Raises NotificationServiceError on any send failure."""
    try:
        # from_email=None -> settings.DEFAULT_FROM_EMAIL.
        send_mail(subject, body, None, [to], fail_silently=False)
    except (SMTPException, OSError) as exc:
        # OSError, not just SMTPException: a connection failure (SMTP host
        # unreachable, DNS failure, connection refused/reset) surfaces as a
        # socket-level OSError, not smtplib's own exception hierarchy.
        raise NotificationServiceError(f"Failed to send email to {to}: {exc}") from exc
