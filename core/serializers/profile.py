from rest_framework import serializers

from core.models import BankAccount, ConsentRecord, User, UserPreference
from core.utils import mask_account_number


class UserSerializer(serializers.ModelSerializer):
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
            "created_at",
            "updated_at",
        ]
        # email/status/onboarding_date/created_at/updated_at/id are visible on
        # GET but silently ignored if sent in a PATCH body — email changes and
        # status transitions (suspended/deleted) aren't exposed as a plain
        # profile edit; onboarding_date is set by the onboarding flow itself,
        # not hand-edited here.
        read_only_fields = ["id", "email", "status", "onboarding_date", "created_at", "updated_at"]


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

    def to_representation(self, instance):
        # account_number stores whatever's actually known — the real number
        # for statement-derived accounts, an already-masked value for synced
        # accounts (core/utils.py's mask_account_number docstring) — masking
        # only applies on the way out, so POST/PATCH can still accept a raw
        # client-supplied value on the way in.
        data = super().to_representation(instance)
        data["account_number"] = mask_account_number(instance.account_number)
        return data


class ConsentGrantSerializer(serializers.Serializer):
    """POST /users/me/consent body — a grant event, not a full record (granted_at is server-set)."""

    consent_type = serializers.CharField(max_length=50)
    policy_version = serializers.CharField(max_length=20)


class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRecord
        fields = ["id", "consent_type", "policy_version", "granted_at", "revoked_at", "created_at"]
        read_only_fields = fields
