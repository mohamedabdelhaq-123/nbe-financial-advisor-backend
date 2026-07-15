from decimal import Decimal

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.exceptions import AIServiceUnavailable
from core.models import Product, Reaction, RecommendationLog
from core.openapi import error_responses
from core.serializers.feedback import ReactionSerializer
from core.serializers.recommendation import (
    RecommendationFeedbackSerializer,
    RecommendationItemSerializer,
)
from services import ai_service
from services.ai_service import AIServiceError


class RecommendationsView(APIView):
    """
    Get product recommendations, optionally guided by a free-text query
    (`q`). Only ever surfaces **active** products — unlike the admin-facing
    `GET /admin/products`, which also shows inactive ones. Every result
    returned is also logged (who saw it, for what query, at what match
    confidence), since a shown recommendation can later be reacted to via
    `POST /recommendations/{recommendation_id}/feedback`, and that endpoint
    needs a logged instance to attach the reaction to.

    `q` is **not** a queryset filter — matching happens in
    services/ai_service.py's match_recommendations() (a real
    /internal/recommendations/match call, or an in-process mock), which
    returns product ids for this view to resolve, so it can't be expressed
    as a django-filter field. Unlike chat/statement-ingestion, this call
    happens synchronously in the request/response cycle rather than behind a
    Celery task — a failure here becomes a 502 (AIServiceUnavailable)
    directly, not a buffered failure_reason/chat_error.
    """

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "q", OpenApiTypes.STR, required=False, description="Free-text query for matching"
            )
        ],
        responses={200: RecommendationItemSerializer(many=True), **error_responses(502)},
    )
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        try:
            response = ai_service.match_recommendations(str(request.user.id), query)
        except AIServiceError as exc:
            raise AIServiceUnavailable(str(exc)) from exc

        # match_recommendations only returns product_id — resolve back to
        # Product rows here (is_active=True, same as before), preserving the
        # AI service's ranking order.
        products_by_id = {
            str(p.id): p
            for p in Product.objects.filter(
                id__in=[m["product_id"] for m in response["matches"]], is_active=True
            )
        }

        results = []
        for match in response["matches"]:
            product = products_by_id.get(match["product_id"])
            if product is None:
                continue
            RecommendationLog.objects.create(
                user=request.user,
                product=product,
                matched_query=query or None,
                similarity_score=Decimal(str(match["similarity"])),
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
                    "similarity_score": match["similarity"],
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
