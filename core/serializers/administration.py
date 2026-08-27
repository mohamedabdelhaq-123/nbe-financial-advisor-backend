import re

from rest_framework import serializers

from core.models import InvestmentInstrument, Product, Reaction, ReportedIssue


class AdminInvestmentInstrumentSerializer(serializers.ModelSerializer):
    product_id = serializers.PrimaryKeyRelatedField(
        source="product",
        queryset=Product.objects.all(),
    )
    product_title = serializers.CharField(source="product.title", read_only=True)

    class Meta:
        model = InvestmentInstrument
        fields = [
            "id",
            "product_id",
            "product_title",
            "code",
            "asset_class",
            "provider_symbol",
            "price_type",
            "price_currency",
            "unit",
            "minimum_increment",
            "fractional_units_supported",
            "max_quote_age_seconds",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "product_title", "created_at", "updated_at"]

    def validate(self, attrs):
        instance = self.instance

        def value(name):
            if name in attrs:
                return attrs[name]
            return getattr(instance, name, None) if instance is not None else None

        asset_class = value("asset_class")
        price_type = value("price_type")
        expected_price_types = {
            InvestmentInstrument.AssetClass.GOLD: {InvestmentInstrument.PriceType.SPOT},
            InvestmentInstrument.AssetClass.FUND: {
                InvestmentInstrument.PriceType.NAV,
                InvestmentInstrument.PriceType.MARKET_PRICE,
            },
            InvestmentInstrument.AssetClass.CURRENCY: {
                InvestmentInstrument.PriceType.CUSTOMER_BUY_RATE
            },
        }
        expected = expected_price_types.get(asset_class)
        if expected is not None and price_type not in expected:
            allowed = {
                InvestmentInstrument.AssetClass.GOLD: "spot",
                InvestmentInstrument.AssetClass.FUND: "nav or market_price",
                InvestmentInstrument.AssetClass.CURRENCY: "customer_buy_rate",
            }[asset_class]
            raise serializers.ValidationError(
                {"price_type": f"{asset_class} instruments must use {allowed}."}
            )

        price_currency = value("price_currency")
        if price_currency:
            normalized_currency = str(price_currency).upper()
            if normalized_currency != "EGP":
                raise serializers.ValidationError(
                    {"price_currency": "The first release supports EGP pricing only."}
                )
            attrs["price_currency"] = normalized_currency

        minimum_increment = value("minimum_increment")
        if minimum_increment is not None and minimum_increment <= 0:
            raise serializers.ValidationError({"minimum_increment": "Must be greater than zero."})
        fractional_units_supported = value("fractional_units_supported")
        if (
            minimum_increment is not None
            and not fractional_units_supported
            and minimum_increment != minimum_increment.to_integral_value()
        ):
            raise serializers.ValidationError(
                {
                    "minimum_increment": (
                        "Must be a whole quantity when fractional units are disabled."
                    )
                }
            )

        unit = value("unit")
        if unit:
            normalized_unit = str(unit).strip()
            if asset_class == InvestmentInstrument.AssetClass.GOLD and not re.fullmatch(
                r"gram_(?:24k|21k|18k)", normalized_unit.lower()
            ):
                raise serializers.ValidationError(
                    {"unit": "Gold units must identify grams and purity, such as gram_24k."}
                )
            if (
                asset_class == InvestmentInstrument.AssetClass.FUND
                and normalized_unit != "fund_unit"
            ):
                raise serializers.ValidationError({"unit": "Fund instruments must use fund_unit."})
            if asset_class == InvestmentInstrument.AssetClass.CURRENCY:
                normalized_unit = normalized_unit.upper()
                if not re.fullmatch(r"[A-Z]{3}", normalized_unit):
                    raise serializers.ValidationError(
                        {"unit": "Currency units must be a three-letter ISO code."}
                    )
                attrs["unit"] = normalized_unit

        max_quote_age_seconds = value("max_quote_age_seconds")
        if max_quote_age_seconds is not None and max_quote_age_seconds <= 0:
            raise serializers.ValidationError(
                {"max_quote_age_seconds": "Must be greater than zero."}
            )
        return attrs


class AdminLoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, trim_whitespace=False)


class AdminLoginResponseSerializer(serializers.Serializer):
    """Shared by POST /admin/auth/login and /admin/auth/refresh — no
    refresh_token field, it's an httpOnly cookie. admin_id/role included on
    both so the UI can permission-gate right after a reload restore."""

    access_token = serializers.CharField()
    admin_id = serializers.UUIDField()
    role = serializers.CharField()


class AdminReactionSerializer(serializers.ModelSerializer):
    """
    Admin-facing feedback row — cross-user, so unlike the end-user-facing
    Feedback domain's ReactionSerializer, this exposes `user_id`. It's an
    opaque reference only (a UUID, not an expanded user profile) — the
    Administration domain doesn't own or expose any user profile data
    itself, it just needs to say whose feedback this was.
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
    investment_instrument = AdminInvestmentInstrumentSerializer(read_only=True)

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
            "investment_instrument",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]


class AdminProductCreateSerializer(serializers.ModelSerializer):
    # Seed text(s) for embedding generation (AI service /internal/embeddings,
    # via services/ai_service.py's create_embeddings()) — not a Product
    # field itself, popped and handled separately by the view.
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
    """Any subset of the writable product fields. No `problem_statements`
    here — seeding a product's matching text only happens at creation time
    (POST /admin/products); there's no endpoint to add more afterward."""

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
