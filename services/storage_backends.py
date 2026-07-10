"""
django-storages backend classes for SeaweedFS, reached through its
S3-compatible Filer gateway (docs/System_Architecture.md §2,
docs/File_System_Structure.md).

One bucket per file type (PLAN.md Checkpoint 0's confirmed decision, not the
3-bucket grouping docs/File_System_Structure.md §3 originally described) —
raw uploads, OCR artifacts, and normalized artifacts each get their own
bucket instead of sharing one "artifacts" bucket. Each subclass below only
needs to set `bucket_name`; connection details are shared via the base class
so there is exactly one place that reads the SEAWEED_* env vars.
"""

import os

from storages.backends.s3boto3 import S3Boto3Storage

_SEAWEED_S3_ENDPOINT = os.environ.get("SEAWEED_S3_ENDPOINT")
_SEAWEED_ACCESS_KEY = os.environ.get("SEAWEED_ACCESS_KEY")
_SEAWEED_SECRET_KEY = os.environ.get("SEAWEED_SECRET_KEY")


class _SeaweedBucketStorage(S3Boto3Storage):
    """Shared connection config for a single SeaweedFS bucket. Not used directly."""

    endpoint_url = _SEAWEED_S3_ENDPOINT
    access_key = _SEAWEED_ACCESS_KEY
    secret_key = _SEAWEED_SECRET_KEY
    # SeaweedFS's S3 gateway doesn't support virtual-hosted-style bucket
    # addressing (https://bucket.host/key) — only path-style (https://host/bucket/key).
    addressing_style = "path"
    signature_version = "s3v4"
    # SeaweedFS doesn't have real AWS regions; pinned so boto3 doesn't fall
    # back to whatever region config happens to be on the host/CI runner.
    region_name = "us-east-1"
    file_overwrite = False


class RawStatementStorage(_SeaweedBucketStorage):
    """pfm-statements-raw — raw uploaded documents (File_System_Structure.md §2)."""

    bucket_name = "pfm-statements-raw"


class OcrArtifactStorage(_SeaweedBucketStorage):
    """pfm-statements-ocr — MinerU OCR output (File_System_Structure.md §3)."""

    bucket_name = "pfm-statements-ocr"


class NormalizedArtifactStorage(_SeaweedBucketStorage):
    """pfm-statements-normalized — LLM-adjusted normalized output (File_System_Structure.md §3)."""

    bucket_name = "pfm-statements-normalized"


class ReferenceDataStorage(_SeaweedBucketStorage):
    """pfm-reference-data — budget/onboarding templates (File_System_Structure.md §4)."""

    bucket_name = "pfm-reference-data"
