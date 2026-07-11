from decimal import Decimal

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product, Reaction, RecommendationLog
from core.openapi import error_responses
from core.serializers.feedback import ReactionSerializer
from core.serializers.recommendation import (
    RecommendationFeedbackSerializer,
    RecommendationItemSerializer,
)
from services import ai_service


class RecommendationsView(APIView):
    """
    Get product recommendations, optionally guided by a free-text query
    (`q`). Only ever surfaces **active** products — unlike the admin-facing
    `GET /admin/products`, which also shows inactive ones. Every result
    returned is also logged (who saw it, for what query, at what match
    confidence), since a shown recommendation can later be reacted to via
    `POST /recommendations/{recommendation_id}/feedback`, and that endpoint
    needs a logged instance to attach the reaction to.

    `q` is **not** a queryset filter — it feeds an in-memory keyword-overlap
    ranking over the full active-product list rather than narrowing a
    database query, so it can't be expressed as a django-filter field.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q", OpenApiTypes.STR, required=False, description="Free-text query for matching"
            )
        ],
        responses={200: RecommendationItemSerializer(many=True)},
    )
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        active_products = list(Product.objects.filter(is_active=True))
        matches = ai_service.match_recommendations(query, active_products)

        results = []
        for match in matches:
            product = match["product"]
            RecommendationLog.objects.create(
                user=request.user,
                product=product,
                matched_query=query or None,
                similarity_score=Decimal(str(match["similarity_score"])),
            )
            results.append(
                {
                    "id": product.id,
                    "title": product.title,
                    "description": product.description,
                    "categories": product.categories,
                    "tags": product.tags,
                    "features": product.features,
                    "external_link": product.external_link,
                    "similarity_score": match["similarity_score"],
                }
            )

        return Response(RecommendationItemSerializer(results, many=True).data)


class RecommendationFeedbackView(APIView):
    """
    React to a specific shown recommendation with an optional rating
    (1-5) and/or comment — at least one of the two is required.

    `recommendation_id` refers to a logged "this product was shown to this
    user" row (created by `GET /recommendations`), not a Product id
    directly — so reacting to a recommendation this user was never shown
    404s, even if the product id itself exists. Creates a reaction exactly
    like `POST /feedback` would, just via a dedicated path with
    `target_type` fixed to `"recommendation"`.
    """

    @extend_schema(
        request=RecommendationFeedbackSerializer,
        responses={201: ReactionSerializer, **error_responses(404, 422)},
    )
    def post(self, request, recommendation_id):
        log = get_object_or_404(RecommendationLog, id=recommendation_id, user=request.user)
        serializer = RecommendationFeedbackSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        reaction = Reaction.objects.create(
            user=request.user,
            target_type="recommendation",
            target_id=log.id,
            rating=data.get("rating"),
            comment=data.get("comment"),
        )
        return Response(ReactionSerializer(reaction).data, status=201)
