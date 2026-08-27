from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from datetime import timezone as datetime_timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID

from django.utils import timezone
from rest_framework import serializers

from core.models import (
    InvestmentInstrument,
    SavedInvestmentAllocationPurchase,
    SavedInvestmentScenario,
)

MONEY_TOLERANCE = Decimal("0.02")
IMMUTABLE_PLAN_FIELDS = ("confirmed_amount", "currency", "disclaimer")
IMMUTABLE_ALLOCATION_FIELDS = (
    "instrument_id",
    "instrument_code",
    "display_name",
    "asset_class",
    "unit_price",
    "price_currency",
    "unit",
    "price_type",
    "minimum_increment",
    "observed_at",
    "source",
    "mode",
    "priority",
    "match_factors",
)
MATCH_FACTORS = ["objective", "risk", "horizon", "liquidity", "closest_available"]


class InvestmentAllocationSnapshotSerializer(serializers.Serializer):
    instrument_id = serializers.UUIDField()
    instrument_code = serializers.CharField(max_length=100)
    display_name = serializers.CharField(max_length=255)
    asset_class = serializers.ChoiceField(choices=["gold", "fund", "currency"])
    percentage = serializers.DecimalField(
        max_digits=7,
        decimal_places=2,
        min_value=Decimal("0"),
        max_value=Decimal("100"),
    )
    target_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    unit_price = serializers.DecimalField(
        max_digits=20, decimal_places=8, min_value=Decimal("0.00000001")
    )
    price_currency = serializers.ChoiceField(choices=["EGP"])
    unit = serializers.CharField(max_length=40)
    price_type = serializers.ChoiceField(
        choices=["spot", "nav", "market_price", "customer_buy_rate"]
    )
    minimum_increment = serializers.DecimalField(
        max_digits=20, decimal_places=8, min_value=Decimal("0.00000001")
    )
    quantity = serializers.DecimalField(max_digits=24, decimal_places=8, min_value=Decimal("0"))
    actual_allocated_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    unallocated_remainder = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    observed_at = serializers.DateTimeField()
    source = serializers.CharField(max_length=255)
    mode = serializers.ChoiceField(choices=["live", "mock", "user_supplied"])
    priority = serializers.IntegerField(min_value=1, max_value=3, required=False)
    match_factors = serializers.ListField(
        child=serializers.ChoiceField(choices=MATCH_FACTORS),
        max_length=len(MATCH_FACTORS),
        required=False,
        default=list,
    )


