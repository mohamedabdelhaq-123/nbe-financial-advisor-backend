import uuid

from django.contrib.postgres.fields import ArrayField
from django.db import models


class Product(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, null=True)
    categories = ArrayField(models.TextField(), default=list)  # Maps to TEXT[]
    tags = ArrayField(models.TextField(), default=list)  # Maps to TEXT[]
    features = models.JSONField(blank=True, null=True)  # Native JSONB payload
    external_link = models.CharField(max_length=500, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "products"

    def __str__(self):
        return self.title
