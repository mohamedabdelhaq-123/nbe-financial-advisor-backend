"""
Unit tests for services/file_storage.py, backed by moto's mocked S3 rather
than a real SeaweedFS instance — fast, no network, runs in every CI push.
See tests/integration/test_file_storage_live.py for the smaller set of tests
that exercise the real thing.
"""

import json

import pytest

from services import file_storage


def test_compute_checksum_is_real_sha256():
    assert (
        file_storage.compute_checksum(b"hello")
        == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    )


def test_compute_checksum_differs_for_different_bytes():
    assert file_storage.compute_checksum(b"a") != file_storage.compute_checksum(b"b")


def test_raw_statement_key_shape():
    assert (
        file_storage.raw_statement_key("user1", "stmt1", "pdf")
        == "pfm-statements-raw/user1/stmt1/original.pdf"
    )


def test_ocr_artifact_key_shape():
    assert file_storage.ocr_artifact_key("user1", "stmt1") == "pfm-statements-ocr/user1/stmt1/"


def test_normalized_artifact_key_shape():
    assert (
        file_storage.normalized_artifact_key("user1", "stmt1")
        == "pfm-statements-normalized/user1/stmt1/normalized.json"
    )


def test_storage_for_key_raises_on_unknown_bucket():
    with pytest.raises(ValueError, match="Unknown bucket"):
        file_storage._storage_for_key("not-a-real-bucket/some/key")


def test_store_raw_file_and_read_it_back(moto_storage):
    key = file_storage.raw_statement_key("user1", "stmt1", "pdf")
    file_storage.store_raw_file(key, b"raw statement bytes")

    stream, _content_type = file_storage.get_object_stream(key)
    assert stream.read() == b"raw statement bytes"


def test_get_object_stream_returns_none_for_missing_key(moto_storage):
    key = file_storage.raw_statement_key("user1", "does-not-exist", "pdf")
    assert file_storage.get_object_stream(key) is None


def test_delete_prefix_removes_only_matching_objects(moto_storage):
    client = moto_storage["pfm-statements-raw"].connection.meta.client
    client.put_object(Bucket="pfm-statements-raw", Key="user1/stmt1/original.pdf", Body=b"a")
    client.put_object(Bucket="pfm-statements-raw", Key="user2/stmt2/original.pdf", Body=b"b")

    file_storage.delete_prefix("pfm-statements-raw/user1/stmt1/")

    remaining = client.list_objects_v2(Bucket="pfm-statements-raw").get("Contents", [])
    assert [obj["Key"] for obj in remaining] == ["user2/stmt2/original.pdf"]


def test_get_onboarding_templates_reads_and_parses_real_objects(moto_storage):
    client = moto_storage["pfm-reference-data"].connection.meta.client
    template = {"template_key": "balanced", "name": "Balanced", "allocations": []}
    client.put_object(
        Bucket="pfm-reference-data",
        Key="onboarding-templates/balanced.json",
        Body=json.dumps(template).encode(),
    )

    assert file_storage.get_onboarding_templates() == [template]


def test_get_onboarding_templates_ignores_non_json_keys(moto_storage):
    client = moto_storage["pfm-reference-data"].connection.meta.client
    client.put_object(
        Bucket="pfm-reference-data", Key="onboarding-templates/README.txt", Body=b"not json"
    )

    assert file_storage.get_onboarding_templates() == []
