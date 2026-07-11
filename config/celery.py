"""
Celery application for the project. `-A config` (see docker-compose.yml's
celery-worker command) imports this package, which imports this module via
config/__init__.py, so worker startup bootstraps Django settings the same
way manage.py/wsgi.py do — tasks can use the ORM directly.
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("nbe_financial_advisor")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
