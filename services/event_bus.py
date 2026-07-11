"""
Redis pub/sub backed event bus for the single multiplexed SSE connection
(core/views/events.py's EventStreamView) — one channel per user, carrying
both statement/OCR events (core/tasks/statements.py) and chat events
(core/tasks/conversations.py). Redis pub/sub has no replay/history: an event
published while a client is disconnected is simply lost. This is accepted —
the persisted StatementFile/Message rows are always the source of truth, and
SSE is a low-latency nudge to refetch, not the sole way state becomes
visible (see the plan's "missed events" note).
"""

import json

import redis
from django.conf import settings

_redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)


def _channel(user_id) -> str:
    return f"sse:user:{user_id}"


def publish_user_event(user_id, event_type: str, data: dict) -> None:
    _redis_client.publish(_channel(user_id), json.dumps({"event": event_type, "data": data}))


def stream_user_events(user_id, heartbeat_seconds: int = 15):
    """
    Long-lived generator, one per open SSE connection — used directly as a
    StreamingHttpResponse body (core/views/events.py). Emits real SSE
    `event:` lines (not a JSON-embedded `event` key) so the client can use
    EventSource.addEventListener per event type.

    pubsub.get_message(timeout=...) blocks the calling thread on the Redis
    socket rather than busy-polling, and returns None on timeout — used here
    to emit a heartbeat comment so intermediate proxies/load balancers don't
    idle the connection out, and so this generator gets a chance to notice a
    closed connection promptly instead of blocking forever.
    """
    pubsub = _redis_client.pubsub()
    pubsub.subscribe(_channel(user_id))
    try:
        yield ": connected\n\n"
        while True:
            message = pubsub.get_message(timeout=heartbeat_seconds)
            if message is None:
                yield ": heartbeat\n\n"
                continue
            if message["type"] != "message":
                continue
            envelope = json.loads(message["data"])
            yield f"event: {envelope['event']}\ndata: {json.dumps(envelope['data'])}\n\n"
    finally:
        # Runs on normal completion AND on GeneratorExit — Django closes this
        # generator when the client disconnects mid-stream, and this is the
        # only place the Redis subscription gets cleaned up. Without it,
        # every dropped SSE connection leaks a live pubsub subscription.
        pubsub.unsubscribe(_channel(user_id))
        pubsub.close()
