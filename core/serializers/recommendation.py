from rest_framework import serializers


class RecommendationItemSerializer(serializers.Serializer):
    """
    GET /recommendations response item. No dedicated Data Shapes doc exists
    for this domain (see PLAN.md §5's open items) — built from
    Data_Governance_Specs.md §6's listed display fields ("title, description,
    categories, tags, features, external link") plus `similarity_score`,
    since Data_Governance_Specs.md §6 explicitly frames every match as a
    "soft suggestion" the score should communicate, not a bare list.
    Read-only/computed, so this is a plain Serializer (not a ModelSerializer)
    over a dict the view assembles — mirrors the pattern already used for
    other computed, non-1:1-model responses (e.g. statements' ocr-result).
    """

    id = serializers.UUIDField()
    title = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    categories = serializers.ListField(child=serializers.CharField())
    tags = serializers.ListField(child=serializers.CharField())
    features = serializers.JSONField(allow_null=True)
    external_link = serializers.CharField(allow_null=True)
    similarity_score = serializers.FloatField()


class RecommendationFeedbackSerializer(serializers.Serializer):
    """
    POST /recommendations/{recommendation_id}/feedback body. Mirrors
    POST /feedback's rating/comment convention (Data_Shapes_Feedback.md) —
    target_type/target_id aren't accepted here since they're implied by the
    URL path (always "recommendation" + the path's recommendation_id).
    """

    rating = serializers.IntegerField(min_value=1, max_value=5, required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_null=True, allow_blank=False)

    def validate(self, attrs):
        if not attrs.get("rating") and not attrs.get("comment"):
            raise serializers.ValidationError("At least one of rating or comment is required.")
        return attrs
