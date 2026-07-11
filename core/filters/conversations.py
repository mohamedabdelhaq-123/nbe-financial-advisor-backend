import django_filters as filters

from core.models import Conversation, Message


class ConversationFilterSet(filters.FilterSet):
    """GET /chat/conversations"""

    class Meta:
        model = Conversation
        fields = {"status": ["exact"]}


class MessageFilterSet(filters.FilterSet):
    """GET /chat/conversations/{conversation_id}/messages"""

    class Meta:
        model = Message
        fields = {"stage": ["exact"]}
