import uuid

from django.db import models


class AdminUser(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    email = models.EmailField(max_length=255, unique=True)
    password_hash = models.CharField(max_length=255)
    role = models.CharField(
        max_length=50, default="reviewer"
    )  # e.g., 'reviewer', 'manager', 'admin'
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "admin_users"

    def __str__(self):
        return f"Staff: {self.name} ({self.role})"

    @property
    def is_super_admin(self):
        """Quick operational check for high-clearance management actions."""
        return self.role == "admin"
