"""
Mock stand-in for SeaweedFS (reached via its S3-compatible Filer gateway in
the real implementation — docs/System_Architecture.md §2,
docs/File_System_Structure.md §1). No real bytes are written or read by
anything in this module — functions only compute the object-key strings a
real implementation would use, per File_System_Structure.md's documented key
layout, and return synthetic values for anything that needs to look like it
points at real content (e.g. a signed URL). Swapping this for the real thing
later means replacing each function's body with an actual
django-storages/boto3 call against the same key convention already
established here — call sites elsewhere in the codebase don't change.
"""

import hashlib


def compute_checksum(file_bytes: bytes) -> str:
    """
    Real SHA-256 over the uploaded bytes — this is the one thing here that
    isn't mocked, since it backs a real DB-level constraint
    (UNIQUE(user_id, checksum) on statement_files, DB_Schema.md) and doesn't
    require actual file storage to be meaningful.
    """
    return hashlib.sha256(file_bytes).hexdigest()


def raw_statement_key(user_id, statement_id, extension: str) -> str:
    """File_System_Structure.md §2: pfm-statements-raw/{user_id}/{statement_id}/original.{ext}"""
    return f"pfm-statements-raw/{user_id}/{statement_id}/original.{extension}"


def ocr_artifact_key(user_id, statement_id) -> str:
    """File_System_Structure.md §3: pfm-statements-artifacts/{user_id}/{statement_id}/ocr/"""
    return f"pfm-statements-artifacts/{user_id}/{statement_id}/ocr/"


def get_signed_url(object_key: str) -> str:
    """
    Mock signed URL. A real implementation asks SeaweedFS's Filer gateway for
    a time-limited pre-signed URL; here it's a deterministic, non-functional
    placeholder that still encodes the real key, so it's obvious in a
    response body that this is a mock rather than a silently-broken real link.
    """
    return f"https://mock-seaweedfs.internal/{object_key}?mock-signed=true"


def delete_prefix(prefix: str) -> None:
    """
    Mock deletion. A real implementation issues a batch delete against every
    object under this key prefix (used by statement deletion and full user
    deletion — File_System_Structure.md §6's "delete by prefix" rule). No-op
    here since nothing is actually written to any backing store yet.
    """
    return None


def get_onboarding_templates() -> list[dict]:
    """
    Mock for reading pfm-reference-data/onboarding-templates/*.json
    (File_System_Structure.md §4) — the 3-5 hand-authored starter templates
    backing GET /budget/starter-templates. Real implementation reads these as
    versioned JSON files from SeaweedFS, edited out-of-band by whoever owns
    the reference data, never through the app's own write paths (§4's "read-
    only from the application's perspective at request time"); this mock
    hardcodes the same shape instead of reading from a file. Each template's
    allocations sum to exactly 100, matching the percentage convention
    (API Design Guidelines §3) every allocation set in the system follows.
    """
    return [
        {
            "template_key": "balanced",
            "name": "Balanced",
            "description": "An even split across essentials, savings, and lifestyle spending.",
            "allocations": [
                {"category": "housing", "allocated_percentage": 30},
                {"category": "food", "allocated_percentage": 15},
                {"category": "transport", "allocated_percentage": 10},
                {"category": "savings", "allocated_percentage": 20},
                {"category": "lifestyle", "allocated_percentage": 15},
                {"category": "other", "allocated_percentage": 10},
            ],
        },
        {
            "template_key": "aggressive_savings",
            "name": "Aggressive Savings",
            "description": "Minimizes discretionary spending to maximize savings rate.",
            "allocations": [
                {"category": "housing", "allocated_percentage": 30},
                {"category": "food", "allocated_percentage": 12},
                {"category": "transport", "allocated_percentage": 8},
                {"category": "savings", "allocated_percentage": 35},
                {"category": "lifestyle", "allocated_percentage": 5},
                {"category": "other", "allocated_percentage": 10},
            ],
        },
        {
            "template_key": "comfortable",
            "name": "Comfortable",
            "description": "More room for lifestyle spending, with a lighter savings target.",
            "allocations": [
                {"category": "housing", "allocated_percentage": 30},
                {"category": "food", "allocated_percentage": 15},
                {"category": "transport", "allocated_percentage": 10},
                {"category": "savings", "allocated_percentage": 10},
                {"category": "lifestyle", "allocated_percentage": 25},
                {"category": "other", "allocated_percentage": 10},
            ],
        },
    ]
