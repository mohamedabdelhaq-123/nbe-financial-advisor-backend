from decimal import Decimal

from django.utils import timezone
from rest_framework import serializers

from core.models import InvestmentHolding, InvestmentInstrument


class HoldingInstrumentSerializer(serializers.ModelSerializer):
    display_name = serializers.CharField(source="product.title", read_only=True)

    class Meta:
        model = InvestmentInstrument
        fields = (
            "id",
            "code",
            "display_name",
            "asset_class",
            "price_currency",
            "unit",
            "price_type",
            "minimum_increment",
            "fractional_units_supported",
        )


class InvestmentHoldingSerializer(serializers.ModelSerializer):
    instrument = HoldingInstrumentSerializer(read_only=True)
    cost_basis = serializers.SerializerMethodField()

    class Meta:
        model = InvestmentHolding
        fields = (
            "id",
            "instrument",
            "quantity",
            "average_purchase_price",
            "fees",
            "purchased_at",
            "cost_basis",
            "created_at",
            "updated_at",
        )

    def get_cost_basis(self, holding: InvestmentHolding) -> Decimal:
        return holding.quantity * holding.average_purchase_price + holding.fees


class InvestmentHoldingWriteSerializer(serializers.ModelSerializer):
    instrument_id = serializers.PrimaryKeyRelatedField(
        source="instrument",
        queryset=InvestmentInstrument.objects.filter(is_active=True).select_related("product"),
        required=False,
    )

    class Meta:
        model = InvestmentHolding
        fields = (
            "instrument_id",
            "quantity",
            "average_purchase_price",
            "fees",
            "purchased_at",
        )
        extra_kwargs = {
            "quantity": {"min_value": Decimal("0.00000001")},
            "average_purchase_price": {"min_value": Decimal("0.0001")},
            "fees": {"min_value": Decimal("0"), "required": False},
        }

    def validate(self, attrs):
        purchased_at = attrs.get("purchased_at")
        if purchased_at and purchased_at > timezone.localdate():
            raise serializers.ValidationError(
                {"purchased_at": "Purchase date cannot be in the future."}
            )

        request = self.context["request"]
        instrument = attrs.get("instrument")
        if self.instance is None:
            if instrument is None:
                raise serializers.ValidationError({"instrument_id": "This field is required."})
            if InvestmentHolding.objects.filter(user=request.user, instrument=instrument).exists():
                raise serializers.ValidationError(
                    {
                        "instrument_id": (
                            "You already track this opportunity. Edit the existing holding "
                            "and enter your total quantity and average price."
                        )
                    }
                )
        elif instrument is not None and instrument.id != self.instance.instrument_id:
            raise serializers.ValidationError(
                {"instrument_id": "The opportunity cannot be changed; create another holding."}
            )

        effective_instrument = instrument or (self.instance.instrument if self.instance else None)
        quantity = attrs.get("quantity")
        if quantity is not None and effective_instrument:
            if quantity % effective_instrument.minimum_increment != 0:
                raise serializers.ValidationError(
                    {
                        "quantity": (
                            "Quantity must use increments of "
                            f"{effective_instrument.minimum_increment.normalize()}."
                        )
                    }
                )
        return attrs


class HoldingValuationItemSerializer(serializers.Serializer):
    holding = InvestmentHoldingSerializer()
    quote_status = serializers.ChoiceField(
        choices=("current", "needs_refresh", "unavailable", "disabled")
    )
    current_price = serializers.DecimalField(max_digits=20, decimal_places=4, allow_null=True)
    current_value = serializers.DecimalField(max_digits=28, decimal_places=2, allow_null=True)
    gain_loss = serializers.DecimalField(max_digits=28, decimal_places=2, allow_null=True)
    gain_loss_percentage = serializers.DecimalField(
        max_digits=16, decimal_places=4, allow_null=True
    )
    observed_at = serializers.DateTimeField(allow_null=True)
    source = serializers.CharField(allow_null=True)


class HoldingValuationResponseSerializer(serializers.Serializer):
    feature_status = serializers.ChoiceField(choices=("enabled", "disabled"))
    refreshed_at = serializers.DateTimeField()
    is_complete = serializers.BooleanField()
    priced_holding_count = serializers.IntegerField(min_value=0)
    total_holding_count = serializers.IntegerField(min_value=0)
    total_cost_basis = serializers.DecimalField(max_digits=28, decimal_places=2)
    total_current_value = serializers.DecimalField(max_digits=28, decimal_places=2, allow_null=True)
    total_gain_loss = serializers.DecimalField(max_digits=28, decimal_places=2, allow_null=True)
    total_gain_loss_percentage = serializers.DecimalField(
        max_digits=16, decimal_places=4, allow_null=True
    )
    holdings = HoldingValuationItemSerializer(many=True)
