"""Coverage for seed_db's --if-empty flag — added so an automatic startup
step can seed a fresh volume once without flushing and reseeding a running
deployment's data on every subsequent restart (see deploy/docker-compose.prod.yml's
AUTO_SEED_DB-gated backend command).
"""

import pytest
from django.core.management import call_command
from django.test import override_settings

from core.models import Product, User


@pytest.mark.django_db
def test_if_empty_skips_when_a_product_already_exists():
    Product.objects.create(
        title="Existing Product",
        external_link="https://example.com/real-product",
    )

    with override_settings(DEBUG=False):
        # No --force: if this actually tried to seed, it would raise
        # CommandError for the DEBUG guard instead of returning quietly.
        call_command("seed_db", "--if-empty")

    assert not User.objects.filter(email__startswith="seed_user_").exists()


@pytest.mark.django_db
def test_if_empty_seeds_when_no_products_exist():
    assert not Product.objects.exists()

    # --force: pytest-django forces settings.DEBUG = False for every test
    # run regardless of the environment, so the DEBUG guard would otherwise
    # trip here too — that guard is what test_if_empty_still_requires_
    # debug_or_force_when_it_actually_seeds below actually exercises.
    call_command("seed_db", "--if-empty", "--force", "--users", "1")

    assert Product.objects.exists()
    assert User.objects.filter(email="seed_user_0@example.com").exists()


@pytest.mark.django_db
def test_if_empty_still_requires_debug_or_force_when_it_actually_seeds():
    assert not Product.objects.exists()

    with override_settings(DEBUG=False):
        with pytest.raises(Exception, match="Refusing to seed synthetic data"):
            call_command("seed_db", "--if-empty")
