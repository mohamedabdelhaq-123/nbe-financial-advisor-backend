"""
Integration tests against a real, running SeaweedFS instance — the
counterpart to tests/test_file_storage.py's moto-backed unit tests. Not run
by default (see the `integration` marker in pyproject.toml and
.github/workflows/ci.yml, which passes -m "not integration"); run explicitly
with:

    docker compose run --rm backend pytest -m integration
"""

import json
import uuid

import pytest

from services import file_storage

pytestmark = pytest.mark.integration


def test_store_and_stream_round_trip():
    user_id = f"test-user-{uuid.uuid4()}"
    statement_id = str(uuid.uuid4())
    key = file_storage.raw_statement_key(user_id, statement_id, "pdf")

    file_storage.store_raw_file(key, b"integration test bytes")
    stream, _content_type = file_storage.get_object_stream(key)
    assert stream.read() == b"integration test bytes"

    file_storage.delete_prefix(f"pfm-statements-raw/{user_id}/{statement_id}/")
    assert file_storage.get_object_stream(key) is None


def test_delete_prefix_only_removes_matching_statement():
    user_id = f"test-user-{uuid.uuid4()}"
    keep_key = file_storage.raw_statement_key(user_id, "keep-me", "pdf")
    delete_key = file_storage.raw_statement_key(user_id, "delete-me", "pdf")
    file_storage.store_raw_file(keep_key, b"keep")
    file_storage.store_raw_file(delete_key, b"delete")

    file_storage.delete_prefix(f"pfm-statements-raw/{user_id}/delete-me/")

    assert file_storage.get_object_stream(delete_key) is None
    stream, _content_type = file_storage.get_object_stream(keep_key)
    assert stream.read() == b"keep"

    file_storage.delete_prefix(f"pfm-statements-raw/{user_id}/keep-me/")


def test_get_onboarding_templates_reads_real_objects():
    template_key = f"integration-test-{uuid.uuid4()}"
    template = {"template_key": template_key, "name": "Integration Test", "allocations": []}
    storage = file_storage._STORAGE_BY_BUCKET["pfm-reference-data"]
    client = storage.connection.meta.client
    object_key = f"onboarding-templates/{template_key}.json"
    client.put_object(
        Bucket="pfm-reference-data", Key=object_key, Body=json.dumps(template).encode()
    )

    try:
        templates = file_storage.get_onboarding_templates()
        assert template in templates
    finally:
        client.delete_object(Bucket="pfm-reference-data", Key=object_key)
