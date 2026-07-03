"""MqttTransport unit tests that don't require a live broker.

No broker infrastructure exists for this project yet (Priority #3 Milestone 1
scope note) -- these tests exercise ``request()``'s request_id handling in
isolation by faking ``publish()`` rather than connecting a real
``aiomqtt.Client``.
"""

from __future__ import annotations

import asyncio

from jarvis_node_sdk.transport import MqttTransport


class _FakeMqttConfig:
    host = "localhost"
    port = 1883
    client_id = "test-client"
    keepalive = 30
    clean_session = False
    qos = 1
    username = None
    password = None


def _make_faked_transport() -> tuple[MqttTransport, dict]:
    """A transport whose publish() is faked to immediately resolve the
    matching pending future, as if a responder echoed the request straight
    back -- avoids needing a real broker connection."""
    transport = MqttTransport(_FakeMqttConfig())
    captured: dict = {}

    async def fake_publish(topic, payload, *, qos=None, retain=False):
        captured["topic"] = topic
        captured["payload"] = payload
        pending = transport._pending[payload["request_id"]]
        if not pending.future.done():
            pending.future.set_result(payload)

    transport.publish = fake_publish  # type: ignore[method-assign]
    return transport, captured


def test_request_preserves_caller_supplied_request_id() -> None:
    """The exact behavior the signing-order fix (executor.py's
    _execute_phone()) depends on: if the caller already put request_id in
    the payload -- e.g. so it can be included under an HMAC signature
    computed before request() runs -- request() must not replace it with a
    freshly generated one."""
    transport, captured = _make_faked_transport()
    caller_request_id = "caller-supplied-id"

    result = asyncio.run(
        transport.request(
            topic="chimera/testnode/cmd",
            payload={"action": "ping", "request_id": caller_request_id},
            reply_topic="chimera/testnode/response",
            timeout=1.0,
        )
    )

    assert captured["payload"]["request_id"] == caller_request_id
    assert result["request_id"] == caller_request_id


def test_request_generates_id_when_absent() -> None:
    transport, captured = _make_faked_transport()

    result = asyncio.run(
        transport.request(
            topic="chimera/testnode/cmd",
            payload={"action": "ping"},
            reply_topic="chimera/testnode/response",
            timeout=1.0,
        )
    )

    assert "request_id" in captured["payload"]
    assert captured["payload"]["request_id"] == result["request_id"]
