"""
The single multiplexed SSE connection (Data_Governance_Specs.md-adjacent
async-infra phase) — one GET /events/stream per user, carrying both
statement/OCR events (core/tasks/statements.py) and chat events
(core/tasks/conversations.py), discriminated by SSE event type. Gated by a
short-lived, single-use ticket (services/sse_tickets.py) rather than the
normal JWT header, since a native EventSource can't set one.
"""

from django.conf import settings
from django.http import StreamingHttpResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from core.authentication import SSETicketAuthentication
from services import event_bus, sse_tickets


class SSETicketMintView(APIView):
    """
    POST /events/ticket — normal JWT-authenticated (default
    UserJWTAuthentication). Mints a short-TTL, single-use ticket for the
    caller to open GET /events/stream with.
    """

    @extend_schema(responses={200: OpenApiResponse(description="{ticket, expires_in}")})
    def post(self, request):
        ticket = sse_tickets.mint_ticket(request.user)
        return Response({"ticket": ticket, "expires_in": settings.SSE_TICKET_TTL_SECONDS})


class EventStreamView(APIView):
    """
    GET /events/stream?ticket=... — the one multiplexed SSE connection per
    user. A native EventSource auto-reconnects by re-requesting this exact
    URL, which will 401 once the single-use ticket is consumed — the client
    is expected to mint a fresh ticket and open a new EventSource on
    error/close rather than relying on native auto-reconnect.
    """

    authentication_classes = [SSETicketAuthentication]

    @extend_schema(
        responses={
            200: OpenApiResponse(
                description=(
                    "text/event-stream — a persistent connection relaying "
                    "statement_status, chat_token, and chat_message events "
                    "(named SSE `event:` types) as they occur."
                )
            )
        }
    )
    def get(self, request):
        response = StreamingHttpResponse(
            event_bus.stream_user_events(request.user.id),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        # nginx-specific, belt-and-suspenders alongside deploy/nginx.conf's
        # proxy_buffering off for this route.
        response["X-Accel-Buffering"] = "no"
        return response
