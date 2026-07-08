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
    Documents the `data` payload of the terminal "done" SSE event from POST
    .../messages (core/views/conversations.py's _sse_stream()) — the actual
    HTTP response is text/event-stream, not JSON, so this is a documentation
    aid for drf-spectacular (API Design Guidelines §11) rather than something
    a client would ever deserialize the whole response body as.
    """

    id = serializers.UUIDField()
    content = serializers.CharField()
    widget = WidgetSerializer()
    references = MessageReferenceSerializer(many=True)


class ConversationAttachmentResponseSerializer(serializers.Serializer):
    statement_id = serializers.UUIDField()
    status = serializers.CharField()
    message_id = serializers.UUIDField()


class ConversationAttachmentRequestSerializer(serializers.Serializer):
    """multipart/form-data request for POST .../attachments — documentation only,
    the view reads request.FILES directly (see create_statement_from_upload())."""

    file = serializers.FileField()
