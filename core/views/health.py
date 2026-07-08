from django.db import connection
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from core.models import Ping


def health(request):
    """Liveness check. Does NOT touch the database — proves the app itself is up."""
    return JsonResponse({"status": "ok"})


def db_check(request):
    """Readiness check. Confirms the app can reach Postgres and counts the rows."""
    with connection.cursor() as cur:
        cur.execute("SELECT 1")
        cur.fetchone()
    return JsonResponse({"db": "ok", "ping_count": Ping.objects.count()})


@csrf_exempt
def ping(request):
    """POST here to write one row. Used to test that data survives a restart."""
    if request.method != "POST":
        return JsonResponse({"error": "use POST"}, status=405)
    row = Ping.objects.create()
    return JsonResponse({"created_id": row.id, "ping_count": Ping.objects.count()})
