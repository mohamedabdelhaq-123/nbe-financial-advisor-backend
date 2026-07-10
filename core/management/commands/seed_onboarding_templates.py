"""
One-time seed for pfm-reference-data/onboarding-templates/*.json
(File_System_Structure.md §4) — the 3 starter templates backing
GET /budget/starter-templates (services/file_storage.py::get_onboarding_templates).

Skips any template that already exists rather than overwriting it: reference
data is edited out-of-band by whoever owns it (File_System_Structure.md §4 —
"read-only from the application's perspective at request time"), so a rerun
of this command must never clobber a team member's manual edit.
"""

import json

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand

from services.storage_backends import ReferenceDataStorage

# Same 3 starter templates the mock in services/file_storage.py used to
# hardcode directly — now real objects in the bucket instead.
ONBOARDING_TEMPLATES = [
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


class Command(BaseCommand):
    help = "Seed the onboarding starter templates into pfm-reference-data, if not already present."

    def handle(self, *args, **options):
        storage = ReferenceDataStorage()
        client = storage.connection.meta.client
        for template in ONBOARDING_TEMPLATES:
            key = f"onboarding-templates/{template['template_key']}.json"
            if self._exists(client, storage.bucket_name, key):
                self.stdout.write(f"Already present: {key}")
                continue
            client.put_object(
                Bucket=storage.bucket_name,
                Key=key,
                Body=json.dumps(template, indent=2).encode(),
                ContentType="application/json",
            )
            self.stdout.write(self.style.SUCCESS(f"Seeded: {key}"))

    @staticmethod
    def _exists(client, bucket: str, key: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise
