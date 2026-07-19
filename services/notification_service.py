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

from django.core.mail import EmailMultiAlternatives, send_mail


class NotificationServiceError(Exception):
    """Raised for any email-send failure — callers catch this one type
    instead of smtplib's own exception hierarchy."""


def send_email(to: str, subject: str, body: str, html_body: str | None = None) -> None:
    """
    Sends an email via the configured Gmail SMTP account. `body` is always
    sent (either as the whole plain-text email, or as the plain-text
    fallback part of a multipart message when `html_body` is given — every
    client that can't/won't render HTML, plus spam filters that weigh a
    missing text part negatively, still gets a readable message).
    Raises NotificationServiceError on any send failure.
    """
    try:
        if html_body is not None:
            # from_email=None -> settings.DEFAULT_FROM_EMAIL.
            message = EmailMultiAlternatives(subject, body, None, [to])
            message.attach_alternative(html_body, "text/html")
            message.send(fail_silently=False)
        else:
            send_mail(subject, body, None, [to], fail_silently=False)
    except (SMTPException, OSError) as exc:
        # OSError, not just SMTPException: a connection failure (SMTP host
        # unreachable, DNS failure, connection refused/reset) surfaces as a
        # socket-level OSError, not smtplib's own exception hierarchy.
        raise NotificationServiceError(f"Failed to send email to {to}: {exc}") from exc


def notify(user, subject: str, body: str) -> None:
    """
    Best-effort email to a core.User — every "let the user know something
    happened" call site (budget changes, detected anomalies, a finished
    statement upload: PLAN.md Checkpoint 6) wants the exact same "don't fail
    the parent action over a notification that couldn't be sent" behavior
    that core/tasks/bank_sync.py implemented inline before this existed.
    Collected here once instead of repeating the try/except at every site.
    """
    try:
        send_email(user.email, subject, body)
    except NotificationServiceError:
        pass
