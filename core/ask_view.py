import requests as http_client
from django.conf import settings
from django.http import JsonResponse

AI_SERVICE_URL = settings.AI_SERVICE_URL
AI_SERVICE_TOKEN = settings.AI_SERVICE_TOKEN


def ask(request):
    """
    GET /ask/?q=<message>

    Forwards the query to the AI service at AI_SERVICE_URL/chat using a
    Bearer token from AI_SERVICE_TOKEN, then returns the model's reply.
    Returns 502 if the AI service call fails.
    """
    q = request.GET.get("q", "").strip()
    if not q:
        return JsonResponse({"error": "missing required query param: q"}, status=400)

    try:
        resp = http_client.post(
            f"{AI_SERVICE_URL}/chat",
            json={"message": q},
            headers={"Authorization": f"Bearer {AI_SERVICE_TOKEN}"},
            timeout=30,
        )
        resp.raise_for_status()
        return JsonResponse(resp.json())
    except http_client.exceptions.RequestException as exc:
        return JsonResponse({"error": f"AI service call failed: {exc}"}, status=502)
