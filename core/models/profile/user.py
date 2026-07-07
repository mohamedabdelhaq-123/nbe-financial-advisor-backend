import uuid

from django.db import models


class UserQuerySet(models.QuerySet):
    def with_active_profile_context(self):
        """
        Optimizes fetching a user alongside their live preferences,
        active budget, and active bank accounts to prevent N+1 queries.
        """
        return self.select_related("preferences", "budget").prefetch_related(
            models.Prefetch(
                "bank_accounts",
                queryset=models.ForeignKey(
                    "BankAccount", on_delete=models.CASCADE
                ).target_field.model.objects.filter(is_active=True),
                to_attr="active_accounts",
            )
        )


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    password_hash = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True, null=True)
    employment_status = models.CharField(max_length=50, blank=True, null=True)
    income_bracket = models.CharField(max_length=50, blank=True, null=True)
    monthly_income = models.DecimalField(
        max_length=14, decimal_places=2, max_digits=20, blank=True, null=True
    )
    income_steadiness = models.CharField(max_length=20, blank=True, null=True)
    dependents_count = models.SmallIntegerField(default=0)
    onboarding_date = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, default="active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserQuerySet.as_manager()

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.name} ({self.email})"

    @property
    def has_active_consent(self):
        """Quick check to see if the user's latest consent state is valid."""
        latest_consent = self.consent_records.order_by("-created_at").first()
        return (
            latest_consent is not None
            and latest_consent.granted_at is not None
            and latest_consent.revoked_at is None
        )
