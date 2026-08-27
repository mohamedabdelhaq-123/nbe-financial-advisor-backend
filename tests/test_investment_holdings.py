from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import InvestmentHolding, InvestmentInstrument, Product, User


@pytest.fixture
def user(db):
    return User.objects.create_user(email="holder@example.com", password="x", name="Holder")


@pytest.fixture
def other_user(db):
    return User.objects.create_user(email="other-holder@example.com", password="x", name="Other")


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


@pytest.fixture
def gold(db):
    product = Product.objects.create(
        title="24K Gold", categories=["investments", "gold"], tags=["gold"], is_active=True
    )
    return InvestmentInstrument.objects.create(
        product=product,
        code="gold-holding-test",
        asset_class="gold",
        provider_symbol="XAU_EGP_GRAM_24K",
        price_type="spot",
        price_currency="EGP",
        unit="gram_24k",
        minimum_increment=Decimal("0.01"),
        fractional_units_supported=True,
        max_quote_age_seconds=900,
    )


@pytest.fixture
def fund(db):
    product = Product.objects.create(
        title="EGX30 ETF", categories=["investments", "fund"], tags=["fund"], is_active=True
    )
    return InvestmentInstrument.objects.create(
        product=product,
        code="fund-holding-test",
        asset_class="fund",
        provider_symbol="EGX30ETF_MARKET_PRICE",
        price_type="market_price",
        price_currency="EGP",
        unit="fund_unit",
        minimum_increment=Decimal("1"),
        fractional_units_supported=False,
        max_quote_age_seconds=259200,
    )


def _create(client, instrument, **overrides):
    payload = {
        "instrument_id": str(instrument.id),
        "quantity": 2,
        "average_purchase_price": 100,
        "fees": 5,
        "purchased_at": "2026-08-01",
        **overrides,
    }
    return client.post("/investment-holdings/", payload, format="json")


@pytest.mark.django_db
def test_holding_can_be_created_edited_and_deleted(client, user, gold):
    created = _create(client, gold)

    assert created.status_code == 201
    assert Decimal(created.data["cost_basis"]) == Decimal("205")
    holding_id = created.data["id"]

    updated = client.patch(
        f"/investment-holdings/{holding_id}/",
        {"quantity": 3, "average_purchase_price": 90, "fees": 6},
        format="json",
    )
    assert updated.status_code == 200
    assert Decimal(updated.data["cost_basis"]) == Decimal("276")

    assert client.delete(f"/investment-holdings/{holding_id}/").status_code == 204
    assert not InvestmentHolding.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_holding_validation_rejects_duplicate_nonpositive_and_future_date(client, gold):
    assert _create(client, gold).status_code == 201
    assert _create(client, gold).status_code == 422
    assert _create(client, gold, quantity=0).status_code == 422
    future = (timezone.localdate() + timedelta(days=1)).isoformat()
    other_product = Product.objects.create(
        title="Other Gold", categories=["investments"], tags=[], is_active=True
    )
    other = InvestmentInstrument.objects.create(
        product=other_product,
        code="other-gold",
        asset_class="gold",
        provider_symbol="OTHER",
        price_type="spot",
        unit="gram_24k",
    )
    assert _create(client, other, purchased_at=future).status_code == 422


@pytest.mark.django_db
def test_holding_quantity_respects_instrument_increment(client, fund):
    response = _create(client, fund, quantity=Decimal("0.5"))

    assert response.status_code == 422
    assert "increments of 1" in str(response.data)


@pytest.mark.django_db
def test_holding_isolation_prevents_other_user_read_or_edit(client, other_user, gold):
    other_holding = InvestmentHolding.objects.create(
        user=other_user,
        instrument=gold,
        quantity=1,
        average_purchase_price=100,
    )

    assert client.get("/investment-holdings/").data == []
    assert client.get(f"/investment-holdings/{other_holding.id}/").status_code == 404
    assert (
        client.patch(
            f"/investment-holdings/{other_holding.id}/", {"quantity": 2}, format="json"
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_disabled_valuation_never_calls_gateway(client, user, gold, settings, monkeypatch):
    InvestmentHolding.objects.create(
        user=user, instrument=gold, quantity=2, average_purchase_price=100
    )
    settings.MARKET_DATA_ENABLED = False

    def fail(_):
        raise AssertionError("disabled pricing must make no provider request")

    monkeypatch.setattr("core.views.investment_holdings.fetch_market_quotes", fail)
    response = client.get("/investment-holdings/valuation/")

    assert response.status_code == 200
    assert response.data["feature_status"] == "disabled"
    assert response.data["holdings"][0]["quote_status"] == "disabled"
    assert response.data["holdings"][0]["gain_loss"] is None


@pytest.mark.django_db
def test_live_valuation_calculates_profit_including_fees(client, user, gold, settings, monkeypatch):
    InvestmentHolding.objects.create(
        user=user,
        instrument=gold,
        quantity=Decimal("2"),
        average_purchase_price=Decimal("100"),
        fees=Decimal("5"),
    )
    settings.MARKET_DATA_ENABLED = True
    monkeypatch.setattr(
        "core.views.investment_holdings.fetch_market_quotes",
        lambda instruments: {
            gold.id: {
                "price": Decimal("120"),
                "observed_at": timezone.now(),
                "source": "Test live source",
            }
        },
    )

    response = client.get("/investment-holdings/valuation/")

    assert response.status_code == 200
    row = response.data["holdings"][0]
    assert row["quote_status"] == "current"
    assert Decimal(row["current_value"]) == Decimal("240")
    assert Decimal(row["gain_loss"]) == Decimal("35")
    assert Decimal(response.data["total_gain_loss"]) == Decimal("35")
    assert response.data["is_complete"] is True


@pytest.mark.django_db
def test_stale_quote_is_labelled_but_partial_quote_hides_portfolio_totals(
    client, user, gold, fund, settings, monkeypatch
):
    for instrument in (gold, fund):
        InvestmentHolding.objects.create(
            user=user, instrument=instrument, quantity=1, average_purchase_price=100
        )
    settings.MARKET_DATA_ENABLED = True
    monkeypatch.setattr(
        "core.views.investment_holdings.fetch_market_quotes",
        lambda instruments: {
            gold.id: {
                "price": Decimal("90"),
                "observed_at": timezone.now() - timedelta(hours=2),
                "source": "Delayed test source",
            }
        },
    )

    response = client.get("/investment-holdings/valuation/")

    by_id = {row["holding"]["instrument"]["id"]: row for row in response.data["holdings"]}
    assert by_id[str(gold.id)]["quote_status"] == "needs_refresh"
    assert Decimal(by_id[str(gold.id)]["gain_loss"]) == Decimal("-10")
    assert by_id[str(fund.id)]["quote_status"] == "unavailable"
    assert response.data["is_complete"] is False
    assert response.data["total_current_value"] is None
    assert response.data["total_gain_loss"] is None


@pytest.mark.django_db
def test_curated_instrument_list_is_authenticated_and_active_only(client, gold):
    gold.is_active = False
    gold.save(update_fields=["is_active"])
    assert client.get("/investment-instruments/").data == []

    anonymous = APIClient()
    assert anonymous.get("/investment-instruments/").status_code == 401
