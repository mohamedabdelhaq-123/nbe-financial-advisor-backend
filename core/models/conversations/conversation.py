import uuid

from django.db import models


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey("User", on_delete=models.CASCADE, related_name="conversations")
    started_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, default="active")

    class Meta:
        db_table = "conversations"

    def __str__(self):
        return f"Session {self.id[:8]} - User {self.user_id} ({self.status})"

    @property
    def ordered_messages(self):
        """Returns the full chronological flow of the dialogue."""
        return self.messages.order_by("created_at")
