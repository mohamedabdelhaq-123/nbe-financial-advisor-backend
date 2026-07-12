"""Shared fixtures for the (non-integration) test suite."""

import pytest
from moto import mock_aws


@pytest.fixture(autouse=True)
def _celery_eager_mode(monkeypatch):
    """
    Forces every task (core/tasks/statements.py, core/tasks/conversations.py)
    to run synchronously, in-process, the moment .delay() is called — so an
    ordinary APIClient request exercises the whole pipeline/reply-generation
    task inline, with no live broker/worker needed.

    Monkeypatches each task's .delay to call the task directly instead —
    Task.__call__ always runs the task body synchronously in-process,
    regardless of any task_always_eager config, so this doesn't depend on
    Celery's config resolution at all. Two approaches were tried and
    rejected first: (1) celery_app.conf.task_always_eager = True after the
    fact — silently doesn't take effect on reads against celery==5.6.3,
    verified by hand (Celery's Settings/ConfigurationView stores the
    mutation in an internal `changes` overlay, but attribute/`.get()`/
    `__getitem__` reads of this specific key don't see it); (2) setting the
    CELERY_TASK_ALWAYS_EAGER env var at conftest module-import time — too
    late, since pytest-django's own django.setup() call (inside its
    pytest_load_initial_conftests hook) runs before any conftest.py module
    code gets a chance to execute.
    """
    from core.tasks.conversations import generate_chat_reply
    from core.tasks.statements import process_statement_pipeline

    for task in (process_statement_pipeline, generate_chat_reply):
        monkeypatch.setattr(task, "delay", lambda *a, _task=task, **kw: _task(*a, **kw))


@pytest.fixture
def fake_redis(monkeypatch):
    """
    Swaps services/event_bus.py's and services/sse_tickets.py's module-level
    Redis clients for one shared fakeredis instance, for the duration of one
    test — mirrors moto_storage's monkeypatch-the-module-singleton pattern
    above. Both modules must share the *same* fake instance (not one each)
    so a ticket minted via sse_tickets and an event published via event_bus
    are visible to each other exactly as they would be against one real
    Redis server.

    Imports deferred to inside this function for the same reason as
    moto_storage's: these modules read django.conf.settings at import time
    (REDIS_URL), which can't happen before pytest-django calls django.setup().
    """
    import fakeredis

    from services import event_bus, sse_tickets

    client = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(event_bus, "_redis_client", client)
    monkeypatch.setattr(sse_tickets, "_redis_client", client)
    return client


@pytest.fixture
def moto_storage(monkeypatch):
    """
    Swaps services.file_storage's storage instances for ones pointed at
    moto's mocked AWS S3, for the duration of one test.

    moto only intercepts requests shaped like real AWS endpoints (verified
    by hand while writing this suite — a custom endpoint_url, like the real
    SEAWEED_S3_ENDPOINT these classes are normally configured with, gets
    dialed for real and fails rather than being intercepted), so tests can't
    reuse the production instances built at import time in
    services/file_storage.py; this fixture builds parallel ones instead.

    Imports are deferred to inside this function (rather than at module
    level) because they touch Django settings (django-storages reads them on
    S3Boto3Storage.__init__) — importing at collection time can run before
    pytest-django has called django.setup().
    """
    from services import file_storage
    from services.storage_backends import STORAGE_CLASSES

    with mock_aws():
        test_storages = {
            cls.bucket_name: cls(
                endpoint_url=None,
                access_key="testing",
                secret_key="testing",
                region_name="us-east-1",
            )
            for cls in STORAGE_CLASSES
        }
        for storage in test_storages.values():
            storage.connection.meta.client.create_bucket(Bucket=storage.bucket_name)
        monkeypatch.setattr(file_storage, "_STORAGE_BY_BUCKET", test_storages)
        yield test_storages
