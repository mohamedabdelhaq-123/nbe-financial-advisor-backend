from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import InvestmentHolding
from core.openapi import error_responses
from core.serializers.investment_holdings import (
    HoldingInstrumentSerializer,
    HoldingValuationResponseSerializer,
    InvestmentHoldingSerializer,
    InvestmentHoldingWriteSerializer,
)
from services.market_data import MarketDataUnavailable, fetch_market_quotes

MONEY = Decimal("0.01")
PERCENT = Decimal("0.0001")


def _holding_queryset(user):
    return InvestmentHolding.objects.filter(user=user).select_related("instrument__product")


@extend_schema(responses={200: HoldingInstrumentSerializer(many=True)})
class InvestmentInstrumentListView(generics.ListAPIView):
    """The authenticated user's selectable curated holding catalogue."""

    serializer_class = HoldingInstrumentSerializer
    pagination_class = None

    def get_queryset(self):
        from core.models import InvestmentInstrument

        return (
            InvestmentInstrument.objects.filter(is_active=True)
            .select_related("product")
            .order_by("product__title", "id")
        )


@extend_schema_view(
    get=extend_schema(responses={200: InvestmentHoldingSerializer(many=True)}),
    post=extend_schema(
        request=InvestmentHoldingWriteSerializer,
        responses={201: InvestmentHoldingSerializer, **error_responses(422)},
    ),
)
class InvestmentHoldingListCreateView(generics.ListCreateAPIView):
    pagination_class = None

    def get_queryset(self):
        return _holding_queryset(self.request.user).order_by("-updated_at", "-id")

    def get_serializer_class(self):
        if self.request.method == "GET":
            return InvestmentHoldingSerializer
        return InvestmentHoldingWriteSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        holding = serializer.save(user=request.user)
        holding = get_object_or_404(_holding_queryset(request.user), id=holding.id)
        return Response(InvestmentHoldingSerializer(holding).data, status=201)


@extend_schema_view(
    get=extend_schema(responses={200: InvestmentHoldingSerializer, **error_responses(404)}),
    patch=extend_schema(
        request=InvestmentHoldingWriteSerializer,
        responses={200: InvestmentHoldingSerializer, **error_responses(404, 422)},
    ),
    delete=extend_schema(responses={204: None, **error_responses(404)}),
)
class InvestmentHoldingDetailView(generics.RetrieveUpdateDestroyAPIView):
    lookup_url_kwarg = "holding_id"
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return _holding_queryset(self.request.user)

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return InvestmentHoldingWriteSerializer
        return InvestmentHoldingSerializer

    def update(self, request, *args, **kwargs):
        holding = self.get_object()
        serializer = InvestmentHoldingWriteSerializer(
            holding,
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        holding = get_object_or_404(_holding_queryset(request.user), id=holding.id)
        return Response(InvestmentHoldingSerializer(holding).data)


@extend_schema(
    responses={200: HoldingValuationResponseSerializer},
    description=(
        "Value manually recorded holdings with the latest available normalized quotes. "
        "No user, quantity, purchase price, or fee data is sent to the quote provider."
    ),
)
class InvestmentHoldingValuationView(APIView):
    def get(self, request):
        holdings = list(_holding_queryset(request.user).order_by("-updated_at", "-id"))
        instruments = list({item.instrument_id: item.instrument for item in holdings}.values())
        quotes = {}
        if settings.MARKET_DATA_ENABLED:
            try:
                quotes = fetch_market_quotes(instruments)
            except MarketDataUnavailable:
                quotes = {}

        now = timezone.now()
        rows = []
        total_cost = Decimal("0")
        total_current = Decimal("0")
        priced_count = 0
        for holding in holdings:
            cost_basis = holding.quantity * holding.average_purchase_price + holding.fees
            total_cost += cost_basis
            quote = quotes.get(holding.instrument_id)
            if not settings.MARKET_DATA_ENABLED:
                quote_status = "disabled"
            elif quote is None:
                quote_status = "unavailable"
            else:
                max_age = holding.instrument.max_quote_age_seconds
                age = (now - quote["observed_at"]).total_seconds()
                quote_status = "current" if age <= max_age else "needs_refresh"

            if quote is None:
                current_price = current_value = gain_loss = gain_loss_percentage = None
                observed_at = source = None
            else:
                current_price = quote["price"]
                current_value = (holding.quantity * current_price).quantize(
                    MONEY, rounding=ROUND_HALF_UP
                )
                gain_loss = (current_value - cost_basis).quantize(MONEY, rounding=ROUND_HALF_UP)
                gain_loss_percentage = (gain_loss / cost_basis * 100).quantize(
                    PERCENT, rounding=ROUND_HALF_UP
                )
                observed_at = quote["observed_at"]
                source = quote["source"]
                total_current += current_value
                priced_count += 1

            rows.append(
                {
                    "holding": holding,
                    "quote_status": quote_status,
                    "current_price": current_price,
                    "current_value": current_value,
                    "gain_loss": gain_loss,
                    "gain_loss_percentage": gain_loss_percentage,
                    "observed_at": observed_at,
                    "source": source,
                }
            )

        is_complete = priced_count == len(holdings)
        if is_complete and holdings:
            total_gain = (total_current - total_cost).quantize(MONEY, rounding=ROUND_HALF_UP)
            total_gain_pct = (total_gain / total_cost * 100).quantize(
                PERCENT, rounding=ROUND_HALF_UP
            )
            total_current_value = total_current.quantize(MONEY, rounding=ROUND_HALF_UP)
        else:
            total_gain = total_gain_pct = total_current_value = None

        data = {
            "feature_status": "enabled" if settings.MARKET_DATA_ENABLED else "disabled",
            "refreshed_at": now,
            "is_complete": is_complete,
            "priced_holding_count": priced_count,
            "total_holding_count": len(holdings),
            "total_cost_basis": total_cost.quantize(MONEY, rounding=ROUND_HALF_UP),
            "total_current_value": total_current_value,
            "total_gain_loss": total_gain,
            "total_gain_loss_percentage": total_gain_pct,
            "holdings": rows,
        }
        return Response(HoldingValuationResponseSerializer(data).data)
