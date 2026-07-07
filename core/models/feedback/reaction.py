import uuid

from django.db import models


class Reaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="reactions")
    target_type = models.CharField(max_length=50)
    target_id = models.UUIDField()
    rating = models.SmallIntegerField(blank=True, null=True)
    comment = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reactions"
        indexes = [models.Index(fields=["target_type", "target_id"], name="idx_reactions_target")]
        constraints = [
            # Standard way to combine lookups safely without triggering deprecation warnings
            models.CheckConstraint(
                condition=models.Q(rating__range=(1, 5)),
                name="chk_reaction_rating_range",
            )
        ]

    def __str__(self):
        return f"Reaction by {self.user_id} on {self.target_type} ({self.rating}/5)"
