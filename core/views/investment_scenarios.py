from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from core.models import (
    InvestmentHolding,
    InvestmentInstrument,
    SavedInvestmentAllocationPurchase,
    SavedInvestmentScenario,
    User,
)
from core.openapi import error_responses
from core.serializers.investment_scenarios import (
    SavedInvestmentAllocationPurchaseSerializer,
    SavedInvestmentAllocationUpdateSerializer,
    SavedInvestmentScenarioSerializer,
    SavedInvestmentScenarioUpdateSerializer,
    remove_saved_allocation,
    update_saved_allocation,
)


@extend_schema_view(
    get=extend_schema(
        description=(
            "List the current user's saved investment-scenario snapshots. "
            "They are advisory records only and never represent executed holdings. "
            "Defaults to active saved scenarios; pass status=archived or status=all explicitly."
        ),
        responses={200: SavedInvestmentScenarioSerializer(many=True)},
    )
)
class SavedInvestmentScenarioListView(generics.ListAPIView):
    serializer_class = SavedInvestmentScenarioSerializer
    pagination_class = LimitOffsetPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SavedInvestmentScenario.objects.none()

        status_filter = self.request.query_params.get(
            "status", SavedInvestmentScenario.Status.SAVED
        )
        if status_filter not in {
            SavedInvestmentScenario.Status.SAVED,
            SavedInvestmentScenario.Status.ARCHIVED,
            "all",
        }:
            raise ValidationError({"status": "Use saved, archived, or all."})

        queryset = (
            SavedInvestmentScenario.objects.filter(user=self.request.user)
            .select_related("source_message__conversation")
            .prefetch_related("allocation_purchases")
        )
        if status_filter != "all":
            queryset = queryset.filter(status=status_filter)
        return queryset.order_by("-saved_at", "-id")


