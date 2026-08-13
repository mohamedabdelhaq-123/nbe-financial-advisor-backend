"""
Renderers for responses DRF's content negotiation would otherwise reject.

Only ServerSentEventRenderer lives here today — see its docstring for why the
SSE stream needs one at all.
"""

import json

from rest_framework.renderers import BaseRenderer


class ServerSentEventRenderer(BaseRenderer):
    """
    Declares `text/event-stream` so GET /events/stream can be negotiated at all.

    Without this the endpoint is unreachable from an actual browser. DRF runs
    content negotiation in APIView.initial() — *before* authentication — and
    matches the request's Accept header against the renderer classes. A native
    EventSource always sends `Accept: text/event-stream`, which no default
    renderer declares, so every real connection attempt was rejected with 406
    before the ticket was ever looked at.

    This is easy to miss by hand: curl sends `Accept: */*` unless told
    otherwise, which happily matches JSONRenderer and returns a perfectly
    healthy-looking 200 stream. Only a client that asks for `text/event-stream`
    specifically — i.e. the only kind that actually matters here — sees the
    406. tests/test_events.py pins the header explicitly for that reason.

    render() is never called on the success path: EventStreamView returns a
    StreamingHttpResponse (a plain Django response, not a DRF Response), and
    finalize_response passes those through untouched. It exists for the error
    path — a missing/expired/reused ticket produces a DRF Response that gets
    rendered with whatever negotiation picked, which for an EventSource request
    is this renderer.
    """

    media_type = "text/event-stream"
    format = "event-stream"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None:
            return b""
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode(self.charset)
        # A 401 body is a dict (core/exceptions.py's shared error shape). JSON
        # keeps it readable instead of serializing a Python repr — the client
        # is failing to open a stream either way, so the media type on that
        # response is beside the point.
        return json.dumps(data).encode(self.charset)
