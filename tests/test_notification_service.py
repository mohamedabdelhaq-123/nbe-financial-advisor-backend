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


class _FakeUser:
    email = "customer@example.com"


def test_notify_happy_path_sends_the_email(db):
    notification_service.notify(_FakeUser(), "Subject", "Body")

    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["customer@example.com"]
    assert mail.outbox[0].subject == "Subject"


def test_notify_swallows_send_failures(monkeypatch):
    def _raise(*args, **kwargs):
        raise SMTPException("connection refused")

    monkeypatch.setattr(notification_service, "send_mail", _raise)

    # Doesn't raise — this is the whole point of notify() vs. send_email().
    notification_service.notify(_FakeUser(), "Subject", "Body")
    assert len(mail.outbox) == 0
