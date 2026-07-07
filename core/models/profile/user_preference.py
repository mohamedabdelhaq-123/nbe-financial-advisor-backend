import uuid

from django.db import models


class UserPreference(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField("User", on_delete=models.CASCADE, related_name="preferences")
    language = models.CharField(max_length=10, default="en")
    currency_display_format = models.CharField(max_length=20, default="symbol")
    date_format = models.CharField(max_length=20, default="DD/MM/YYYY")
    budget_cycle_start_day = models.SmallIntegerField(default=1)
    default_view = models.CharField(max_length=20, default="monthly")
    retain_raw_documents = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "user_preferences"

    def __str__(self):
        return f"Preferences for {self.user_id}"
