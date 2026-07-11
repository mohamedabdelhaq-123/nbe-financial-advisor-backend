from rest_framework import serializers


class RecommendationItemSerializer(serializers.Serializer):
    """
    One matched product recommendation: the usual display fields (title,
    description, categories, tags, features, external link) plus
    `similarity_score` — every match is framed as a soft suggestion the
    score should communicate, not a bare ranked list, so the score always
    travels with the result rather than only being used internally to sort.
    Read-only/computed, so this is a plain Serializer over a dict the view
    assembles, not a ModelSerializer.
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
    Request body for reacting to a specific shown recommendation. Mirrors
    POST /feedback's rating/comment convention, but `target_type`/`target_id`
    aren't accepted here since they're implied by the URL path itself
    (always `"recommendation"` plus the path's `recommendation_id`).
    """

    rating = serializers.IntegerField(min_value=1, max_value=5, required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_null=True, allow_blank=False)

    def validate(self, attrs):
        if not attrs.get("rating") and not attrs.get("comment"):
            raise serializers.ValidationError("At least one of rating or comment is required.")
        return attrs
