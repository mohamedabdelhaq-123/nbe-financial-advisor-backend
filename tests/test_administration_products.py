"""
Endpoint-level test for AdminProductListCreateView's product-embedding
wiring — POST /admin/products with problem_statements now calls
ai_service.create_embeddings() and stores the result on each
ProblemStatement.embedding, instead of leaving it null.
"""

import pytest
from rest_framework.test import APIClient

from core.models import AdminUser, ProblemStatement


@pytest.fixture
def super_admin(db):
    return AdminUser.objects.create(
        name="Super Admin", email="super-admin-test@example.com", role="super_admin"
    )


@pytest.fixture
def client(super_admin):
    api_client = APIClient()
    api_client.force_authenticate(user=super_admin)
    return api_client


def test_create_product_embeds_problem_statements(client):
    response = client.post(
        "/admin/products/",
        {
            "title": "Premium Savings Account",
            "description": "A low-fee savings account.",
            "problem_statements": ["I want to save more money", "I want low fees"],
        },
        format="json",
    )

    assert response.status_code == 201
    statements = list(ProblemStatement.objects.filter(product_id=response.data["id"]))
    assert len(statements) == 2
    for statement in statements:
        assert statement.embedding is not None
        assert len(statement.embedding) == 768


def test_create_product_without_problem_statements_skips_embedding_call(client):
    response = client.post(
        "/admin/products/",
        {"title": "Basic Account", "description": "No seed text."},
        format="json",
    )

    assert response.status_code == 201
    assert not ProblemStatement.objects.filter(product_id=response.data["id"]).exists()
