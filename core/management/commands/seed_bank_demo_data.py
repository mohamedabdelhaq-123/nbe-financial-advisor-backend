"""Seeds mock-bank-sync and, via the real bank-login flow, this backend
itself with demo bank customers — local-dev-only (guarded by
settings.DEBUG), makes real HTTP calls to the sibling mock-bank-oauth and
mock-bank-sync services rather than writing their data directly, and every
run flushes its own previously-seeded rows first so reruns are
reproducible.

Produces two kinds of demo identity:
  --unlinked N  mock bank customers that exist only in mock-bank-sync's
                ledger, with no Django User yet — for exercising the link/
                bank-login flow starting from a fresh customer.
  --linked N    demo users registered in both systems: a mock-bank-sync
                customer plus a real Django User/BankConnection/BankAccount,
                produced by actually driving the OAuth+OTP round trip
                (the same sequence tests/integration/test_bank_integration_live.py
                exercises), not by writing rows directly — so the resulting
                state is exactly what a real login would produce.
"""

import re
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from rest_framework.test import APIClient

from core.models import Transaction, User

UNLINKED_ID_PREFIX = "seed-bank-demo-unlinked-"
LINKED_ID_PREFIX = "seed-bank-demo-linked-"


class Command(BaseCommand):
    help = "Seed demo bank customers (linked and unlinked) for manual testing (local dev only)."

    _TIMEOUT_SECONDS = 10
    _BACKFILL_TIMEOUT_SECONDS = 10
    _BACKFILL_POLL_INTERVAL_SECONDS = 0.5

    def add_arguments(self, parser):
        parser.add_argument(
            "--unlinked",
            type=int,
            default=2,
            help="Number of mock-bank-sync-only customers to seed (no Django user).",
        )
        parser.add_argument(
            "--linked",
            type=int,
            default=2,
            help="Number of demo users to seed registered in both systems.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Bypass the DEBUG-only guardrail.",
        )

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to seed bank demo data: settings.DEBUG is False. "
                "Pass --force to override."
            )

        unlinked_count = options["unlinked"]
        linked_count = options["linked"]

        self._check_service_health(settings.MOCK_BANK_OAUTH_SERVICE_URL, "mock-bank-oauth")
        self._check_service_health(settings.MOCK_BANK_SYNC_SERVICE_URL, "mock-bank-sync")

        # Sweeps a fixed range rather than just this run's requested counts,
        # so a smaller --linked/--unlinked on a later run still cleans up
        # rows a larger previous run left behind.
        sweep_count = max(unlinked_count, linked_count, 10)
        self._flush(sweep_count)

        unlinked = self._seed_unlinked(unlinked_count)
        linked = self._seed_linked(linked_count)

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {len(unlinked)} unlinked mock customers and "
                f"{len(linked)} linked demo users."
            )
        )
        self.stdout.write("\nUnlinked (mock-bank-sync only, no Django user yet):")
        for customer in unlinked:
            self.stdout.write(
                f"  {customer['customer_bank_id']}  {customer['email']}  "
                f"account {customer['masked_account_number']}"
            )
        self.stdout.write(
            "\nLinked (registered in both systems — sign back in with no "
            "password via POST /auth/bank-login/initiate/):"
        )
        for customer in linked:
            self.stdout.write(
                f"  {customer['customer_bank_id']}  {customer['email']}  "
                f"user_id={customer['user_id']}"
            )

    # ------------------------------------------------------------------
    # Preconditions
    # ------------------------------------------------------------------

    def _check_service_health(self, base_url, label):
        """Fails fast with an actionable message if a sibling mock service
        isn't reachable, rather than surfacing an opaque connection error
        partway through seeding."""
        url = f"{base_url}/health"
        try:
            response = requests.get(url, timeout=self._TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise CommandError(
                f"Could not reach {label} at {url}: {exc}. Is it running? "
                f"(`docker compose up -d {label}`)"
            ) from exc
        if not response.ok:
            raise CommandError(f"{label} health check at {url} returned {response.status_code}")

    # ------------------------------------------------------------------
    # Flush
    # ------------------------------------------------------------------

    def _flush(self, sweep_count):
        """Deletes only rows this command owns. The Django side is deleted
        first — local, fast, no network dependency — cascading through
        BankConnection/BankAccount/Transaction, so a failure partway through
        the mock-bank-sync sweep below can leave at worst a harmless stale
        mock-ledger row, never a BankConnection pointing at a since-deleted
        mock customer."""
        User.objects.filter(email__startswith=LINKED_ID_PREFIX).delete()
        for i in range(sweep_count):
            self._delete_mock_customer(f"{UNLINKED_ID_PREFIX}{i}")
            self._delete_mock_customer(f"{LINKED_ID_PREFIX}{i}")

    def _delete_mock_customer(self, customer_bank_id):
        """Best-effort cleanup of one mock-bank-sync customer — a missing
        or already-deleted id is expected on most sweeps, not an error."""
        try:
            response = requests.delete(
                f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer/{customer_bank_id}",
                timeout=self._TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            self.stderr.write(
                f"  warning: failed to delete mock customer {customer_bank_id!r}: {exc}"
            )
            return
        if response.status_code not in (204, 404):
            self.stderr.write(
                f"  warning: failed to delete mock customer {customer_bank_id!r} "
                f"(status {response.status_code})"
            )

    # ------------------------------------------------------------------
    # Unlinked customers (mock-bank-sync only)
    # ------------------------------------------------------------------

    def _seed_unlinked(self, count):
        customers = []
        for i in range(count):
            customer_bank_id = f"{UNLINKED_ID_PREFIX}{i}"
            email = f"{UNLINKED_ID_PREFIX}{i}@example.com"
            customer = self._create_mock_customer(
                customer_bank_id, email, f"Seed Unlinked Customer {i}"
            )
            account = customer["accounts"][0]
            self._seed_starter_transaction(account["external_account_id"])
            customers.append(
                {
                    "customer_bank_id": customer_bank_id,
                    "email": email,
                    "masked_account_number": account["masked_account_number"],
                }
            )
        return customers

    def _create_mock_customer(self, customer_bank_id, email, name):
        """Seeds one mock bank customer plus a starter checking account
        directly against the mock-bank-sync ledger."""
        response = requests.post(
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/customer",
            json={"customer_bank_id": customer_bank_id, "email": email, "name": name},
            timeout=self._TIMEOUT_SECONDS,
        )
        if response.status_code != 201:
            raise CommandError(
                f"POST /simulate/customer for {customer_bank_id!r} returned "
                f"{response.status_code}: {response.text[:300]}"
            )
        return response.json()

    def _seed_starter_transaction(self, external_account_id):
        """Pushes one transaction onto a mock account via
        /simulate/transaction, always with an explicit account_id — omitting
        it picks a uniformly random account across the entire mock ledger,
        not one scoped to the customer just created."""
        response = requests.post(
            f"{settings.MOCK_BANK_SYNC_SERVICE_URL}/simulate/transaction",
            json={"account_id": external_account_id},
            timeout=self._TIMEOUT_SECONDS,
        )
        if response.status_code != 201:
            raise CommandError(
                f"POST /simulate/transaction for account {external_account_id!r} "
                f"returned {response.status_code}: {response.text[:300]}"
            )

    # ------------------------------------------------------------------
    # Linked users (registered in both systems)
    # ------------------------------------------------------------------

    def _seed_linked(self, count):
        api_client = APIClient()
        results = []
        for i in range(count):
            customer_bank_id = f"{LINKED_ID_PREFIX}{i}"
            email = f"{LINKED_ID_PREFIX}{i}@example.com"
            customer = self._create_mock_customer(
                customer_bank_id, email, f"Seed Linked Customer {i}"
            )
            account = customer["accounts"][0]
            self._seed_starter_transaction(account["external_account_id"])

            initiate = api_client.post("/auth/bank-login/initiate/", {"provider_slug": "mock_bank"})
            if initiate.status_code != 201:
                raise CommandError(
                    f"POST /auth/bank-login/initiate/ returned {initiate.status_code}: "
                    f"{initiate.data}"
                )
            code = self._complete_oauth_otp_dance(initiate.data["authorize_url"], customer_bank_id)
            callback = api_client.post(
                "/auth/bank-login/callback/", {"code": code, "state": initiate.data["state"]}
            )
            if callback.status_code not in (200, 201):
                raise CommandError(
                    f"POST /auth/bank-login/callback/ returned {callback.status_code}: "
                    f"{callback.data}"
                )
            user_id = callback.data["user_id"]
            self._wait_for_backfill(user_id)
            results.append(
                {"customer_bank_id": customer_bank_id, "email": email, "user_id": user_id}
            )
        return results

    def _complete_oauth_otp_dance(self, authorize_url, customer_bank_id):
        """Drives the real OAuth+OTP round trip against the running
        mock-bank-oauth container and returns the single-use authorization
        code — the same sequence
        tests/integration/test_bank_integration_live.py exercises, reused
        here so a demo user is produced by the real flow rather than direct
        DB writes."""
        authorize_response = requests.get(authorize_url, timeout=self._TIMEOUT_SECONDS)
        if authorize_response.status_code != 200:
            raise CommandError(f"GET {authorize_url} returned {authorize_response.status_code}")
        challenge_match = re.search(r'name="challenge_id" value="([^"]+)"', authorize_response.text)
        if not challenge_match:
            raise CommandError(
                f"Could not find challenge_id on the authorize page at {authorize_url}"
            )
        challenge_id = challenge_match.group(1)

        login_start_url = f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/login/start"
        login_start_response = requests.post(
            login_start_url,
            data={"challenge_id": challenge_id, "customer_bank_id": customer_bank_id},
            timeout=self._TIMEOUT_SECONDS,
        )
        # 502 is what this dev stack's placeholder Gmail credentials produce
        # when mock-bank-oauth tries to email the OTP — the OTP itself is
        # already generated and stored before that email attempt, so a 502
        # here is expected, not a failure.
        if login_start_response.status_code not in (200, 502):
            raise CommandError(
                f"POST {login_start_url} returned {login_start_response.status_code}: "
                f"{login_start_response.text[:300]}"
            )

        debug_url = f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/debug/challenges/{challenge_id}"
        debug_response = requests.get(debug_url, timeout=self._TIMEOUT_SECONDS)
        if debug_response.status_code == 404:
            raise CommandError(
                f"GET {debug_url} returned 404 — either "
                "MOCK_BANK_OAUTH_ENABLE_DEBUG_ENDPOINTS=1 is not set on the "
                "mock-bank-oauth service, or this challenge has already "
                "expired. This command reads the OTP via that debug "
                "endpoint instead of real email delivery, so it must be "
                "enabled."
            )
        if debug_response.status_code != 200:
            raise CommandError(f"GET {debug_url} returned {debug_response.status_code}")
        otp = debug_response.json()["otp"]

        verify_url = f"{settings.MOCK_BANK_OAUTH_SERVICE_URL}/login/verify"
        verify_response = requests.post(
            verify_url,
            data={"challenge_id": challenge_id, "otp": otp},
            timeout=self._TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if verify_response.status_code != 302:
            raise CommandError(
                f"POST {verify_url} returned {verify_response.status_code}: "
                f"{verify_response.text[:300]}"
            )
        code_match = re.search(r"code=([^&]+)", verify_response.headers["Location"])
        if not code_match:
            raise CommandError(
                f"Could not find an authorization code in the redirect from {verify_url}"
            )
        return code_match.group(1)

    def _wait_for_backfill(self, user_id):
        """Polls for the starter transaction landing via the async
        ingest_synced_transactions Celery task — the bank-login callback's
        own DB write is synchronous, but the transaction backfill it
        triggers runs through the real broker/celery-worker, not inline."""
        deadline = time.monotonic() + self._BACKFILL_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if Transaction.objects.filter(
                account__connection__user_id=user_id, source="synced"
            ).exists():
                return
            time.sleep(self._BACKFILL_POLL_INTERVAL_SECONDS)
        self.stderr.write(
            f"  warning: backfill still pending for user_id={user_id} after "
            f"{self._BACKFILL_TIMEOUT_SECONDS}s — confirm the celery-worker "
            "service is running"
        )
