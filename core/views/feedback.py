from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.filters.feedback import IssueFilterSet
from core.models import Budget, Message, Reaction, ReportedIssue, Transaction
from core.openapi import error_responses
from core.serializers.feedback import (
    FeedbackCreateSerializer,
    IssueCreateSerializer,
    IssueSerializer,
    ReactionSerializer,
)

# One ownership check per allowed target_type — "message" is scoped via its
# owning conversation (Message has no direct user FK), rather than a
# direct field on the message itself.
_OWNERSHIP_CHECKS = {
    "transaction": lambda target_id, user: Transaction.objects.filter(
        id=target_id, user=user
    ).exists(),
    "message": lambda target_id, user: Message.objects.filter(
        id=target_id, conversation__user=user
    ).exists(),
    "budget": lambda target_id, user: Budget.objects.filter(id=target_id, user=user).exists(),
}


class FeedbackCreateView(APIView):
    """
    Leave feedback (a rating 1-5 and/or a comment — at least one is
    required) on a transaction, chat message, or budget. The target must
    belong to the current user: a `target_id` that exists but belongs to
    someone else returns 404, the same as a `target_id` that doesn't exist
    at all, so a caller can't use this endpoint to probe whether an id
    belongs to another user.
    """

    @extend_schema(
        request=FeedbackCreateSerializer,
        responses={201: ReactionSerializer, **error_responses(404, 422)},
    )
    def post(self, request):
        serializer = FeedbackCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        owns_target = _OWNERSHIP_CHECKS[data["target_type"]](data["target_id"], request.user)
        if not owns_target:
            raise NotFound("Target not found.")

        reaction = Reaction.objects.create(
            user=request.user,
            target_type=data["target_type"],
            target_id=data["target_id"],
            rating=data.get("rating"),
            comment=data.get("comment"),
        )
        return Response(ReactionSerializer(reaction).data, status=201)


@extend_schema_view(
    post=extend_schema(
        request=IssueCreateSerializer,
        responses={201: IssueSerializer, **error_responses(422)},
    )
)
class IssueListCreateView(generics.ListCreateAPIView):
    """List the current user's reported issues (bug reports/support
    requests), or file a new one. A new issue always starts with
    `status: "open"` — only an admin can move it through triage via
    `PATCH /admin/issues/{id}`, there's no user-facing way to change it."""

    pagination_class = LimitOffsetPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = IssueFilterSet

    def get_serializer_class(self):
        return IssueCreateSerializer if self.request.method == "POST" else IssueSerializer

    def get_queryset(self):
        # swagger_fake_view: see aggregations.py's TransactionListCreateView.get_queryset().
        if getattr(self, "swagger_fake_view", False):
            return ReportedIssue.objects.none()
        return ReportedIssue.objects.filter(user=self.request.user).order_by("-created_at")

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        issue = ReportedIssue.objects.create(
            user=request.user, description=serializer.validated_data["description"]
        )
        return Response(IssueSerializer(issue).data, status=201)
