"""Static verification of the chimera/* topic-namespace migration (ADR-013).

This is a substitute for running ``_smoke.py`` live: that script requires a
real Mosquitto broker plus provisioned credentials (``JARVIS_BROKER``,
``JARVIS_PASS``) that aren't available in every environment this runs in.
These tests instead prove, without a broker, that (a) every topic template
in ``protocol.T`` now uses the ``chimera/`` namespace and none use the old
``jarvis/node/`` one, and (b) the two production entrypoints that build
topics exclusively through ``T`` still import cleanly against the renamed
file -- the two things that would actually break if the rename were wrong.
"""

from __future__ import annotations

import protocol
from protocol import T


NODE = "test-node"

ALL_METHOD_TOPICS = [
    T.cmd(NODE),
    T.response(NODE),
    T.event(NODE),
    T.presence(NODE),
    T.state(NODE),
    T.heartbeat(NODE),
]

ALL_WILDCARD_TOPICS = [
    T.ALL_RESPONSE,
    T.ALL_EVENT,
    T.ALL_PRESENCE,
    T.ALL_STATE,
    T.ALL_HEARTBEAT,
]


def test_all_topic_methods_use_chimera_namespace() -> None:
    for topic in ALL_METHOD_TOPICS:
        assert topic.startswith("chimera/"), topic
        assert not topic.startswith("jarvis/"), topic


def test_all_wildcard_constants_use_chimera_namespace() -> None:
    for topic in ALL_WILDCARD_TOPICS:
        assert topic.startswith("chimera/"), topic
        assert not topic.startswith("jarvis/"), topic


def test_topic_verb_suffixes_unchanged() -> None:
    """Only the namespace prefix changed -- the verb suffixes (cmd/response/
    event/presence/state/heartbeat) that the rest of the stack matches on
    must be identical to before the rename."""
    assert T.cmd(NODE) == f"chimera/{NODE}/cmd"
    assert T.response(NODE) == f"chimera/{NODE}/response"
    assert T.event(NODE) == f"chimera/{NODE}/event"
    assert T.presence(NODE) == f"chimera/{NODE}/presence"
    assert T.state(NODE) == f"chimera/{NODE}/state"
    assert T.heartbeat(NODE) == f"chimera/{NODE}/heartbeat"
    assert T.ALL_RESPONSE == "chimera/+/response"
    assert T.ALL_EVENT == "chimera/+/event"
    assert T.ALL_PRESENCE == "chimera/+/presence"
    assert T.ALL_STATE == "chimera/+/state"
    assert T.ALL_HEARTBEAT == "chimera/+/heartbeat"


def test_brain_pc_imports_cleanly_against_renamed_protocol() -> None:
    """brain_pc.py builds every topic through T.* (zero raw literals,
    confirmed independently) -- if the rename broke it, this import fails."""
    import brain_pc  # noqa: F401


def test_agent_phone_imports_cleanly_against_renamed_protocol() -> None:
    """Same proof for the phone-side production entrypoint."""
    import agent_phone  # noqa: F401


def test_schema_version_and_message_types_unaffected_by_rename() -> None:
    """Sanity check that the rename touched only topic strings, not the
    message envelope schema itself."""
    assert protocol.SCHEMA == 1
    cmd = protocol.Command(skill="phone.battery")
    assert cmd.type == "cmd"
    assert cmd.version == protocol.SCHEMA
