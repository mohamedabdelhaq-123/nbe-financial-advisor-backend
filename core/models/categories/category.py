import uuid

from django.db import models


class Category(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=100)
    category_type = models.CharField(max_length=20)  # "income" | "expense"
    is_fallback = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "categories"
        constraints = [
            # At most one fallback row per type (FR-008-style guarantee, now
            # enforced by the DB instead of just a comment): resolve_category()
            # relies on there being exactly one to fall back to per direction.
            models.UniqueConstraint(
                fields=["category_type"],
                condition=models.Q(is_fallback=True),
                name="unique_fallback_per_type",
            )
        ]

    def __str__(self):
        return f"{self.label} ({self.category_type})"
