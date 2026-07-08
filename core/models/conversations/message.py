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
    # Not in docs/DB_Schema.md's `messages` table, but
    # docs/API_GUIDE/Data_Shapes_Conversations.md's message shape requires a
    # `widget` field on every message (both in GET .../messages history and
    # the POST .../messages SSE "done" event) — Architectural_Guidelines.md
    # §7 treats widgets as first-class, reopenable structured output, not a
    # one-time streaming artifact, so it needs to be persisted rather than
    # only ever present in the live SSE response. Added here as the smallest
    # schema change that satisfies the documented contract.
    widget_json = models.JSONField(blank=True, null=True)
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
