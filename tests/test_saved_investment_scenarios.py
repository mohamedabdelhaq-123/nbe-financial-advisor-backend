from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import (
    Conversation,
    InvestmentHolding,
    InvestmentInstrument,
    Message,
    Product,
    SavedInvestmentAllocationPurchase,
    SavedInvestmentScenario,
    User,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="saved-scenario@example.com", password="x", name="Scenario User"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def _instrument(code, title, asset_class, price_type, unit, increment, max_age=3600):
    product = Product.objects.create(
        title=title,
        categories=["investments", asset_class],
        tags=[asset_class],
        is_active=True,
    )
    return InvestmentInstrument.objects.create(
        product=product,
        code=code,
        asset_class=asset_class,
        provider_symbol=code.upper(),
        price_type=price_type,
        unit=unit,
        minimum_increment=increment,
        fractional_units_supported=increment < 1,
        max_quote_age_seconds=max_age,
    )


@pytest.fixture
def scenario_message(user):
    gold = _instrument("gold-24k-gram-egp", "24K Gold", "gold", "spot", "gram_24k", Decimal("0.01"))
    fund = _instrument(
        "egx30-etf",
        "EGX30 ETF",
        "fund",
        "market_price",
        "fund_unit",
        Decimal("1"),
    )
    observed_at = (timezone.now() - timedelta(minutes=5)).isoformat()
    payload = {
        "confirmed_amount": 1200,
        "currency": "EGP",
        "allocations": [
            {
                "instrument_id": str(gold.id),
                "instrument_code": gold.code,
                "display_name": gold.product.title,
                "asset_class": "gold",
                "percentage": 50,
                "target_amount": 600,
                "unit_price": 100,
                "price_currency": "EGP",
                "unit": "gram_24k",
                "price_type": "spot",
                "minimum_increment": 0.01,
                "quantity": 6,
                "actual_allocated_amount": 600,
                "unallocated_remainder": 0,
                "observed_at": observed_at,
                "source": "Live gold source",
                "mode": "live",
                "priority": 1,
                "match_factors": ["objective", "risk", "horizon"],
            },
            {
                "instrument_id": str(fund.id),
                "instrument_code": fund.code,
                "display_name": fund.product.title,
                "asset_class": "fund",
                "percentage": 50,
                "target_amount": 600,
                "unit_price": 250,
                "price_currency": "EGP",
                "unit": "fund_unit",
                "price_type": "market_price",
                "minimum_increment": 1,
                "quantity": 2,
                "actual_allocated_amount": 500,
                "unallocated_remainder": 100,
                "observed_at": observed_at,
                "source": "Official fund NAV",
                "mode": "live",
                "priority": 2,
                "match_factors": ["objective"],
            },
        ],
        "total_allocated": 1100,
        "total_remainder": 100,
        "disclaimer": "Illustrative only. No trade is executed.",
    }
    conversation = Conversation.objects.create(user=user)
    message = Message.objects.create(
        conversation=conversation,
        sender="assistant",
        content="Here is the illustrative scenario.",
        widget_json={"type": "investment_plan", "payload": payload},
    )
    return conversation, message, payload


@pytest.mark.django_db
def test_saving_widget_creates_one_idempotent_scenario(client, scenario_message):
    conversation, message, payload = scenario_message
    saved_payload = {**deepcopy(payload), "saved": True}
    url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"

    first = client.patch(url, {"payload": saved_payload}, format="json")
    second = client.patch(url, {"payload": saved_payload}, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert SavedInvestmentScenario.objects.count() == 1
    scenario = SavedInvestmentScenario.objects.get()
    assert scenario.payload_json["saved"] is True
    assert scenario.payload_json["total_remainder"] == 100.0

    listed = client.get("/investment-scenarios/")
    assert listed.status_code == 200
    assert listed.data["count"] == 1
    assert listed.data["results"][0]["source_conversation_id"] == conversation.id
    assert listed.data["results"][0]["quote_status"] == "current"
    assert [item["state"] for item in listed.data["results"][0]["allocation_states"]] == [
        "planned",
        "planned",
    ]


@pytest.mark.django_db
def test_recording_planned_purchase_creates_holding_and_is_idempotent(
    client, user, scenario_message
):
    conversation, message, payload = scenario_message
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]
    purchase_url = f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/"
    purchase = {
        "quantity": "1.25000000",
        "unit_price": "110.0000",
        "fees": "3.50",
        "purchased_at": "2026-08-01",
    }

    first = client.post(purchase_url, purchase, format="json")
    second = client.post(purchase_url, purchase, format="json")

    assert first.status_code == 201
    assert second.status_code == 200
    assert InvestmentHolding.objects.count() == 1
    holding = InvestmentHolding.objects.get()
    assert holding.user == user
    assert holding.quantity == Decimal("1.25000000")
    assert holding.average_purchase_price == Decimal("110.0000")
    assert holding.fees == Decimal("3.50")
    assert SavedInvestmentAllocationPurchase.objects.count() == 1
    by_id = {item["instrument_id"]: item for item in second.data["allocation_states"]}
    assert by_id[gold_id]["state"] == "purchased"
    assert by_id[gold_id]["holding_id"] == str(holding.id)
    assert sum(item["state"] == "planned" for item in second.data["allocation_states"]) == 1


@pytest.mark.django_db
def test_pending_allocation_amount_can_be_edited_and_recalculated(client, scenario_message):
    conversation, message, payload = scenario_message
    client.patch(
        f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/",
        {"payload": {**payload, "saved": True}},
        format="json",
    )
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]

    response = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/",
        {"target_amount": 900, "unit_price": 120, "quantity": 5},
        format="json",
    )

    assert response.status_code == 200
    saved = response.data["payload"]
    by_id = {item["instrument_id"]: item for item in saved["allocations"]}
    assert Decimal(str(saved["confirmed_amount"])) == Decimal("1500")
    assert Decimal(str(saved["total_allocated"])) == Decimal("1100")
    assert Decimal(str(saved["total_remainder"])) == Decimal("400")
    assert Decimal(str(by_id[gold_id]["target_amount"])) == Decimal("900")
    assert Decimal(str(by_id[gold_id]["percentage"])) == Decimal("60")
    assert Decimal(str(by_id[gold_id]["unit_price"])) == Decimal("120")
    assert Decimal(str(by_id[gold_id]["quantity"])) == Decimal("5")
    assert Decimal(str(by_id[gold_id]["actual_allocated_amount"])) == Decimal("600")
    assert Decimal(str(by_id[gold_id]["unallocated_remainder"])) == Decimal("300")
    assert by_id[gold_id]["mode"] == "user_supplied"
    assert by_id[gold_id]["source"] == "User-edited plan price"
    assert response.data["quote_status"] == "user_supplied"
    assert not InvestmentHolding.objects.exists()

    too_much = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/",
        {"quantity": 8},
        format="json",
    )
    invalid_increment = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/",
        {"quantity": "5.005"},
        format="json",
    )
    assert too_much.status_code == 422
    assert invalid_increment.status_code == 422


