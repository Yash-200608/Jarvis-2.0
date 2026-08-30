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


def test_sms_send_requires_message_and_recipient() -> None:
    runner, calls = _fake_runner()
    skills = _skills(runner)

    assert asyncio.run(skills["phone.sms.send"]({"recipient": "+911234"}))["ok"] is False
    assert asyncio.run(skills["phone.sms.send"]({"message": "hi"}))["ok"] is False
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


def test_open_app_fails_on_security_exception() -> None:
    runner, calls = _fake_runner(
        {
            "am": {
                "exit_code": 1,
                "stdout": "Starting: Intent",
                "stderr": (
                    "SecurityException: Permission Denial: startActivityAsUser "
                    "asks to run as user -2"
                ),
            },
            "cmd": {"exit_code": 1, "stdout": "", "stderr": "no activity"},
        }
    )
    skills = _skills(runner, termux=False)

    result = asyncio.run(skills["phone.app.open"]({"app": "settings"}))

    assert result["ok"] is False
    assert calls[0][0] == "am"


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


def _whatsapp_adb_runner(responses: dict[str, dict] | None = None):
    """Runner that simulates adb-connected device for WhatsApp automation tests."""
    calls: list[tuple] = []
    responses = responses or {}
    compose_text = {"value": ""}

    def _ui_xml() -> str:
        text_attr = f' text="{compose_text["value"]}"' if compose_text["value"] else ""
        return (
            '<?xml version="1.0"?><hierarchy>'
            f'<node class="android.widget.EditText" resource-id="com.whatsapp:id/entry"'
            f' content-desc="Type a message" bounds="[100,2100][980,2300]"{text_attr}/>'
            '<node clickable="true" resource-id="com.whatsapp:id/send" '
            'content-desc="Send" bounds="[950,2200][1050,2300]"/>'
            "</hierarchy>"
        )

    default = {"exit_code": 0, "stdout": "", "stderr": ""}

    async def runner(*argv: str, timeout: float = 15.0) -> dict:
        calls.append(argv)
        key = argv[0]
        if key == "adb" and len(argv) > 1 and argv[1] == "devices":
            return {
                "exit_code": 0,
                "stdout": "List of devices attached\nlocalhost:5555\tdevice\n",
                "stderr": "",
            }
        if key == "adb" and len(argv) > 2:
            sub = argv[2]
            if sub == "wm":
                return dict(responses.get("wm", default))
            if sub == "uiautomator":
                return dict(responses.get("uiautomator", default))
            if sub == "cat":
                return {"exit_code": 0, "stdout": _ui_xml(), "stderr": ""}
            if sub == "input" and len(argv) > 3 and argv[3] == "text":
                compose_text["value"] = argv[4].replace("%s", " ")
                return dict(default)
            if sub == "input" and len(argv) > 3 and argv[3] in ("tap", "keyevent"):
                compose_text["value"] = ""
                return dict(default)
            if sub == "rm":
                return dict(default)
        return dict(responses.get(key, default))

    return runner, calls


def test_whatsapp_auto_send_requires_adb() -> None:
    runner, calls = _fake_runner()
    skills = build_phone_skills(runner=runner, have=lambda b: b == "termux-sms-send")

    result = asyncio.run(
        skills["phone.whatsapp.send"]({"phone": "+919876543210", "message": "hi"})
    )

    assert result["ok"] is False
    assert "adb" in result["error"].lower()
    assert any(c[0] == "adb" for c in calls)


def test_whatsapp_send_uses_native_intent_and_auto_taps_send() -> None:
    runner, calls = _whatsapp_adb_runner(
        {"wm": {"exit_code": 0, "stdout": "Physical size: 1080x2400\n", "stderr": ""}}
    )
    skills = build_phone_skills(runner=runner, have=lambda b: b == "termux-sms-send")

    result = asyncio.run(
        skills["phone.whatsapp.send"]({"phone": "+91 76785 66378", "message": "hi there"})
    )

    assert result["ok"] is True
    assert result["sent"] is True
    assert result["phone"] == "917678566378"
    am_calls = [c for c in calls if c and c[0] == "am"]
    assert am_calls
    assert any("whatsapp://send" in str(a) for a in am_calls[0])
    assert any(c[0] == "adb" for c in calls)


