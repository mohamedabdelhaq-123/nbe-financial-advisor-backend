"""
Auth-flow emails that must not run inline in the request/response cycle,
because doing so leaks whether an account exists: PasswordResetRequestView
always responds 202 immediately regardless of whether `email` matched a
real account, but an inline `notification_service.send_email()` call only
ever happens on the match branch — the extra time that SMTP round-trip
takes is itself a timing side-channel an attacker could use to enumerate
registered emails, the same class of leak LoginView's generic error message
and constant-time comparison are already there to prevent. Moving the send
into a Celery task closes that gap: both branches of the view now return
after the same fast, constant-time work (either nothing, or a `.delay()`
call that just enqueues to the broker).
"""

from celery import shared_task
from django.template.loader import render_to_string

from services import notification_service


@shared_task
def send_password_reset_email(to: str, link: str) -> None:
    try:
        notification_service.send_email(
            to,
            "Reset your password",
            "Reset your password by visiting the link below:\n\n"
            f"{link}\n\n"
            "If you didn't request this, you can ignore this email.",
            html_body=render_to_string("emails/reset_password.html", {"link": link}),
        )
    except notification_service.NotificationServiceError:
        # Best-effort, same as every other notification send in this
        # codebase — see core/views/auth.py's other send_email call sites.
        pass