class InvestmentScenarioPayloadSerializer(serializers.Serializer):
    confirmed_amount = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0.01")
    )
    currency = serializers.ChoiceField(choices=["EGP"])
    allocations = InvestmentAllocationSnapshotSerializer(many=True, min_length=1, max_length=3)
    total_allocated = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    total_remainder = serializers.DecimalField(
        max_digits=14, decimal_places=2, min_value=Decimal("0")
    )
    disclaimer = serializers.CharField(max_length=1000)
    saved = serializers.BooleanField(required=True)
    # Read old chat cards without treating their former wording as a second
    # save control. New snapshots are canonicalized to `saved` only.
    confirmed = serializers.BooleanField(required=False, write_only=True)

    def validate(self, attrs):
        if attrs["saved"] is not True:
            raise serializers.ValidationError({"saved": "The scenario must be explicitly saved."})

        allocations = attrs["allocations"]
        ids = [item["instrument_id"] for item in allocations]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError({"allocations": "Instruments must be unique."})

        priorities = [item.get("priority") for item in allocations]
        if any(priority is not None for priority in priorities):
            if any(priority is None for priority in priorities) or len(set(priorities)) != len(
                priorities
            ):
                raise serializers.ValidationError(
                    {"allocations": "Priorities must be present and unique for every item."}
                )

        percentage_total = sum((item["percentage"] for item in allocations), Decimal("0"))
        if percentage_total != Decimal("100.00"):
            raise serializers.ValidationError(
                {"allocations": "Allocation percentages must total exactly 100%."}
            )

        now = timezone.now()
        for item in allocations:
            if item["observed_at"] > now + timedelta(minutes=5):
                raise serializers.ValidationError(
                    {"allocations": "A quote timestamp cannot be in the future."}
                )

            expected_target = (
                attrs["confirmed_amount"] * item["percentage"] / Decimal("100")
            ).quantize(Decimal("0.01"))
            if abs(item["target_amount"] - expected_target) > MONEY_TOLERANCE:
                raise serializers.ValidationError(
                    {"allocations": "An allocation target does not match its percentage."}
                )

            increment = item["minimum_increment"]
            steps = (item["target_amount"] / item["unit_price"] / increment).to_integral_value(
                rounding=ROUND_DOWN
            )
            expected_quantity = steps * increment
            allow_under_allocation = self.context.get("allow_under_allocation_quantity", False)
            quantity_is_valid_edit = (
                allow_under_allocation
                and item["quantity"] <= expected_quantity
                and item["quantity"] % increment == 0
            )
            if item["quantity"] != expected_quantity and not quantity_is_valid_edit:
                raise serializers.ValidationError(
                    {"allocations": "A quantity does not respect its purchase increment."}
                )

            expected_actual = (item["quantity"] * item["unit_price"]).quantize(Decimal("0.01"))
            if abs(item["actual_allocated_amount"] - expected_actual) > MONEY_TOLERANCE:
                raise serializers.ValidationError(
                    {"allocations": "An allocated amount does not match its quantity and quote."}
                )

            expected_remainder = item["target_amount"] - item["actual_allocated_amount"]
            if abs(item["unallocated_remainder"] - expected_remainder) > MONEY_TOLERANCE:
                raise serializers.ValidationError(
                    {"allocations": "An instrument remainder is inconsistent."}
                )

        total_allocated = sum(
            (item["actual_allocated_amount"] for item in allocations), Decimal("0")
        )
        if abs(attrs["total_allocated"] - total_allocated) > MONEY_TOLERANCE:
            raise serializers.ValidationError(
                {"total_allocated": "The allocated total is inconsistent."}
            )
        if (
            abs(attrs["confirmed_amount"] - attrs["total_allocated"] - attrs["total_remainder"])
            > MONEY_TOLERANCE
        ):
            raise serializers.ValidationError(
                {"total_remainder": "The cash remainder is inconsistent."}
            )

        instruments = {
            instrument.id: instrument
            for instrument in InvestmentInstrument.objects.select_related("product").filter(
                id__in=ids, is_active=True, product__is_active=True
            )
        }
        if len(instruments) != len(ids):
            raise serializers.ValidationError(
                {"allocations": "Every saved instrument must still be curated and active."}
            )
        for item in allocations:
            instrument = instruments[item["instrument_id"]]
            expected = {
                "instrument_code": instrument.code,
                "asset_class": instrument.asset_class,
                "price_currency": instrument.price_currency,
                "unit": instrument.unit,
                "price_type": instrument.price_type,
                "minimum_increment": instrument.minimum_increment,
            }
            for field, value in expected.items():
                if item[field] != value:
                    raise serializers.ValidationError(
                        {
                            "allocations": (
                                f"Instrument metadata does not match the catalogue ({field})."
                            )
                        }
                    )

        return attrs


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        utc = value.astimezone(datetime_timezone.utc)
        return utc.isoformat().replace("+00:00", "Z")
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items() if key != "confirmed"}
    return value


def validate_saved_payload(payload: Any, original_payload: Any) -> dict[str, Any]:
    """Validate calculations and prove immutable quote data came from the widget."""

    serializer = InvestmentScenarioPayloadSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    incoming = serializer.validated_data

    original_data = dict(original_payload) if isinstance(original_payload, dict) else {}
    # The assistant's initial plan predates the save action, so validate it
    # through the same contract after adding the one action-owned field.
    original_data["saved"] = True
    original_serializer = InvestmentScenarioPayloadSerializer(data=original_data)
    original_serializer.is_valid(raise_exception=True)
    original = original_serializer.validated_data

    for field in IMMUTABLE_PLAN_FIELDS:
        if incoming[field] != original[field]:
            raise serializers.ValidationError(
                {"payload": f"The saved scenario changed immutable plan data ({field})."}
            )

    incoming_allocations = {item["instrument_id"]: item for item in incoming["allocations"]}
    original_allocations = {item["instrument_id"]: item for item in original["allocations"]}
    if incoming_allocations.keys() != original_allocations.keys():
        raise serializers.ValidationError(
            {"payload": "The saved scenario changed the quoted instrument set."}
        )
    for instrument_id, item in incoming_allocations.items():
        original_item = original_allocations[instrument_id]
        for field in IMMUTABLE_ALLOCATION_FIELDS:
            if item.get(field) != original_item.get(field):
                raise serializers.ValidationError(
                    {"payload": f"The saved scenario changed immutable quote data ({field})."}
                )

    return _json_value(incoming)


