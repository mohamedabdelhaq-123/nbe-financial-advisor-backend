"""
Outbound email client — this service's own Gmail SMTP account, sent
directly via stdlib smtplib rather than through the main Django backend.
This is the bank's own OTP delivery channel: a real bank owns its customer
authentication end-to-end and would never ask a relying party's
infrastructure to relay its security codes, so this service holds its own
sender credentials (MOCK_BANK_OAUTH_GMAIL_ADDRESS/MOCK_BANK_OAUTH_GMAIL_APP_PASSWORD)
and sends independently. Same shape as services/notification_service.py on
the Django side (one send_email() function, one error type), since there's
nothing Django-specific about the underlying protocol.
"""

import smtplib
from email.message import EmailMessage

from app.config import MOCK_BANK_OAUTH_GMAIL_ADDRESS, MOCK_BANK_OAUTH_GMAIL_APP_PASSWORD

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587
_SMTP_TIMEOUT_SECONDS = 10


class NotificationError(Exception):
    """Raised for any email-send failure — callers catch this one type
    instead of smtplib's own exception hierarchy."""


def send_email(to: str, subject: str, body: str) -> None:
    """Sends a plain-text email via this service's own Gmail SMTP account.
    Raises NotificationError on any send failure."""
    message = EmailMessage()
    message["From"] = MOCK_BANK_OAUTH_GMAIL_ADDRESS
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    try:
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT, timeout=_SMTP_TIMEOUT_SECONDS) as smtp:
            smtp.starttls()
            smtp.login(MOCK_BANK_OAUTH_GMAIL_ADDRESS, MOCK_BANK_OAUTH_GMAIL_APP_PASSWORD)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as exc:
        # OSError, not just SMTPException: a connection failure (SMTP host
        # unreachable, DNS failure, connection refused/reset) surfaces as a
        # socket-level OSError, not smtplib's own exception hierarchy.
        raise NotificationError(f"Failed to send email to {to}: {exc}") from exc
