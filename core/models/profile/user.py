import uuid

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models


class UserQuerySet(models.QuerySet):
    def with_active_profile_context(self):
        """
        Optimizes fetching a user alongside their live preferences,
        active budget, and active bank accounts to prevent N+1 queries.
        """
        # Local import: profile.bank_account doesn't import this module, so no
        # cycle, but importing at module load time would run before the app
        # registry has finished populating during Django's model-loading pass.
        from core.models.profile.bank_account import BankAccount

        return self.select_related("preferences", "budget").prefetch_related(
            models.Prefetch(
                "bank_accounts",
                queryset=BankAccount.objects.filter(is_active=True),
                to_attr="active_accounts",
            )
        )


class UserManager(BaseUserManager.from_queryset(UserQuerySet)):
    """
    Required by AbstractBaseUser — Django has no default manager that knows
    how to create a `core.User` (email-keyed, no `username` field). Built on
    top of UserQuerySet (via from_queryset) so `.with_active_profile_context()`
    stays available as `User.objects.with_active_profile_context()`.
    """

    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        # Only used for `manage.py createsuperuser` (Django admin access for devs/ops) —
        # ordinary product signups always go through create_user() with is_staff=False.
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")
        return self._create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """
    AUTH_USER_MODEL for the project (see PLAN.md Checkpoint 0). Chosen over a
    hand-rolled JWT/auth scheme so simplejwt's standard token views and DRF's
    IsAuthenticated/JWTAuthentication work with no custom auth plumbing.

    Deliberate deviations from docs/DB_Schema.md's `users` table, and why:
      - `password` (inherited from AbstractBaseUser) stays as Django's default
        column name rather than being forced to `password_hash` — the column
        is never exposed via any API response in the Data Shapes docs, so
        there's nothing to gain from literal name parity, and DB_Schema.md's
        `password_hash` is now this field.
      - `is_staff` is added (not in DB_Schema.md) purely to let `createsuperuser`
        gate access to Django's built-in /admin/ site for devs/ops. Ordinary
        end users are never given is_staff=True — the product's actual admin
        surface is the separate `AdminUser` model/credential space.
      - `is_superuser`, `groups`, `user_permissions`, `last_login` are added by
        PermissionsMixin/AbstractBaseUser — unused by the product's own
        permission logic (which is role/ownership-based, not Django group
        permissions), but harmless and required by Django's auth internals.
      - `is_active` is not a separate column: overridden below as a property
        derived from the existing `status` field, so authentication eligibility
        tracks the one field the product already uses for suspension/deletion
        instead of duplicating that concept in a second column.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    employment_status = models.CharField(max_length=50, blank=True, null=True)
    income_bracket = models.CharField(max_length=50, blank=True, null=True)
    # max_digits corrected to 14 to match docs/DB_Schema.md's NUMERIC(14,2) — the
    # previous max_digits=20 was inconsistent with the documented schema, and the
    # max_length=14 kwarg previously here was a no-op (Field.max_length exists on
    # the base Field class but DecimalField's validation never reads it).
    monthly_income = models.DecimalField(max_digits=14, decimal_places=2, blank=True, null=True)
    income_steadiness = models.CharField(max_length=20, blank=True, null=True)
    dependents_count = models.SmallIntegerField(default=0)
    onboarding_date = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=20, default="active")  # active | suspended | deleted
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    is_staff = models.BooleanField(default=False)  # Django /admin/ site gate only — see docstring
    # Informational only — nothing gates login on this today (PLAN.md
    # Checkpoint 5). Set True by EmailVerificationConfirmView once the user
    # clicks the link from their signup verification email.
    email_verified = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "users"

    def __str__(self):
        return f"{self.name} ({self.email})"

    @property
    def is_active(self):
        # Ties Django's authentication-eligibility check to the product's own
        # `status` field instead of adding a redundant is_active column.
        return self.status == "active"

    @property
    def has_active_consent(self):
        """Quick check to see if the user's latest consent state is valid."""
        latest_consent = self.consent_records.order_by("-created_at").first()
        return (
            latest_consent is not None
            and latest_consent.granted_at is not None
            and latest_consent.revoked_at is None
        )