class SavedInvestmentAllocationStateSerializer(serializers.Serializer):
    instrument_id = serializers.UUIDField()
    state = serializers.ChoiceField(choices=("planned", "purchased"))
    holding_id = serializers.UUIDField(allow_null=True)
    recorded_at = serializers.DateTimeField(allow_null=True)


class SavedInvestmentAllocationPurchaseSerializer(serializers.Serializer):
    quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=8,
        min_value=Decimal("0.00000001"),
    )
    unit_price = serializers.DecimalField(
        max_digits=20,
        decimal_places=4,
        min_value=Decimal("0.0001"),
    )
    fees = serializers.DecimalField(
        max_digits=20,
        decimal_places=2,
        min_value=Decimal("0"),
        required=False,
        default=Decimal("0"),
    )
    purchased_at = serializers.DateField()

    def validate_purchased_at(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError("Purchase date cannot be in the future.")
        return value


class SavedInvestmentAllocationUpdateSerializer(serializers.Serializer):
    target_amount = serializers.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
    )
    quantity = serializers.DecimalField(
        max_digits=24,
        decimal_places=8,
        min_value=Decimal("0.00000001"),
        required=False,
    )
    unit_price = serializers.DecimalField(
        max_digits=20,
        decimal_places=4,
        min_value=Decimal("0.0001"),
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Change the planned amount, quantity, or price.")
        return attrs


def _recalculate_saved_payload(updated: dict[str, Any]) -> dict[str, Any]:
    allocations = updated.get("allocations", [])
    if not allocations:
        raise serializers.ValidationError({"allocations": "A plan cannot be empty."})

    confirmed_amount = sum(
        (Decimal(str(item["target_amount"])) for item in allocations),
        Decimal("0"),
    ).quantize(Decimal("0.01"))

    percentage_used = Decimal("0")
    total_allocated = Decimal("0")
    for index, allocation in enumerate(allocations):
        target = Decimal(str(allocation["target_amount"])).quantize(Decimal("0.01"))
        if index == len(allocations) - 1:
            percentage = Decimal("100.00") - percentage_used
        else:
            percentage = (target / confirmed_amount * Decimal("100")).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            percentage_used += percentage

        price = Decimal(str(allocation["unit_price"]))
        increment = Decimal(str(allocation["minimum_increment"]))
        quantity = Decimal(str(allocation["quantity"]))
        if quantity % increment != 0:
            raise serializers.ValidationError(
                {"quantity": ("Quantity must use increments of " f"{increment.normalize()}.")}
            )
        allocated = (quantity * price).quantize(Decimal("0.01"))
        if allocated > target:
            raise serializers.ValidationError(
                {"quantity": "Quantity and price cannot exceed the planned amount."}
            )

        allocation["percentage"] = percentage
        allocation["target_amount"] = target
        allocation["quantity"] = quantity
        allocation["actual_allocated_amount"] = allocated
        allocation["unallocated_remainder"] = target - allocated
        total_allocated += allocated

    updated["confirmed_amount"] = confirmed_amount
    updated["total_allocated"] = total_allocated
    updated["total_remainder"] = confirmed_amount - total_allocated
    updated["saved"] = True

    serializer = InvestmentScenarioPayloadSerializer(
        data=updated,
        context={"allow_under_allocation_quantity": True},
    )
    serializer.is_valid(raise_exception=True)
    return _json_value(serializer.validated_data)


def update_saved_allocation(
    payload: dict[str, Any], instrument_id: UUID, changes: dict[str, Decimal]
) -> dict[str, Any]:
    """Edit a pending plan item and deterministically recalculate the plan."""

    updated = deepcopy(payload)
    selected = next(
        (
            allocation
            for allocation in updated.get("allocations", [])
            if str(allocation.get("instrument_id")) == str(instrument_id)
        ),
        None,
    )
    if selected is None:
        raise serializers.ValidationError(
            {"instrument_id": "This investment is not part of the saved plan."}
        )

    if "target_amount" in changes:
        selected["target_amount"] = changes["target_amount"]
    if "unit_price" in changes:
        selected["unit_price"] = changes["unit_price"]
        selected["mode"] = "user_supplied"
        selected["source"] = "User-edited plan price"
        selected["observed_at"] = timezone.now()
    if "quantity" in changes:
        selected["quantity"] = changes["quantity"]
    elif "target_amount" in changes or "unit_price" in changes:
        target = Decimal(str(selected["target_amount"]))
        price = Decimal(str(selected["unit_price"]))
        increment = Decimal(str(selected["minimum_increment"]))
        steps = (target / price / increment).to_integral_value(rounding=ROUND_DOWN)
        selected["quantity"] = steps * increment
    return _recalculate_saved_payload(updated)


def remove_saved_allocation(payload: dict[str, Any], instrument_id: UUID) -> dict[str, Any] | None:
    """Remove a pending item; return None when it was the plan's last item."""

    updated = deepcopy(payload)
    allocations = updated.get("allocations", [])
    remaining = [
        allocation
        for allocation in allocations
        if str(allocation.get("instrument_id")) != str(instrument_id)
    ]
    if len(remaining) == len(allocations):
        raise serializers.ValidationError(
            {"instrument_id": "This investment is not part of the saved plan."}
        )
    if not remaining:
        return None
    updated["allocations"] = remaining
    return _recalculate_saved_payload(updated)


class SavedInvestmentScenarioSerializer(serializers.ModelSerializer):
    payload = serializers.JSONField(source="payload_json", read_only=True)
    source_message_id = serializers.UUIDField(read_only=True, allow_null=True)
    source_conversation_id = serializers.SerializerMethodField()
    quote_status = serializers.SerializerMethodField()
    oldest_observed_at = serializers.SerializerMethodField()
    allocation_states = serializers.SerializerMethodField()

    class Meta:
        model = SavedInvestmentScenario
        fields = [
            "id",
            "title",
            "status",
            "payload",
            "source_message_id",
            "source_conversation_id",
            "quote_status",
            "oldest_observed_at",
            "allocation_states",
            "saved_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_source_conversation_id(self, scenario: SavedInvestmentScenario) -> UUID | None:
        if scenario.source_message_id is None:
            return None
        return scenario.source_message.conversation_id

    def get_allocation_states(self, scenario: SavedInvestmentScenario) -> list[dict[str, Any]]:
        purchases = {
            str(purchase.instrument_id): purchase
            for purchase in scenario.allocation_purchases.all()
        }
        states = []
        for allocation in scenario.payload_json.get("allocations", []):
            instrument_id = str(allocation.get("instrument_id", ""))
            purchase: SavedInvestmentAllocationPurchase | None = purchases.get(instrument_id)
            states.append(
                {
                    "instrument_id": instrument_id,
                    "state": "purchased" if purchase else "planned",
                    "holding_id": purchase.holding_id if purchase else None,
                    "recorded_at": purchase.recorded_at if purchase else None,
                }
            )
        serializer = SavedInvestmentAllocationStateSerializer(data=states, many=True)
        serializer.is_valid(raise_exception=True)
        return serializer.data

    def _quote_summary(self, scenario: SavedInvestmentScenario) -> tuple[str, datetime | None]:
        if hasattr(scenario, "_quote_summary_cache"):
            return scenario._quote_summary_cache

        allocations = scenario.payload_json.get("allocations", [])
        modes = {item.get("mode") for item in allocations}
        observed_values = []
        instrument_ids = []
        for item in allocations:
            try:
                instrument_ids.append(UUID(str(item["instrument_id"])))
                observed_values.append(
                    datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
                )
            except (KeyError, TypeError, ValueError):
                summary = ("unavailable", None)
                scenario._quote_summary_cache = summary
                return summary

        if "mock" in modes:
            summary = ("mock", min(observed_values, default=None))
        elif "user_supplied" in modes:
            summary = ("user_supplied", min(observed_values, default=None))
        else:
            instruments = {
                item.id: item
                for item in InvestmentInstrument.objects.filter(
                    id__in=instrument_ids, is_active=True, product__is_active=True
                )
            }
            if len(instruments) != len(instrument_ids):
                summary = ("unavailable", min(observed_values, default=None))
            else:
                now = timezone.now()
                stale = any(
                    now - observed_at
                    > timedelta(seconds=instruments[instrument_id].max_quote_age_seconds)
                    for instrument_id, observed_at in zip(
                        instrument_ids, observed_values, strict=True
                    )
                )
                summary = (
                    "needs_refresh" if stale else "current",
                    min(observed_values, default=None),
                )

        scenario._quote_summary_cache = summary
        return summary

    def get_quote_status(self, scenario: SavedInvestmentScenario) -> str:
        return self._quote_summary(scenario)[0]

    def get_oldest_observed_at(self, scenario: SavedInvestmentScenario) -> str | None:
        observed_at = self._quote_summary(scenario)[1]
        return observed_at.isoformat().replace("+00:00", "Z") if observed_at else None


class SavedInvestmentScenarioUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedInvestmentScenario
        fields = ["title", "status"]
        extra_kwargs = {"title": {"required": False}, "status": {"required": False}}

    def validate_title(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Title cannot be blank.")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Provide a title or status to update.")
        return attrs
