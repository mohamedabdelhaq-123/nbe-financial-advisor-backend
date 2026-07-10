"""Shared fixtures for the (non-integration) test suite."""

import pytest
from moto import mock_aws


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
