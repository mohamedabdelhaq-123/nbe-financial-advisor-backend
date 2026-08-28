from rest_framework import serializers

from core.constants import CHAT_MESSAGE_MAX_LENGTH
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
        fields = [
            "id",
            "sender",
            "content",
            "stage",
            "widget_json",
            "references",
            "suggestions_json",
            "created_at",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        # Renamed from the model's widget_json (see Message model's docstring
        # comment on why that column exists) to the documented `widget` key,
        # with the documented {type: null, payload: null} fallback when no
        # widget was attached to this message. suggestions_json gets the same
        # treatment, falling back to an empty list.
        data = super().to_representation(instance)
        widget = data.pop("widget_json")
        data["widget"] = widget or {"type": None, "payload": None}
        suggestions = data.pop("suggestions_json")
        data["suggestions"] = suggestions or []
        return data


class MessageCreateSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=CHAT_MESSAGE_MAX_LENGTH)


class MessageWidgetUpdateSerializer(serializers.Serializer):
    """PATCH .../messages/<id>/widget/ body — replaces just the widget's
    payload, not its type."""

    payload = serializers.JSONField()


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
    suggestions = serializers.ListField(child=serializers.CharField())


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


class ChatToolStatusEventSerializer(serializers.Serializer):
    """
    Documents the `data` payload of the `chat_tool_status` SSE event —
    published by generate_chat_reply (core/tasks/conversations.py) whenever
    the AI service's `analysis` node calls a tool mid-turn. Best-effort/
    informational: a turn with no tool calls publishes none of these, and a
    client must not depend on this for correctness. Same documentation-only
    role as MessageDoneEventSerializer above — not a real DRF response body,
    EventStreamView's is text/event-stream.
    """

    conversation_id = serializers.UUIDField()
    call_id = serializers.CharField()
    tool = serializers.CharField()
    status = serializers.ChoiceField(choices=["started", "completed"])
