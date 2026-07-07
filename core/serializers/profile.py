from rest_framework import serializers

from core.models import BankAccount, ConsentRecord, User, UserPreference


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
            "masked_account_number",
            "currency",
            "is_active",
            "current_balance",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class ConsentGrantSerializer(serializers.Serializer):
    """POST /users/me/consent body — a grant event, not a full record (granted_at is server-set)."""

    consent_type = serializers.CharField(max_length=50)
    policy_version = serializers.CharField(max_length=20)


class ConsentRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsentRecord
        fields = ["id", "consent_type", "policy_version", "granted_at", "revoked_at", "created_at"]
        read_only_fields = fields