def test_whatsapp_send_sanitizes_phone_and_quotes_message() -> None:
    runner, calls = _fake_runner()
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b == "termux-sms-send",
    )

    result = asyncio.run(
        skills["phone.whatsapp.send"](
            {"phone": "+91 98765 43210", "message": "hi there", "auto_send": False}
        )
    )

    assert result["ok"] is True
    assert result["sent"] is False
    am_calls = [c for c in calls if c and c[0] == "am"]
    assert any(
        "919876543210" in str(a) for a in am_calls[0]
    )
    assert any(
        "hi there" in str(a) or "hi%20there" in str(a) for a in am_calls[0]
    )


def test_whatsapp_send_falls_back_to_am_without_termux_open() -> None:
    runner, calls = _fake_runner()
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b == "termux-sms-send",
    )

    result = asyncio.run(
        skills["phone.whatsapp.send"](
            {"phone": "1234567890", "message": "hi", "auto_send": False}
        )
    )

    assert result["ok"] is True
    assert calls[0][0] == "am"


def test_sms_send_resolves_contact_name() -> None:
    contacts = json.dumps(
        [
            {"name": "Mom", "number": "+919876543210"},
            {"name": "John Smith", "number": "+911111111111"},
        ]
    )
    runner, calls = _fake_runner(
        {"termux-contact-list": {"exit_code": 0, "stdout": contacts, "stderr": ""}}
    )
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b in ("termux-sms-send", "termux-contact-list"),
    )

    result = asyncio.run(
        skills["phone.sms.send"]({"name": "Mom", "message": "running late"})
    )

    assert result["ok"] is True
    assert result["resolved_contact"] == {"name": "Mom", "number": "+919876543210"}
    assert calls[0] == ("termux-contact-list",)
    assert calls[1] == ("termux-sms-send", "-n", "+919876543210", "running late")


def test_whatsapp_send_resolves_contact_name() -> None:
    contacts = json.dumps([{"name": "Alice", "number": "+91 98765 43210"}])
    runner, calls = _fake_runner(
        {
            "termux-contact-list": {"exit_code": 0, "stdout": contacts, "stderr": ""},
        }
    )
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b in ("termux-sms-send", "termux-contact-list", "termux-open"),
    )

    result = asyncio.run(
        skills["phone.whatsapp.send"](
            {"recipient": "Alice", "message": "ping", "auto_send": False}
        )
    )

    assert result["ok"] is True
    assert result["resolved_contact"]["name"] == "Alice"
    assert calls[0] == ("termux-contact-list",)
    am_calls = [c for c in calls if c and c[0] == "am"]
    assert any("9876543210" in str(a) for a in am_calls[0])


def test_messaging_picks_best_match_and_lists_alternates() -> None:
    contacts = json.dumps(
        [
            {"name": "John Smith", "number": "+911111"},
            {"name": "John Doe", "number": "+912222"},
        ]
    )
    runner, calls = _fake_runner(
        {"termux-contact-list": {"exit_code": 0, "stdout": contacts, "stderr": ""}}
    )
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b in ("termux-sms-send", "termux-contact-list"),
    )

    result = asyncio.run(
        skills["phone.sms.send"]({"recipient": "John", "message": "hi"})
    )

    assert result["ok"] is True
    assert result["resolved_contact"]["number"] in ("+911111", "+912222")
    assert len(result["resolved_contact"]["alternates"]) == 1
    assert calls[1][0] == "termux-sms-send"


def test_messaging_dedupes_gmail_duplicate_numbers() -> None:
    contacts = json.dumps(
        [
            {"name": "Aman", "number": "+91 76785 66378"},
            {"name": "Aman", "number": "+917678566378"},
            {"name": "Aman", "number": "7678566378"},
        ]
    )
    runner, calls = _fake_runner(
        {"termux-contact-list": {"exit_code": 0, "stdout": contacts, "stderr": ""}}
    )
    skills = build_phone_skills(
        runner=runner,
        have=lambda b: b in ("termux-sms-send", "termux-contact-list", "termux-open"),
    )

    result = asyncio.run(
        skills["phone.whatsapp.send"](
            {"recipient": "Aman", "message": "hi", "auto_send": False}
        )
    )

    assert result["ok"] is True
    assert "7678566378" in result["phone"]
    assert "alternates" not in result.get("resolved_contact", {})
    assert "exit_code" not in result
    assert "stderr" not in result


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
        "phone.sms.send": {"phone": "+911234567890", "message": "x"},
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