@extend_schema_view(
    get=extend_schema(responses={200: SavedInvestmentScenarioSerializer, **error_responses(404)}),
    patch=extend_schema(
        request=SavedInvestmentScenarioUpdateSerializer,
        responses={200: SavedInvestmentScenarioSerializer, **error_responses(404, 422)},
    ),
    delete=extend_schema(responses={204: None, **error_responses(404)}),
)
class SavedInvestmentScenarioDetailView(generics.RetrieveUpdateDestroyAPIView):
    lookup_url_kwarg = "scenario_id"
    http_method_names = ["get", "patch", "delete", "head", "options"]

    def get_queryset(self):
        return (
            SavedInvestmentScenario.objects.filter(user=self.request.user)
            .select_related("source_message__conversation")
            .prefetch_related("allocation_purchases")
        )

    def get_serializer_class(self):
        if self.request.method == "PATCH":
            return SavedInvestmentScenarioUpdateSerializer
        return SavedInvestmentScenarioSerializer

    def patch(self, request, *args, **kwargs):
        scenario = self.get_object()
        serializer = SavedInvestmentScenarioUpdateSerializer(
            scenario, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        scenario.refresh_from_db()
        scenario = get_object_or_404(self.get_queryset(), id=scenario.id)
        return Response(SavedInvestmentScenarioSerializer(scenario).data)


class SavedInvestmentAllocationPurchaseView(APIView):
    """Move one saved-plan allocation into the user's actual investments."""

    @extend_schema(
        request=SavedInvestmentAllocationPurchaseSerializer,
        responses={
            200: SavedInvestmentScenarioSerializer,
            201: SavedInvestmentScenarioSerializer,
            **error_responses(404, 422),
        },
        description=(
            "Record the actual purchase for one allocation in a saved plan. "
            "This records a user-reported purchase; it never executes a trade. "
            "Retries are idempotent and cannot add the quantity twice."
        ),
    )
    @transaction.atomic
    def post(self, request, scenario_id, instrument_id):
        scenario = get_object_or_404(
            SavedInvestmentScenario.objects.select_for_update(),
            id=scenario_id,
            user=request.user,
        )
        if scenario.status != SavedInvestmentScenario.Status.SAVED:
            raise ValidationError({"scenario": "Restore this plan before recording a purchase."})

        allocation_ids = {
            str(allocation.get("instrument_id"))
            for allocation in scenario.payload_json.get("allocations", [])
        }
        if str(instrument_id) not in allocation_ids:
            raise ValidationError(
                {"instrument_id": "This investment is not part of the saved plan."}
            )

        serializer = SavedInvestmentAllocationPurchaseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        existing_purchase = (
            SavedInvestmentAllocationPurchase.objects.select_related("holding")
            .filter(scenario=scenario, instrument_id=instrument_id)
            .first()
        )
        if existing_purchase:
            return Response(self._serialized_scenario(scenario.id))

        instrument = get_object_or_404(
            InvestmentInstrument.objects.select_related("product"), id=instrument_id
        )
        quantity: Decimal = serializer.validated_data["quantity"]
        unit_price: Decimal = serializer.validated_data["unit_price"]
        fees: Decimal = serializer.validated_data["fees"]
        purchased_at = serializer.validated_data["purchased_at"]
        if quantity % instrument.minimum_increment != 0:
            raise ValidationError(
                {
                    "quantity": (
                        "Quantity must use increments of "
                        f"{instrument.minimum_increment.normalize()}."
                    )
                }
            )

        # Purchase confirmations for the same user may arrive from different
        # saved plans. Serializing them on the user row prevents lost weighted-
        # average updates to a shared holding.
        User.objects.select_for_update().only("id").get(id=request.user.id)

        holding = (
            InvestmentHolding.objects.select_for_update()
            .filter(user=request.user, instrument=instrument)
            .first()
        )
        if holding is None:
            holding = InvestmentHolding.objects.create(
                user=request.user,
                instrument=instrument,
                quantity=quantity,
                average_purchase_price=unit_price,
                fees=fees,
                purchased_at=purchased_at,
            )
        else:
            previous_purchase_cost = holding.quantity * holding.average_purchase_price
            new_quantity = holding.quantity + quantity
            weighted_price = (
                (previous_purchase_cost + quantity * unit_price) / new_quantity
            ).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
            holding.quantity = new_quantity
            holding.average_purchase_price = weighted_price
            holding.fees = (holding.fees + fees).quantize(Decimal("0.01"))
            holding.purchased_at = (
                min(holding.purchased_at, purchased_at) if holding.purchased_at else purchased_at
            )
            holding.save(
                update_fields=[
                    "quantity",
                    "average_purchase_price",
                    "fees",
                    "purchased_at",
                    "updated_at",
                ]
            )

        SavedInvestmentAllocationPurchase.objects.create(
            scenario=scenario,
            instrument=instrument,
            holding=holding,
            quantity=quantity,
            unit_price=unit_price,
            fees=fees,
            purchased_at=purchased_at,
        )
        return Response(
            self._serialized_scenario(scenario.id),
            status=status.HTTP_201_CREATED,
        )

    def _serialized_scenario(self, scenario_id):
        scenario = (
            SavedInvestmentScenario.objects.select_related("source_message__conversation")
            .prefetch_related("allocation_purchases")
            .get(id=scenario_id)
        )
        return SavedInvestmentScenarioSerializer(scenario).data


class SavedInvestmentAllocationUpdateView(APIView):
    """Edit or remove one allocation that has not been bought."""

    @extend_schema(
        request=SavedInvestmentAllocationUpdateSerializer,
        responses={
            200: SavedInvestmentScenarioSerializer,
            **error_responses(404, 422),
        },
        description=(
            "Change one pending allocation's planned amount or reference price, "
            "then recalculate its estimated quantity. A user-edited price is "
            "clearly stored as user-supplied. Purchased allocations cannot be changed."
        ),
    )
    @transaction.atomic
    def patch(self, request, scenario_id, instrument_id):
        scenario = get_object_or_404(
            SavedInvestmentScenario.objects.select_for_update(),
            id=scenario_id,
            user=request.user,
        )
        if scenario.status != SavedInvestmentScenario.Status.SAVED:
            raise ValidationError({"scenario": "Restore this plan before editing it."})
        if SavedInvestmentAllocationPurchase.objects.filter(
            scenario=scenario, instrument_id=instrument_id
        ).exists():
            raise ValidationError(
                {
                    "instrument_id": (
                        "This investment is already owned. Edit it under Owned investments."
                    )
                }
            )

        serializer = SavedInvestmentAllocationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scenario.payload_json = update_saved_allocation(
            scenario.payload_json,
            instrument_id,
            serializer.validated_data,
        )
        scenario.save(update_fields=["payload_json", "updated_at"])
        scenario = (
            SavedInvestmentScenario.objects.select_related("source_message__conversation")
            .prefetch_related("allocation_purchases")
            .get(id=scenario.id)
        )
        return Response(SavedInvestmentScenarioSerializer(scenario).data)

    @extend_schema(
        responses={204: None, **error_responses(404, 422)},
        description=(
            "Remove one pending allocation. Removing the final item removes the "
            "plan. Purchases already moved to Owned are never deleted."
        ),
    )
    @transaction.atomic
    def delete(self, request, scenario_id, instrument_id):
        scenario = get_object_or_404(
            SavedInvestmentScenario.objects.select_for_update(),
            id=scenario_id,
            user=request.user,
        )
        if scenario.status != SavedInvestmentScenario.Status.SAVED:
            raise ValidationError({"scenario": "Restore this plan before editing it."})
        if SavedInvestmentAllocationPurchase.objects.filter(
            scenario=scenario, instrument_id=instrument_id
        ).exists():
            raise ValidationError(
                {
                    "instrument_id": (
                        "This investment is already owned. Edit it under Owned investments."
                    )
                }
            )

        updated_payload = remove_saved_allocation(scenario.payload_json, instrument_id)
        purchased_ids = {
            str(value)
            for value in SavedInvestmentAllocationPurchase.objects.filter(
                scenario=scenario
            ).values_list("instrument_id", flat=True)
        }
        remaining_ids = {
            str(allocation.get("instrument_id"))
            for allocation in (updated_payload or {}).get("allocations", [])
        }
        if updated_payload is None or remaining_ids.issubset(purchased_ids):
            scenario.delete()
        else:
            scenario.payload_json = updated_payload
            scenario.save(update_fields=["payload_json", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)
