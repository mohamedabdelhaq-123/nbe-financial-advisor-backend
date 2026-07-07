from rest_framework import serializers

from core.models import Product, Reaction, ReportedIssue


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class AdminLoginResponseSerializer(serializers.Serializer):
    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    admin_id = serializers.UUIDField()
    role = serializers.CharField()


class AdminReactionSerializer(serializers.ModelSerializer):
    """
    GET /admin/feedback — cross-user, so unlike the end-user-facing Feedback
    domain's ReactionSerializer, this exposes user_id (Data_Shapes_
    Administration.md: "opaque reference, not expanded to full profile —
    Administration owns no user profile data itself").
    """

    user_id = serializers.PrimaryKeyRelatedField(source="user", read_only=True)

    class Meta:
        model = Reaction
        fields = ["id", "user_id", "target_type", "target_id", "rating", "comment", "created_at"]
        read_only_fields = fields


class AdminIssueSerializer(serializers.ModelSerializer):
    user_id = serializers.PrimaryKeyRelatedField(source="user", read_only=True)

    class Meta:
        model = ReportedIssue
        fields = ["id", "user_id", "description", "status", "created_at", "resolved_at"]
        read_only_fields = fields


class AdminIssueUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["open", "in_review", "resolved", "dismissed"])


class AdminProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = [
            "id",
            "title",
            "description",
            "categories",
            "tags",
            "features",
            "external_link",
            "is_active",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AdminProductCreateSerializer(serializers.ModelSerializer):
    # Seed text(s) for embedding generation (AI service /internal/embed) —
    # not a Product field itself, popped and handled separately by the view.
    problem_statements = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )

    class Meta:
        model = Product
        fields = [
            "title",
            "description",
            "categories",
            "tags",
            "features",
            "external_link",
            "is_active",
            "problem_statements",
        ]
        extra_kwargs = {
            "categories": {"required": False},
            "tags": {"required": False},
            "features": {"required": False},
            "is_active": {"required": False},
        }


class AdminProductUpdateSerializer(serializers.ModelSerializer):
    """PATCH /admin/products/{id} — any subset of the writable fields; no problem_statements
    (that's POST-only, per API_GUIDE/Data_Shapes_Administration.md's PATCH spec)."""

    class Meta:
        model = Product
        fields = [
            "title",
            "description",
            "categories",
            "tags",
            "features",
            "external_link",
            "is_active",
        ]
