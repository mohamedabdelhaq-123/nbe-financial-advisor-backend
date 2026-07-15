"""
Endpoint-level tests for GET /recommendations — in particular that a real
ai_service.match_recommendations() failure (AIServiceError) becomes a clean
502 in the shared {"error": {...}} envelope (core/exceptions.py), not an
unhandled 500. This call is synchronous in the request/response cycle
(unlike chat/statement-ingestion, which buffer failures behind a Celery task
instead), so it's the one place a real ai-service outage has to become the
HTTP response itself.
"""

import pytest
from rest_framework.test import APIClient

from core.models import Product, User
from services import ai_service


@pytest.fixture
def user(db):
    return User.objects.create_user(
        email="recommendations-test@example.com", password="x", name="Recommendations Test"
    )


@pytest.fixture
def client(user):
    api_client = APIClient()
    api_client.force_authenticate(user=user)
    return api_client


def test_get_recommendations_returns_matched_products(client):
    Product.objects.create(
        title="Premium Savings Account", description="low-fee savings", is_active=True
    )

    response = client.get("/recommendations/?q=savings")

    assert response.status_code == 200
    assert response.data[0]["title"] == "Premium Savings Account"


def test_get_recommendations_returns_502_when_ai_service_fails(client, monkeypatch):
    def _raise(*args, **kwargs):
        raise ai_service.AIServiceError("AI service call to /internal/recommendations/match failed")

    monkeypatch.setattr(ai_service, "match_recommendations", _raise)

    response = client.get("/recommendations/?q=savings")

    assert response.status_code == 502
    assert response.data["error"]["code"] == "ai_service_unavailable"
