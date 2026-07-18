from rest_framework import serializers

from core.models import Category

_CATEGORY_TYPE_CHOICES = ["income", "expense"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = [
            "id",
            "name",
            "label",
            "category_type",
            "is_fallback",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class CategoryCreateSerializer(serializers.ModelSerializer):
    category_type = serializers.ChoiceField(choices=_CATEGORY_TYPE_CHOICES)

    class Meta:
        model = Category
        fields = ["name", "label", "category_type", "is_fallback"]
        extra_kwargs = {"is_fallback": {"required": False}}


class CategoryUpdateSerializer(serializers.ModelSerializer):
    category_type = serializers.ChoiceField(choices=_CATEGORY_TYPE_CHOICES, required=False)

    class Meta:
        model = Category
        fields = ["name", "label", "category_type", "is_fallback"]
        extra_kwargs = {
            "name": {"required": False},
            "label": {"required": False},
            "is_fallback": {"required": False},
        }
