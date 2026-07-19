from rest_framework import serializers

from core.models import Conversation, Message, MessageReference


class ConversationSerializer(serializers.ModelSerializer):
    """POST /chat/conversations response shape."""

    class Meta:
        model = Conversation
        fields = ["id", "started_at", "last_message_at", "status"]
        read_only_fields = fields


class ConversationListItemSerializer(ConversationSerializer):
    """GET /chat/conversations — adds `preview`, the most recent message's first ~80 chars."""

    preview = serializers.SerializerMethodField()

    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields + ["preview"]
        read_only_fields = fields

    def get_preview(self, conversation) -> str | None:
        latest = conversation.messages.order_by("-created_at").first()
        return latest.content[:80] if latest else None


class MessageReferenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReference
        fields = ["target_type", "target_id"]
        read_only_fields = fields


class MessageSerializer(serializers.ModelSerializer):
    references = MessageReferenceSerializer(many=True, read_only=True)

    class Meta:
        model = Message
        fields = ["id", "sender", "content", "stage", "widget_json", "references", "created_at"]
        read_only_fields = fields

    def to_representation(self, instance):
        # Renamed from the model's widget_json (see Message model's docstring
        # comment on why that column exists) to the documented `widget` key,
        # with the documented {type: null, payload: null} fallback when no
        # widget was attached to this message.
        data = super().to_representation(instance)
        widget = data.pop("widget_json")
        data["widget"] = widget or {"type": None, "payload": None}
        return data


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField()


class WidgetSerializer(serializers.Serializer):
    type = serializers.CharField(allow_null=True)
    payload = serializers.JSONField(allow_null=True)


class MessageDoneEventSerializer(serializers.Serializer):
    """
    Documents the `data` payload of the `chat_message` SSE event published
    on the single multiplexed connection (core/views/events.py's
    EventStreamView) by core/tasks/conversations.py's generate_chat_reply —
    not something POST .../messages itself returns (that endpoint now just
    202s with the user's own message; see MessageSerializer). Documentation
    aid for drf-spectacular (API Design Guidelines §11) rather than
    something a client would deserialize a real DRF response body as, since
    EventStreamView's response is text/event-stream, not JSON.
    """

    conversation_id = serializers.UUIDField()
    id = serializers.UUIDField()
    content = serializers.CharField()
    widget = WidgetSerializer()
    references = MessageReferenceSerializer(many=True)


class ChatErrorEventSerializer(serializers.Serializer):
    """
    Documents the `data` payload of the `chat_error` SSE event — published by
    generate_chat_reply (core/tasks/conversations.py) in place of
    chat_message when the AI service's reply fails (a stream-level `error`
    event, or the request itself failing). No assistant Message is persisted
    when this fires. Same documentation-only role as MessageDoneEventSerializer
    above — not a real DRF response body, EventStreamView's is text/event-stream.
    """

    conversation_id = serializers.UUIDField()
    message = serializers.CharField()


class ConversationAttachmentResponseSerializer(serializers.Serializer):
    statement_id = serializers.UUIDField()
    status = serializers.CharField()
    message_id = serializers.UUIDField()


class ConversationAttachmentRequestSerializer(serializers.Serializer):
    """multipart/form-data request for POST .../attachments — documentation only,
    the view reads request.FILES directly (see create_statement_from_upload())."""

    file = serializers.FileField()
    # Optional caption typed alongside the file; becomes the user's message
    # content (falls back to the file name when absent).
    text = serializers.CharField(required=False, allow_blank=True)
