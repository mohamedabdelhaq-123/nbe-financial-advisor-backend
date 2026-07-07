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

    def get_preview(self, conversation):
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
