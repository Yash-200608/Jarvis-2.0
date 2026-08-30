"""Phone skill tests (Priority #4 Milestone 5) -- exercises the ported
skill implementations against a FAKED shell runner (the Termux boundary),
never real subprocesses. The runner records every argv it is called with
and returns canned results, so each skill's command construction, parsing,
and failure mapping is proven without any Android/Termux dependency.
"""

from __future__ import annotations

import asyncio
import json

from jarvis_node_sdk.phone_skills import build_phone_skills


def _fake_runner(responses: dict[str, dict] | None = None, default: dict | None = None):
    """A runner that records calls and answers by binary name."""
    calls: list[tuple] = []
    responses = responses or {}
    default = default or {"exit_code": 0, "stdout": "", "stderr": ""}

    async def runner(*argv: str, timeout: float = 15.0) -> dict:
        calls.append(argv)
        return dict(responses.get(argv[0], default))

    return runner, calls


def _skills(runner, termux: bool = True):
    return build_phone_skills(runner=runner, have=lambda b: termux)


def test_battery_parses_termux_json() -> None:
    runner, calls = _fake_runner(
        {
            "termux-battery-status": {
                "exit_code": 0,
                "stdout": json.dumps(
                    {"percentage": 73, "status": "CHARGING", "health": "GOOD", "temperature": 30.1}
                ),
                "stderr": "",
            }
        }
    )
    skills = _skills(runner)

    result = asyncio.run(skills["phone.battery"]({}))

    assert result["ok"] is True
    assert result["battery"]["level"] == 73
    assert result["battery"]["charging"] is True
    assert calls[0][0] == "termux-battery-status"


def test_battery_falls_back_to_dumpsys_without_termux() -> None:
    runner, calls = _fake_runner(
        {
            "dumpsys": {
                "exit_code": 0,
                "stdout": "  level: 41\n  AC powered: false\n  USB powered: true\n",
                "stderr": "",
            }
        }
    )
    skills = _skills(runner, termux=False)

    result = asyncio.run(skills["phone.battery"]({}))

    assert result["ok"] is True
    assert result["battery"]["level"] == 41
    assert result["battery"]["charging"] is True
    assert calls[0][0] == "dumpsys"  # never tried termux-battery-status


def test_tts_requires_text() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.tts"]({"text": "  "}))

    assert result == {"ok": False, "error": "missing text"}
    assert calls == []  # rejected before any shell call


def test_tts_happy_path_invokes_termux_tts_speak() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.tts"]({"text": "hello"}))

    assert result["ok"] is True
    assert calls[0] == ("termux-tts-speak", "hello")


def test_sms_send_requires_phone_and_message() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.sms.send"]({"phone": "+911234"}))

    assert result["ok"] is False
    assert calls == []


def test_sms_send_happy_path_argv() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(
        skills["phone.sms.send"]({"phone": "+911234", "message": "running late"})
    )

    assert result["ok"] is True
    assert calls[0] == ("termux-sms-send", "-n", "+911234", "running late")


def test_torch_off_argv() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.torch"]({"on": False}))

    assert result["ok"] is True
    assert result["on"] is False
    assert calls[0] == ("termux-torch", "off")


def test_vibrate_passes_ms() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.vibrate"]({"ms": 250}))

    assert result["ok"] is True
    assert calls[0] == ("termux-vibrate", "-d", "250")


def test_notify_builds_title_and_content() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(
        skills["phone.notify"]({"title": "Reminder", "content": "stand up"})
    )

    assert result["ok"] is True
    assert calls[0] == (
        "termux-notification", "--id", "9000", "--title", "Reminder", "--content", "stand up"
    )


def test_location_parses_json() -> None:
    runner, _ = _fake_runner(
        {
            "termux-location": {
                "exit_code": 0,
                "stdout": json.dumps({"latitude": 1.0, "longitude": 2.0}),
                "stderr": "",
            }
        }
    )
    skills = _skills(runner)

    result = asyncio.run(skills["phone.location"]({}))

    assert result["ok"] is True
    assert result["location"] == {"latitude": 1.0, "longitude": 2.0}


