"""
Unit tests for services/event_bus.py, backed by fakeredis rather than a real
Redis instance — fast, no network, runs in every CI push. Drives
stream_user_events() directly as a generator (not through the HTTP layer)
so a publish can be interleaved between two next() calls within one
synchronous test, matching how core/views/events.py's EventStreamView uses
it as a StreamingHttpResponse body.
"""

import json

from services import event_bus


def test_stream_user_events_yields_connected_comment_first(fake_redis):
    gen = event_bus.stream_user_events("user-1")
    assert next(gen) == ": connected\n\n"
    gen.close()


def test_stream_user_events_relays_a_published_event(fake_redis):
    gen = event_bus.stream_user_events("user-1")
    next(gen)  # consume ": connected"

    event_bus.publish_user_event("user-1", "test_event", {"hello": "world"})

    frame = next(gen)
    assert frame == 'event: test_event\ndata: {"hello": "world"}\n\n'
    gen.close()


def test_stream_user_events_only_relays_to_the_matching_user_channel(fake_redis):
    gen = event_bus.stream_user_events("user-1")
    next(gen)  # consume ": connected"

    event_bus.publish_user_event("user-2", "test_event", {"should": "not-arrive"})
    event_bus.publish_user_event("user-1", "test_event", {"should": "arrive"})

    frame = next(gen)
    assert json.loads(frame.split("data: ", 1)[1]) == {"should": "arrive"}
    gen.close()


def test_stream_user_events_unsubscribes_on_close(fake_redis):
    gen = event_bus.stream_user_events("user-1")
    next(gen)  # consume ": connected" — this is when pubsub.subscribe() ran

    channel = event_bus._channel("user-1")
    assert fake_redis.pubsub_numsub(channel)[0][1] == 1

    gen.close()  # triggers GeneratorExit -> the generator's finally block

    # Without the finally block's unsubscribe()/close(), this would still be 1
    # — every dropped SSE connection would leak a live pubsub subscription.
    assert fake_redis.pubsub_numsub(channel)[0][1] == 0
