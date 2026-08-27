from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from core.models import AdminUser, InvestmentInstrument, Product


@pytest.fixture
def super_admin(db):
    return AdminUser.objects.create(
        name="Investment Admin",
        email="investment-admin@example.com",
        role="super_admin",
    )


@pytest.fixture
def client(super_admin):
    api_client = APIClient()
    api_client.force_authenticate(user=super_admin)
    return api_client


@pytest.fixture
def product(db):
    return Product.objects.create(
        title="24K Gold",
        categories=["investments", "gold"],
        tags=["commodity"],
        is_active=True,
    )


def _payload(product):
    return {
        "product_id": str(product.id),
        "code": "gold-24k-gram-egp",
        "asset_class": "gold",
        "provider_symbol": "XAU_EGP_GRAM_24K",
        "price_type": "spot",
        "price_currency": "egp",
        "unit": "gram_24k",
        "minimum_increment": "0.01000000",
        "fractional_units_supported": True,
        "max_quote_age_seconds": 900,
        "is_active": True,
    }


@pytest.mark.django_db
def test_super_admin_can_create_and_list_curated_instrument(client, product):
    response = client.post("/admin/investment-instruments/", _payload(product), format="json")

    assert response.status_code == 201
    assert response.data["price_currency"] == "EGP"
    assert response.data["product_title"] == "24K Gold"
    instrument = InvestmentInstrument.objects.get(code="gold-24k-gram-egp")
    assert instrument.minimum_increment == Decimal("0.01000000")

    listed = client.get("/admin/investment-instruments/?asset_class=gold&is_active=true")
    assert listed.status_code == 200
    assert listed.data["count"] == 1
    assert listed.data["results"][0]["id"] == response.data["id"]


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("asset_class", "price_type", "expected"),
    [
        ("gold", "nav", "gold instruments must use spot"),
        ("fund", "spot", "fund instruments must use nav"),
        ("currency", "spot", "currency instruments must use customer_buy_rate"),
    ],
)
def test_asset_class_rejects_wrong_price_semantics(
    client,
    product,
    asset_class,
    price_type,
    expected,
):
    payload = _payload(product)
    payload.update({"asset_class": asset_class, "price_type": price_type})

    response = client.post("/admin/investment-instruments/", payload, format="json")

    assert response.status_code == 422
    assert expected in str(response.data)


@pytest.mark.django_db
def test_product_response_embeds_curated_instrument(client, product):
    created = client.post("/admin/investment-instruments/", _payload(product), format="json")
    assert created.status_code == 201

    response = client.get("/admin/products/")

    assert response.status_code == 200
    row = next(item for item in response.data["results"] if item["id"] == str(product.id))
    assert row["investment_instrument"]["code"] == "gold-24k-gram-egp"


@pytest.mark.django_db
def test_instrument_patch_revalidates_combined_state(client, product):
    created = client.post("/admin/investment-instruments/", _payload(product), format="json")

    response = client.patch(
        f"/admin/investment-instruments/{created.data['id']}/",
        {"price_type": "nav"},
        format="json",
    )

    assert response.status_code == 422
    assert "gold instruments must use spot" in str(response.data)


@pytest.mark.django_db
def test_gold_unit_must_include_purity(client, product):
    payload = _payload(product)
    payload["unit"] = "gram"

    response = client.post("/admin/investment-instruments/", payload, format="json")

    assert response.status_code == 422
    assert "must identify grams and purity" in str(response.data)


@pytest.mark.django_db
def test_non_fractional_instrument_requires_whole_increment(client, product):
    payload = _payload(product)
    payload.update(
        {
            "asset_class": "fund",
            "price_type": "nav",
            "unit": "fund_unit",
            "minimum_increment": "0.50000000",
            "fractional_units_supported": False,
        }
    )

    response = client.post("/admin/investment-instruments/", payload, format="json")

    assert response.status_code == 422
    assert "whole quantity" in str(response.data)