def test_location_bad_json_is_a_truthful_failure() -> None:
    runner, _ = _fake_runner(
        {"termux-location": {"exit_code": 0, "stdout": "not json", "stderr": ""}}
    )
    skills = _skills(runner)

    result = asyncio.run(skills["phone.location"]({}))

    assert result["ok"] is False
    assert "bad json" in result["error"]


def test_open_app_maps_known_names_to_packages() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.app.open"]({"app": "WhatsApp"}))

    assert result["ok"] is True
    assert result["package"] == "com.whatsapp"
    assert calls[0][0] == "am"
    assert "com.whatsapp" in calls[0]


def test_open_app_passes_unknown_name_through_as_package() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.app.open"]({"app": "com.custom.app"}))

    assert result["package"] == "com.custom.app"
    assert calls[0][0] == "am"


def test_open_app_treats_am_starting_intent_as_success() -> None:
    runner, calls = _fake_runner(
        {
            "am": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "Starting: Intent { act=android.intent.action.MAIN pkg=com.android.settings }",
            }
        }
    )
    skills = _skills(runner)

    result = asyncio.run(skills["phone.app.open"]({"app": "settings"}))

    assert result["ok"] is True
    assert calls[0][0] == "am"
    assert len(calls) == 1


def test_open_app_resolves_component_when_pkg_launch_fails() -> None:
    calls: list[tuple] = []

    async def runner(*argv: str, timeout: float = 15.0) -> dict:
        calls.append(argv)
        if argv[0] == "am" and len([c for c in calls if c[0] == "am"]) == 1:
            return {"exit_code": 1, "stdout": "", "stderr": "Error"}
        if argv[0] == "cmd":
            return {
                "exit_code": 0,
                "stdout": "priority=0\ncom.android.settings/.Settings",
                "stderr": "",
            }
        if argv[0] == "am":
            return {"exit_code": 0, "stdout": "Starting: Intent", "stderr": ""}
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    skills = build_phone_skills(runner=runner, have=lambda _: False)

    result = asyncio.run(skills["phone.app.open"]({"app": "settings"}))

    assert result["ok"] is True
    assert calls[0][0] == "am"
    assert calls[1][0] == "cmd"
    assert calls[2] == ("am", "start", "-n", "com.android.settings/.Settings")


def test_whatsapp_send_sanitizes_phone_and_quotes_message() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(
        skills["phone.whatsapp.send"]({"phone": "+91 12-34", "message": "hi there"})
    )

    assert result["ok"] is True
    url = calls[0][-1]
    assert "phone=+911234" in url
    assert "hi%20there" in url
    assert "note" in result


def test_ring_turns_torch_on_then_off() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    result = asyncio.run(skills["phone.ring"]({"seconds": 1}))

    assert result == {"ok": True, "seconds": 1}
    binaries = [c[0] for c in calls]
    assert ("termux-torch", "on") in [tuple(c[:2]) for c in calls]
    assert ("termux-torch", "off") in [tuple(c[:2]) for c in calls]
    assert "termux-vibrate" in binaries
    assert "termux-tts-speak" in binaries


def test_termux_gated_skills_fail_closed_without_termux() -> None:
    """Every termux-api-dependent skill reports a truthful, non-raising
    failure when termux is absent (battery has a dumpsys fallback and
    open_app/whatsapp use am/monkey, so they are exempt)."""
    runner, calls = _fake_runner()
    skills = _skills(runner, termux=False)

    gated = {
        "phone.tts": {"text": "x"},
        "phone.sms.send": {"phone": "1", "message": "x"},
        "phone.torch": {},
        "phone.vibrate": {},
        "phone.notify": {},
        "phone.ring": {},
        "phone.location": {},
    }
    for name, params in gated.items():
        result = asyncio.run(skills[name](params))
        assert result == {"ok": False, "error": "termux-api not installed"}, name
    assert calls == []  # none of them touched the shell


def test_all_ten_skills_are_present() -> None:
    runner, _ = _fake_runner()
    skills = _skills(runner)

    assert sorted(skills) == [
        "phone.app.open",
        "phone.battery",
        "phone.location",
        "phone.notify",
        "phone.ring",
        "phone.sms.send",
        "phone.torch",
        "phone.tts",
        "phone.vibrate",
        "phone.whatsapp.send",
    ]
