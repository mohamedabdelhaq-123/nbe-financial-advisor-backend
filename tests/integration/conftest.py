"""
Fixtures for tests that hit a real SeaweedFS instance — services/file_storage.py
functions run completely unmodified here, against whatever SEAWEED_S3_ENDPOINT
actually points at (the seaweedfs service in docker-compose.yml, when run
there).
"""

import pytest
from botocore.exceptions import ClientError


@pytest.fixture(scope="session", autouse=True)
def _ensure_buckets_exist():
    # `docker compose run backend pytest ...` bypasses the compose command:
    # chain (migrate && ensure_storage_buckets && ...), so integration tests
    # create the buckets themselves, the same idempotent way
    # manage.py ensure_storage_buckets does. Import deferred to inside this
    # function for the same reason as tests/conftest.py's moto_storage.
    from services.storage_backends import STORAGE_CLASSES

    for storage_class in STORAGE_CLASSES:
        storage = storage_class()
        client = storage.connection.meta.client
        try:
            client.create_bucket(Bucket=storage.bucket_name)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "BucketAlreadyOwnedByYou":
                raise
