from decimal import Decimal

from django.shortcuts import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import Product, Reaction, RecommendationLog
from core.serializers.feedback import ReactionSerializer
from core.serializers.recommendation import RecommendationFeedbackSerializer, RecommendationItemSerializer
from services import ai_service


class RecommendationsView(APIView):
    """
    GET /recommendations?q=<optional query text>

    Only ever surfaces active, matched products (Data_Shapes_Administration.md's
    contrast with the admin-facing GET /admin/products, which includes
    inactive ones) — is_active=True is applied before matching even runs.
    Every result shown is logged to `recommendation_logs`
    (Data_Governance_Specs.md §6: "log of which product was shown to which
    user, for which query, with what match confidence"), not just returned.
    """

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
    POST /recommendations/{recommendation_id}/feedback

    `recommendation_id` refers to a `recommendation_logs` row — a specific
    shown instance — not a Product id (Data_Shapes_Feedback.md: "tied to a
    specific shown instance"). Creates a Reaction exactly like POST
    /feedback would, just via a dedicated path with target_type fixed to
    "recommendation" (Data_Governance_Specs.md §6: "Recommendation log
    entries may be targeted by Feedback").
    """

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
