"""
File storage backed by SeaweedFS, reached through its S3-compatible Filer
gateway (docs/System_Architecture.md §2, docs/File_System_Structure.md §1).

One bucket per file type (PLAN.md's confirmed decision, not the 3-bucket
grouping File_System_Structure.md §3 originally described) — every object-key
string this module builds or accepts is "{bucket_name}/{key...}", and
_storage_for_key() below resolves the owning bucket/storage backend from the
first path segment so callers never need to know which bucket a key lives in.

Only `store_raw_file` is a real write from this repo. OCR and normalized
artifacts are written by the AI service (a separate repo/container —
System_Architecture.md §2's ownership split: MinerU/AI service owns
extraction and normalization, Django owns everything else); this repo only
computes their keys ahead of time (so it can persist them on
StatementOcrResult/StatementNormalized rows) and later reads/deletes them.

No function here ever hands a client-facing URL directly into SeaweedFS —
it's never exposed publicly (System_Architecture.md §2/§10), so any user-
facing download goes through a Django view that calls get_object_stream()
and proxies the bytes itself (see StatementOcrArtifactDownloadView).
"""

import hashlib
import json

from botocore.exceptions import ClientError

from services.storage_backends import STORAGE_CLASSES

_STORAGE_BY_BUCKET = {cls().bucket_name: cls() for cls in STORAGE_CLASSES}


def _storage_for_key(object_key: str):
    """Resolves "{bucket}/{key...}" to (storage backend, key-within-bucket)."""
    bucket, _, key = object_key.partition("/")
    try:
        storage = _STORAGE_BY_BUCKET[bucket]
    except KeyError:
        raise ValueError(f"Unknown bucket in object key: {object_key!r}") from None
    return storage, key


def compute_checksum(file_bytes: bytes) -> str:
    """
    Real SHA-256 over the uploaded bytes — backs a real DB-level constraint
    (UNIQUE(user_id, checksum) on statement_files, DB_Schema.md).
    """
    return hashlib.sha256(file_bytes).hexdigest()


def raw_statement_key(user_id, statement_id, extension: str) -> str:
    """File_System_Structure.md §2: pfm-statements-raw/{user_id}/{statement_id}/original.{ext}"""
    return f"pfm-statements-raw/{user_id}/{statement_id}/original.{extension}"


def ocr_artifact_key(user_id, statement_id) -> str:
    """
    pfm-statements-ocr/{user_id}/{statement_id}/ — its own bucket rather than
    a folder under a shared "artifacts" bucket (PLAN.md's one-bucket-per-type
    decision splits File_System_Structure.md §3's single artifacts bucket in
    two). Written by the AI service, not this repo — see module docstring.
    """
    return f"pfm-statements-ocr/{user_id}/{statement_id}/"


def normalized_artifact_key(user_id, statement_id) -> str:
    """
    pfm-statements-normalized/{user_id}/{statement_id}/normalized.json — the
    other half of File_System_Structure.md §3's original artifacts bucket.
    New in PLAN.md Checkpoint 2: nothing built this key before, since
    normalized data's queryable copy already lives in Postgres
    (StatementNormalized.normalized_json) — this is only the traceable raw
    artifact, written by the AI service, not this repo.
    """
    return f"pfm-statements-normalized/{user_id}/{statement_id}/normalized.json"


def store_raw_file(object_key: str, file_bytes: bytes) -> None:
    """
    Uploads the raw statement bytes to `object_key`. The call site
    (create_statement_from_upload() in core/views/statements.py) wraps this
    in a try/except: an exception here means POST /statements fails outright
    with no StatementFile row persisted — there is no retryable "record
    created but not stored" status (PLAN.md), so this is the one call in the
    ingestion path that must fail before any row is written.
    """
    storage, key = _storage_for_key(object_key)
    storage.connection.meta.client.put_object(Bucket=storage.bucket_name, Key=key, Body=file_bytes)


def get_object_stream(object_key: str):
    """
    Opens a real byte stream for `object_key`, for Django views that proxy a
    download rather than handing the client a signed URL directly — SeaweedFS
    is never exposed publicly (System_Architecture.md §2/§10), so no URL
    pointing at it is fetchable from outside the internal network anyway.

    Returns `(botocore.response.StreamingBody, content_type)`, or `None` if
    the object doesn't exist (e.g. the AI service hasn't written it yet).
    """
    storage, key = _storage_for_key(object_key)
    client = storage.connection.meta.client
    try:
        response = client.get_object(Bucket=storage.bucket_name, Key=key)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
            return None
        raise
    return response["Body"], response.get("ContentType")


def delete_prefix(prefix: str) -> None:
    """
    Batch-deletes every object under this key prefix (used by statement
    deletion and full user deletion — File_System_Structure.md §6's "delete
    by prefix" rule).
    """
    storage, key_prefix = _storage_for_key(prefix)
    client = storage.connection.meta.client
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage.bucket_name, Prefix=key_prefix):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            client.delete_objects(Bucket=storage.bucket_name, Delete={"Objects": objects})


def get_onboarding_templates() -> list[dict]:
    """
    Reads pfm-reference-data/onboarding-templates/*.json
    (File_System_Structure.md §4) — the 3-5 hand-authored starter templates
    backing GET /budget/starter-templates. Edited out-of-band by whoever owns
    the reference data (never through this app's write paths — seeded once by
    `manage.py seed_onboarding_templates` if not already present); this
    function only reads what's already there.
    """
    storage = _STORAGE_BY_BUCKET["pfm-reference-data"]
    client = storage.connection.meta.client
    paginator = client.get_paginator("list_objects_v2")
    templates = []
    for page in paginator.paginate(Bucket=storage.bucket_name, Prefix="onboarding-templates/"):
        for obj in page.get("Contents", []):
            if not obj["Key"].endswith(".json"):
                continue
            body = client.get_object(Bucket=storage.bucket_name, Key=obj["Key"])["Body"].read()
            templates.append(json.loads(body))
    return templates
