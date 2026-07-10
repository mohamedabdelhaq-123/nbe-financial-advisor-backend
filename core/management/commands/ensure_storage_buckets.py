"""
Idempotently creates the SeaweedFS buckets this app writes to (PLAN.md
Checkpoint 1). Safe to run on every startup, same as `migrate` — a rerun
against buckets that already exist is a no-op, not an error.
"""

from botocore.exceptions import ClientError
from django.core.management.base import BaseCommand

from services.storage_backends import STORAGE_CLASSES


class Command(BaseCommand):
    help = "Create the SeaweedFS buckets this app writes to, if they don't already exist."

    def handle(self, *args, **options):
        for storage_class in STORAGE_CLASSES:
            storage = storage_class()
            bucket = storage.bucket_name
            client = storage.connection.meta.client
            try:
                client.create_bucket(Bucket=bucket)
                self.stdout.write(self.style.SUCCESS(f"Created bucket: {bucket}"))
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") == "BucketAlreadyOwnedByYou":
                    self.stdout.write(f"Bucket already exists: {bucket}")
                else:
                    raise
