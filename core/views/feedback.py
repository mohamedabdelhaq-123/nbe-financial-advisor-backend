from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Budget, Message, Reaction, ReportedIssue, Transaction
from core.serializers.feedback import (
    FeedbackCreateSerializer,
    IssueCreateSerializer,
    IssueSerializer,
    ReactionSerializer,
)

# One ownership check per allowed target_type — "message" is scoped via its
# owning conversation (Message has no direct user FK), matching
# Data_Shapes_Feedback.md: "for message, belong to a conversation owned by
# the requesting user".
_OWNERSHIP_CHECKS = {
    "transaction": lambda target_id, user: Transaction.objects.filter(id=target_id, user=user).exists(),
    "message": lambda target_id, user: Message.objects.filter(
        id=target_id, conversation__user=user
    ).exists(),
    "budget": lambda target_id, user: Budget.objects.filter(id=target_id, user=user).exists(),
}


class FeedbackCreateView(APIView):
    """POST /feedback"""

    def post(self, request):
        serializer = FeedbackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        owns_target = _OWNERSHIP_CHECKS[data["target_type"]](data["target_id"], request.user)
        if not owns_target:
            # 404, not 403 — API Design Guidelines §10's existence-leak
            # avoidance rule applies here exactly as it does to owned
            # resources accessed directly by id elsewhere in the API.
            raise NotFound("Target not found.")

        reaction = Reaction.objects.create(
            user=request.user,
            target_type=data["target_type"],
            target_id=data["target_id"],
            rating=data.get("rating"),
            comment=data.get("comment"),
        )
        return Response(ReactionSerializer(reaction).data, status=201)


class IssueListCreateView(generics.ListCreateAPIView):
    """POST/GET /issues"""

    pagination_class = LimitOffsetPagination

    def get_serializer_class(self):
        return IssueCreateSerializer if self.request.method == "POST" else IssueSerializer

    def get_queryset(self):
        qs = ReportedIssue.objects.filter(user=self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs.order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = ReportedIssue.objects.create(user=request.user, description=serializer.validated_data["description"])
        return Response(IssueSerializer(issue).data, status=201)
