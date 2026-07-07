import uuid

from django.db import models


class Message(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(
        "Conversation",
        on_delete=models.CASCADE,
        related_name="messages",
    )
    sender = models.CharField(max_length=20)  # e.g., 'user', 'assistant'
    content = models.TextField()
    stage = models.CharField(max_length=30, default="general")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "messages"
        indexes = [
            models.Index(
                fields=["conversation", "created_at"],
                name="idx_messages_conversation",
            )
        ]

    def __str__(self):
        return f"Msg {self.id[:8]} by {self.sender} [{self.stage}]"

    def add_reference(self, target_type, target_id):
        """Clean utility to bind an external system artifact to this message."""
        return self.references.create(target_type=target_type, target_id=target_id)
