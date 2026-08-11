"""One-off dev helper: registers a single mock bank customer in mock-bank-sync
for a given email, with a starter checking account and a handful of
transactions — so that email can go through the real bank-login/
bank-connection OAuth+OTP flow end to end, and land with enough transaction
history to look like a real account instead of an empty one. Mirrors
seed_bank_demo_data.py's _create_mock_customer / _seed_starter_transaction,
but for one caller-supplied identity instead of a numbered demo batch, and it
doesn't flush/own any rows the way that command does — it only ever creates
the one customer requested.
"""

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

_STARTER_TRANSACTION_COUNT = 15


class Command(BaseCommand):
    help = (
        "Register one mock bank customer (by email) in mock-bank-sync, with a "
        "starter account and transaction history, for exercising the "
        "bank-login/bank-connection flow against a real inbox (local dev only)."
    )

    _TIMEOUT_SECONDS = 10

    def add_arguments(self, parser):
        parser.add_argument(
            "--email", required=True, help="Customer email — also used as customer_bank_id."
        )

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError(
                "Refusing to register a mock bank customer: settings.DEBUG is False."
            )

        email = options["email"]

        self._check_service_health(settings.MOCK_BANK_SYNC_SERVICE_URL, "mock-bank-sync")

        response = requests.post(
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer",
            json={"customer_bank_id": email, "email": email},
            timeout=self._TIMEOUT_SECONDS,
        )
        if response.status_code == 409:
            # Already registered — DELETE cascades its accounts/transactions
            # (mock-bank-sync's own cascade="all, delete-orphan"), so this is
            # a clean re-seed rather than an error, matching "remove it and
            # redo it" instead of failing on a re-run.
            self.stdout.write(f"customer_bank_id={email!r} already exists — replacing it.")
            self._delete_existing_customer(email)
            response = requests.post(
                f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer",
                json={"customer_bank_id": email, "email": email},
                timeout=self._TIMEOUT_SECONDS,
            )
        if response.status_code != 201:
            raise CommandError(
                f"POST /simulate/customer returned {response.status_code}: "
                f"{response.text[:300]}"
            )
        customer = response.json()
        account = customer["accounts"][0]

        for _ in range(_STARTER_TRANSACTION_COUNT):
            transaction_response = requests.post(
                f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/transaction",
                json={"account_id": account["external_account_id"]},
                timeout=self._TIMEOUT_SECONDS,
            )
            if transaction_response.status_code != 201:
                raise CommandError(
                    f"POST /simulate/transaction returned "
                    f"{transaction_response.status_code}: {transaction_response.text[:300]}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"Registered mock bank customer {email!r} with account "
                f"{account['masked_account_number']} and "
                f"{_STARTER_TRANSACTION_COUNT} transactions."
            )
        )
        self.stdout.write(
            "Next: in the app (logged in as the NBE user who should own this "
            "link), use 'Connect bank' — the mock bank login screen takes "
            f"customer_bank_id={email!r}, and the OTP will be sent for real "
            f"to {email}."
        )

    def _delete_existing_customer(self, customer_bank_id):
        response = requests.delete(
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer/{customer_bank_id}",
            timeout=self._TIMEOUT_SECONDS,
        )
        if response.status_code not in (204, 404):
            raise CommandError(
                f"DELETE /simulate/customer/{customer_bank_id} returned "
                f"{response.status_code}: {response.text[:300]}"
            )

    def _check_service_health(self, base_url, label):
        """Fails fast with an actionable message if mock-bank-sync isn't
        reachable, rather than surfacing an opaque connection error partway
        through registration."""
        url = f"{base_url}/health"
        try:
            resp = requests.get(url, timeout=self._TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise CommandError(
                f"Could not reach {label} at {url}: {exc}. Is it running? "
                f"(`docker compose up -d {label}`)"
            ) from exc
        if not resp.ok:
            raise CommandError(f"{label} health check at {url} returned {resp.status_code}")
