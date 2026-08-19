"""
Endpoint-level tests for /admin/onboarding-templates — the admin panel's
write path onto pfm-reference-data/onboarding-templates/*.json
(services/file_storage.py), which GET /budget/starter-templates reads
directly. Backed by moto's mocked S3 (moto_storage fixture,
tests/conftest.py), not a DB table.
"""

import pytest
from rest_framework.test import APIClient

from core.models import AdminUser

BALANCED = {
    "template_key": "balanced",
    "name": "Balanced",
    "description": "An even split.",
    "allocations": [
        {"category": "housing", "allocated_percentage": 50},
        {"category": "savings", "allocated_percentage": 50},
    ],
}


@pytest.fixture
def super_admin(db):
    return AdminUser.objects.create(
        name="Super Admin", email="super-admin-tpl-test@example.com", role="super_admin"
    )


@pytest.fixture
def reviewer(db):
    return AdminUser.objects.create(
        name="Reviewer", email="reviewer-tpl-test@example.com", role="reviewer"
    )


@pytest.fixture
def super_admin_client(super_admin):
    api_client = APIClient()
    api_client.force_authenticate(user=super_admin)
    return api_client


@pytest.fixture
def reviewer_client(reviewer):
    api_client = APIClient()
    api_client.force_authenticate(user=reviewer)
    return api_client


def test_create_template_writes_to_reference_data(super_admin_client, moto_storage):
    response = super_admin_client.post("/admin/onboarding-templates/", BALANCED, format="json")

    assert response.status_code == 201
    assert response.data["template_key"] == "balanced"

    from services import file_storage

    stored = file_storage.get_onboarding_template("balanced")
    assert stored["name"] == "Balanced"
    assert stored["allocations"] == [
        {"category": "housing", "allocated_percentage": 50.0},
        {"category": "savings", "allocated_percentage": 50.0},
    ]


def test_create_template_reviewer_forbidden(reviewer_client, moto_storage):
    response = reviewer_client.post("/admin/onboarding-templates/", BALANCED, format="json")
    assert response.status_code == 403


def test_create_template_duplicate_key_conflicts(super_admin_client, moto_storage):
    super_admin_client.post("/admin/onboarding-templates/", BALANCED, format="json")
    response = super_admin_client.post("/admin/onboarding-templates/", BALANCED, format="json")
    assert response.status_code == 409


def test_create_template_allocations_must_sum_to_100(super_admin_client, moto_storage):
    body = {**BALANCED, "allocations": [{"category": "housing", "allocated_percentage": 50}]}
    response = super_admin_client.post("/admin/onboarding-templates/", body, format="json")
    assert response.status_code == 422


def test_list_templates_any_admin_role(reviewer_client, moto_storage):
    from services import file_storage

    file_storage.put_onboarding_template(BALANCED)

    response = reviewer_client.get("/admin/onboarding-templates/")
    assert response.status_code == 200
    assert [t["template_key"] for t in response.data] == ["balanced"]


def test_patch_template_replaces_allocations(super_admin_client, moto_storage):
    from services import file_storage

    file_storage.put_onboarding_template(BALANCED)

    response = super_admin_client.patch(
        "/admin/onboarding-templates/balanced/",
        {
            "allocations": [
                {"category": "housing", "allocated_percentage": 30},
                {"category": "savings", "allocated_percentage": 70},
            ]
        },
        format="json",
    )

    assert response.status_code == 200
    assert response.data["allocations"] == [
        {"category": "housing", "allocated_percentage": 30.0},
        {"category": "savings", "allocated_percentage": 70.0},
    ]
    # name/description untouched by a partial update.
    assert response.data["name"] == "Balanced"


def test_patch_template_missing_key_404s(super_admin_client, moto_storage):
    response = super_admin_client.patch(
        "/admin/onboarding-templates/does-not-exist/", {"name": "X"}, format="json"
    )
    assert response.status_code == 404


def test_patch_template_reviewer_forbidden(reviewer_client, moto_storage):
    from services import file_storage

    file_storage.put_onboarding_template(BALANCED)
    response = reviewer_client.patch(
        "/admin/onboarding-templates/balanced/", {"name": "X"}, format="json"
    )
    assert response.status_code == 403


def test_delete_template(super_admin_client, moto_storage):
    from services import file_storage

    file_storage.put_onboarding_template(BALANCED)

    response = super_admin_client.delete("/admin/onboarding-templates/balanced/")
    assert response.status_code == 204
    assert file_storage.get_onboarding_template("balanced") is None


def test_delete_template_missing_key_404s(super_admin_client, moto_storage):
    response = super_admin_client.delete("/admin/onboarding-templates/does-not-exist/")
    assert response.status_code == 404


def test_starter_templates_endpoint_reflects_admin_created_template(
    super_admin_client, moto_storage
):
    """GET /budget/starter-templates (the public onboarding endpoint) reads
    the exact same reference data the admin endpoint writes — no separate
    publish step."""
    super_admin_client.post("/admin/onboarding-templates/", BALANCED, format="json")

    public_client = APIClient()
    response = public_client.get("/budget/starter-templates/")

    assert response.status_code == 200
    assert [t["template_key"] for t in response.data] == ["balanced"]
