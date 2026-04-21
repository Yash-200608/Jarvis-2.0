"""HMAC envelope round-trip tests."""

from __future__ import annotations

import time

from core.signing import MAX_SKEW_SECONDS, sign_envelope, verify_envelope


KEY = "f" * 64


def test_round_trip() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    assert verify_envelope(env, KEY) is True


def test_tampered_action_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    env["action"] = "wipe"
    assert verify_envelope(env, KEY) is False


def test_replay_outside_skew_window_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    env["ts"] = int(time.time()) - MAX_SKEW_SECONDS - 5
    # Re-sign with the stale ts so HMAC still matches but ts check fails.
    from core.signing import _canonical
    import hashlib
    import hmac as _hmac

    env.pop("sig")
    env["sig"] = _hmac.new(KEY.encode(), _canonical(env), hashlib.sha256).hexdigest()
    assert verify_envelope(env, KEY) is False


def test_wrong_key_rejected() -> None:
    env = sign_envelope({"action": "ping"}, KEY)
    assert verify_envelope(env, "0" * 64) is False
