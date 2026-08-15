from rest_framework import serializers

from core.models import BankAccount, ConsentRecord, User, UserPreference


class UserSerializer(serializers.ModelSerializer):
    # Bank-login-created accounts (core/views/auth.py's BankLoginCallbackView)
    # are provisioned with `password=None`, i.e. Django's unusable-password
    # marker — the frontend uses this, alongside email_verified below, to
    # decide whether the "verify your email" nudge applies at all: a
    # bank-login account never goes through the emailed-link flow (no
    # verification email is ever sent on that path), so email_verified stays
    # False for it forever — has_password is what lets the frontend hide the
    # nudge for that case specifically, since bank OTP already proved the
    # identity behind the email.
    has_password = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "employment_status",
            "income_bracket",
            "monthly_income",
            "income_steadiness",
            "dependents_count",
            "onboarding_date",
            "status",
            "has_password",
            # Real model field (not computed) — settable only by
            # EmailVerificationConfirmView, never by a plain profile PATCH,
            # hence read_only below.
            "email_verified",
            "created_at",
            "updated_at",
        ]
        # email/status/onboarding_date/created_at/updated_at/id are visible on
        # GET but silently ignored if sent in a PATCH body — email changes and
        # status transitions (suspended/deleted) aren't exposed as a plain
        # profile edit; onboarding_date is set by the onboarding flow itself,
        # not hand-edited here.
        read_only_fields = [
            "id",
            "email",
            "status",
            "onboarding_date",
            "email_verified",
            "created_at",
            "updated_at",
        ]

    def get_has_password(self, obj: User) -> bool:
        return obj.has_usable_password()


class UserPreferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserPreference
        fields = [
            "language",
            "currency_display_format",
            "date_format",
            "budget_cycle_start_day",
            "default_view",
            "retain_raw_documents",
            "updated_at",
        ]
        read_only_fields = ["updated_at"]


class BankAccountSerializer(serializers.ModelSerializer):
    """`account_number` is returned exactly as stored — no display-side masking.

    It holds the full number whatever created the account: manual entry,
    statement OCR (core/tasks/statements.py's _finalize_normalization_phase()), or
    bank sync, where BankConnector.fetch_accounts()' contract
    (services/bank_connectors/base.py) requires the provider to hand back the
    real number rather than a masked hint. That uniformity is what lets a
    statement resolve onto an already-synced account instead of shadowing it
    with a duplicate.
    """

    current_balance = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = BankAccount
        fields = [
            "id",
            "bank_name",
            "account_type",
            "account_number",
            "currency",
            "is_active",
            "link_type",
            "external_account_id",
            "current_balance",
            "created_at",
        ]
        # link_type/external_account_id are backend-set (see BankConnectionCallbackView)
        # and never client-writable — a synced account's link_type also can't
        # be spoofed away from the client side to bypass assert_account_mutable().
        read_only_fields = ["id", "link_type", "external_account_id", "created_at"]


class ConsentGrantSerializer(serializers.Serializer):
    """POST /users/me/consent body — a grant event, not a full record (granted_at is server-set)."""

    consent_type = serializers.CharField(max_length=50)
    policy_version = serializers.CharField(max_length=20)


class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRecord
        fields = ["id", "consent_type", "policy_version", "granted_at", "revoked_at", "created_at"]
        read_only_fields = fields