@pytest.mark.django_db
def test_pending_allocations_can_be_removed_without_touching_holdings(client, scenario_message):
    conversation, message, payload = scenario_message
    client.patch(
        f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/",
        {"payload": {**payload, "saved": True}},
        format="json",
    )
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]
    fund_id = payload["allocations"][1]["instrument_id"]
    client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
        {"quantity": 1, "unit_price": 100, "fees": 0, "purchased_at": "2026-08-01"},
        format="json",
    )

    response = client.delete(f"/investment-scenarios/{scenario.id}/allocations/{fund_id}/")

    assert response.status_code == 204
    assert not SavedInvestmentScenario.objects.exists()
    assert InvestmentHolding.objects.count() == 1


@pytest.mark.django_db
def test_purchased_or_unknown_allocation_cannot_be_edited(client, scenario_message):
    conversation, message, payload = scenario_message
    client.patch(
        f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/",
        {"payload": {**payload, "saved": True}},
        format="json",
    )
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]
    client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
        {"quantity": 1, "unit_price": 100, "fees": 0, "purchased_at": "2026-08-01"},
        format="json",
    )

    purchased = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/",
        {"target_amount": 900},
        format="json",
    )
    purchased_delete = client.delete(f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/")
    unknown = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{uuid4()}/",
        {"target_amount": 900},
        format="json",
    )
    zero = client.patch(
        f"/investment-scenarios/{scenario.id}/allocations/{payload['allocations'][1]['instrument_id']}/",
        {"target_amount": 0},
        format="json",
    )

    assert purchased.status_code == 422
    assert purchased_delete.status_code == 422
    assert unknown.status_code == 422
    assert zero.status_code == 422


@pytest.mark.django_db
def test_planned_purchase_merges_existing_holding_with_weighted_average(
    client, user, scenario_message
):
    conversation, message, payload = scenario_message
    gold = InvestmentInstrument.objects.get(id=payload["allocations"][0]["instrument_id"])
    existing = InvestmentHolding.objects.create(
        user=user,
        instrument=gold,
        quantity=Decimal("2"),
        average_purchase_price=Decimal("80"),
        fees=Decimal("5"),
        purchased_at="2026-07-15",
    )
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")
    scenario = SavedInvestmentScenario.objects.get()

    response = client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold.id}/purchase/",
        {
            "quantity": 1,
            "unit_price": 100,
            "fees": 2,
            "purchased_at": "2026-08-01",
        },
        format="json",
    )

    assert response.status_code == 201
    existing.refresh_from_db()
    assert existing.quantity == Decimal("3")
    assert existing.average_purchase_price == Decimal("86.6667")
    assert existing.fees == Decimal("7")
    assert existing.purchased_at.isoformat() == "2026-07-15"


