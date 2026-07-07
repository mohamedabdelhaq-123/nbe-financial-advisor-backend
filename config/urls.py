from django.contrib import admin
from django.urls import path

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", views.health),   # is the app up?
    path("db/", views.db_check),      # can it reach the database?
    path("ping/", views.ping),        # POST -> write one row
]
