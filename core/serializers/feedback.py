from rest_framework import serializers

from core.models import Reaction, ReportedIssue


class FeedbackCreateSerializer(serializers.Serializer):
    """
    POST /feedback body. `target_type` is deliberately narrower than what the
    Reaction model/DB comment allows (transaction | recommendation | message
    | budget) — "recommendation" feedback has its own dedicated endpoint
    (POST /recommendations/{id}/feedback, Recommendation domain checkpoint),
    per Data_Shapes_Feedback.md's own scoping note.
    """

    target_type = serializers.ChoiceField(choices=["transaction", "message", "budget"])
    target_id = serializers.UUIDField()
    rating = serializers.IntegerField(min_value=1, max_value=5, required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_null=True, allow_blank=False)

    def validate(self, attrs):
        if not attrs.get("rating") and not attrs.get("comment"):
            raise serializers.ValidationError("At least one of rating or comment is required.")
        return attrs


class ReactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reaction
        fields = ["id", "target_type", "target_id", "rating", "comment", "created_at"]
        read_only_fields = fields


class IssueCreateSerializer(serializers.ModelSerializer):
    description = serializers.CharField(min_length=10)

    class Meta:
        model = ReportedIssue
        fields = ["description"]


class IssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReportedIssue
        fields = ["id", "description", "status", "created_at", "resolved_at"]
        read_only_fields = fields