@pytest.mark.django_db
def test_planned_purchase_validates_owner_allocation_date_and_archived_state(
    client, scenario_message
):
    conversation, message, payload = scenario_message
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]
    body = {
        "quantity": 1,
        "unit_price": 100,
        "fees": 0,
        "purchased_at": (timezone.localdate() + timedelta(days=1)).isoformat(),
    }

    future = client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
        body,
        format="json",
    )
    assert future.status_code == 422

    not_in_plan = _instrument(
        "not-in-plan", "Not in plan", "gold", "spot", "gram_24k", Decimal("0.01")
    )
    body["purchased_at"] = "2026-08-01"
    invalid = client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{not_in_plan.id}/purchase/",
        body,
        format="json",
    )
    assert invalid.status_code == 422

    fund_id = payload["allocations"][1]["instrument_id"]
    invalid_increment = client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{fund_id}/purchase/",
        {**body, "quantity": 0.5},
        format="json",
    )
    assert invalid_increment.status_code == 422

    scenario.status = SavedInvestmentScenario.Status.ARCHIVED
    scenario.save(update_fields=["status"])
    archived = client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
        body,
        format="json",
    )
    assert archived.status_code == 422

    other = User.objects.create_user(email="purchase-other@example.com", password="x")
    other_client = APIClient()
    other_client.force_authenticate(user=other)
    assert (
        other_client.post(
            f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
            body,
            format="json",
        ).status_code
        == 404
    )


@pytest.mark.django_db
def test_deleting_completed_plan_keeps_actual_holding(client, scenario_message):
    conversation, message, payload = scenario_message
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")
    scenario = SavedInvestmentScenario.objects.get()
    gold_id = payload["allocations"][0]["instrument_id"]
    client.post(
        f"/investment-scenarios/{scenario.id}/allocations/{gold_id}/purchase/",
        {"quantity": 1, "unit_price": 100, "fees": 0, "purchased_at": "2026-08-01"},
        format="json",
    )

    assert client.delete(f"/investment-scenarios/{scenario.id}/").status_code == 204
    assert InvestmentHolding.objects.count() == 1
    assert not SavedInvestmentAllocationPurchase.objects.exists()


@pytest.mark.django_db
def test_save_rejects_changed_quote_identity(client, scenario_message):
    conversation, message, payload = scenario_message
    tampered = {**deepcopy(payload), "saved": True}
    tampered["allocations"][0]["unit_price"] = 1
    tampered["allocations"][0]["quantity"] = 600
    url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"

    response = client.patch(url, {"payload": tampered}, format="json")

    assert response.status_code == 422
    assert "immutable quote data" in str(response.data)
    assert not SavedInvestmentScenario.objects.exists()
    message.refresh_from_db()
    assert message.widget_json["payload"]["allocations"][0]["unit_price"] == 100


@pytest.mark.django_db
def test_save_rejects_changed_priority_explanation(client, scenario_message):
    conversation, message, payload = scenario_message
    tampered = {**deepcopy(payload), "saved": True}
    tampered["allocations"][0]["match_factors"] = ["liquidity"]
    url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"

    response = client.patch(url, {"payload": tampered}, format="json")

    assert response.status_code == 422
    assert "immutable quote data" in str(response.data)
    assert not SavedInvestmentScenario.objects.exists()


@pytest.mark.django_db
def test_saved_scenario_can_be_renamed_archived_and_deleted(client, scenario_message):
    conversation, message, payload = scenario_message
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")
    scenario = SavedInvestmentScenario.objects.get()

    updated = client.patch(
        f"/investment-scenarios/{scenario.id}/",
        {"title": "My first gold plan", "status": "archived"},
        format="json",
    )
    assert updated.status_code == 200
    assert updated.data["title"] == "My first gold plan"
    assert updated.data["status"] == "archived"
    assert client.get("/investment-scenarios/").data["count"] == 0
    assert client.get("/investment-scenarios/?status=archived").data["count"] == 1

    deleted = client.delete(f"/investment-scenarios/{scenario.id}/")
    assert deleted.status_code == 204
    assert not SavedInvestmentScenario.objects.exists()


@pytest.mark.django_db
def test_saved_snapshot_survives_source_conversation_deletion(client, scenario_message):
    conversation, message, payload = scenario_message
    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    client.patch(save_url, {"payload": {**payload, "saved": True}}, format="json")

    assert client.delete(f"/chat/conversations/{conversation.id}/").status_code == 204

    scenario = SavedInvestmentScenario.objects.get()
    assert scenario.source_message_id is None
    listed = client.get("/investment-scenarios/")
    assert listed.data["results"][0]["source_conversation_id"] is None


@pytest.mark.django_db
def test_stale_saved_quote_is_labelled_for_refresh(client, scenario_message):
    conversation, message, payload = scenario_message
    old_payload = deepcopy(payload)
    old_time = (timezone.now() - timedelta(days=2)).isoformat()
    for allocation in old_payload["allocations"]:
        allocation["observed_at"] = old_time
    message.widget_json["payload"] = old_payload
    message.save(update_fields=["widget_json"])

    save_url = f"/chat/conversations/{conversation.id}/messages/{message.id}/widget/"
    response = client.patch(save_url, {"payload": {**old_payload, "saved": True}}, format="json")
    assert response.status_code == 200

    listed = client.get("/investment-scenarios/")
    assert listed.data["results"][0]["quote_status"] == "needs_refresh"
