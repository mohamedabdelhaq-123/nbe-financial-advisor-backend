"""
Unit tests for services/notification_service.py. Django's test environment
(pytest-django's session setup) already swaps EMAIL_BACKEND for the locmem
backend, so send_email()'s happy path is asserted against django.core.mail.outbox
directly — no monkeypatching needed there, only for the failure path.
"""

from smtplib import SMTPException

import pytest
from django.core import mail

from services import notification_service


def test_send_email_happy_path():
    notification_service.send_email("customer@example.com", "Your code", "123456")

    assert len(mail.outbox) == 1
    sent = mail.outbox[0]
    assert sent.to == ["customer@example.com"]
    assert sent.subject == "Your code"
    assert sent.body == "123456"


def test_send_email_raises_notification_service_error_on_smtp_failure(monkeypatch):
    def _raise(*args, **kwargs):
        raise SMTPException("connection refused")

    monkeypatch.setattr(notification_service, "send_mail", _raise)

    with pytest.raises(notification_service.NotificationServiceError):
        notification_service.send_email("customer@example.com", "Your code", "123456")
