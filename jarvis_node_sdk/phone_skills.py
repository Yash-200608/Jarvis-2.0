"""Phone skills -- the 10 working Termux skill implementations, ported from
hugginggpt/server/jarvis_prod/agent_phone.py onto the node SDK (Priority #4
Milestone 5).

Ported faithfully -- same skill names, same params, same result shapes --
with one deliberate structural change: agent_phone.py hardcodes its `sh()`
subprocess helper and detects Termux at import time; here both are
injected via build_phone_skills(runner=..., have=...) so tests can fake
the Termux boundary and capability detection happens at build time, not
import time. Production callers use the defaults (real subprocesses, real
shutil.which).

Skills never raise: the runner returns {"exit_code": -1, ...} on every
failure mode (timeout, missing binary, anything), mirroring
agent_phone.py's sh() contract, and each skill maps that to
{"ok": False, ...} in its result.

NOT a general skill framework: exactly the 10 proven phone.* skills, a
dict from name to coroutine. Registration into a SkillRegistry (M3) and
dispatch (command_handler.dispatch_command) are the caller's wiring.
"""

from __future__ import annotations

import asyncio
import json
import re
import shlex
import shutil
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Awaitable, Callable

SkillFn = Callable[[dict], Awaitable[dict]]
Runner = Callable[..., Awaitable[dict]]

# Activity Manager calls from proot Ubuntu print "Starting: Intent" but fail with
# SecurityException — route them through native Termux bash instead.
TERMUX_BASH = "/data/data/com.termux/files/usr/bin/bash"
TERMUX_PREFIX_BIN = "/data/data/com.termux/files/usr/bin"
ANDROID_SYSTEM_BIN = "/system/bin:/system/xbin"
_NATIVE_SHELL_BINARIES = frozenset({
    "am", "monkey", "cmd", "termux-open", "input", "wm", "uiautomator", "cat", "rm", "adb",
})

_WHATSAPP_PKG = "com.whatsapp"
_UI_DUMP_PATH = "/sdcard/jarvis_ui.xml"
_SEND_NEGATIVE = (
    "feedback", "report", "reset", "sender", "resend",
    "location", "photo", "file", "sticker", "voice note", "audio",
    "gif", "attachment", "emoji", "camera",
)
_SEND_POSITIVE_IDS = (
    "send_btn", "send_button", "sendbtn", "btn_send",
    "entry_send", "conversation_send", "composer_send",
    "action_send", "iv_send", "ib_send", "com.whatsapp:id/send",
)
_COMPOSE_FIELD_IDS = (
    "com.whatsapp:id/entry", "com.whatsapp:id/input", "com.whatsapp:id/entry_text",
    "edittext", "message",
)
_CONTINUE_CHAT_MARKERS = ("continue to chat", "continue toChat", "message ", "send message")
_COMPOSE_PLACEHOLDERS = frozenset({"message", "type a message"})


def _launch_ok(result: dict) -> bool:
    combined = (result.get("stdout") or "") + (result.get("stderr") or "")
    if any(
        marker in combined
        for marker in (
            "SecurityException",
            "Permission Denial",
            "InvocationTargetException",
        )
    ):
        return False
    if result.get("exit_code") == 0:
        return True
    return "Starting: Intent" in combined or "Status: ok" in combined


def _inject_am_user(argv: list[str]) -> list[str]:
    if len(argv) >= 2 and argv[0] == "am" and argv[1] == "start" and "--user" not in argv:
        return ["am", "start", "--user", "0", *argv[2:]]
    return argv


async def sh(*argv: str, timeout: float = 15.0) -> dict[str, Any]:
    """Run `argv` non-blocking. Never raises -- always returns a dict with
    exit_code/stdout/stderr (exit_code -1 on timeout/missing binary/any
    error). Same contract as agent_phone.py's sh()."""
    try:
        async with asyncio.timeout(timeout):
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await proc.communicate()
            return {
                "exit_code": proc.returncode,
                "stdout": out.decode("utf-8", errors="replace"),
                "stderr": err.decode("utf-8", errors="replace"),
            }
    except TimeoutError:
        return {"exit_code": -1, "stdout": "", "stderr": f"timeout {timeout}s"}
    except FileNotFoundError as e:
        return {"exit_code": -1, "stdout": "", "stderr": f"not found: {e}"}
    except Exception as e:  # noqa: BLE001 -- the never-raise contract is the point
        return {"exit_code": -1, "stdout": "", "stderr": f"{type(e).__name__}: {e}"}


async def termux_android_runner(*argv: str, timeout: float = 15.0) -> dict[str, Any]:
    """Run Android shell tools in native Termux (proot cannot start activities)."""
    args = list(argv)
    if args and args[0] in _NATIVE_SHELL_BINARIES:
        if args[0] == "am":
            args = _inject_am_user(args)
        cmd = (
            f"export PATH={TERMUX_PREFIX_BIN}:{ANDROID_SYSTEM_BIN}:$PATH; exec "
            + " ".join(shlex.quote(a) for a in args)
        )
        return await sh(TERMUX_BASH, "-c", cmd, timeout=timeout)
    return await sh(*argv, timeout=timeout)


async def _adb_has_device(runner: Runner) -> bool:
    r = await runner("adb", "devices", timeout=8)
    if r["exit_code"] != 0:
        return False
    return any("\tdevice" in ln for ln in (r.get("stdout") or "").splitlines())


async def _device_shell(
    runner: Runner, use_adb: bool, *cmd: str, timeout: float = 10.0
) -> dict[str, Any]:
    if use_adb:
        return await runner("adb", "shell", *cmd, timeout=timeout)
    return await runner(*cmd, timeout=timeout)


def _input_text_arg(message: str) -> str:
    """Format text for `adb shell input text` (spaces -> %s)."""
    escaped = message.replace("\\", "\\\\").replace("%", "\\%").replace("'", "\\'")
    return escaped.replace(" ", "%s")


async def _read_ui_dump(runner: Runner, *, use_adb: bool) -> str:
    await _device_shell(runner, use_adb, "uiautomator", "dump", _UI_DUMP_PATH, timeout=12)
    r = await _device_shell(runner, use_adb, "cat", _UI_DUMP_PATH, timeout=10)
    await _device_shell(runner, use_adb, "rm", "-f", _UI_DUMP_PATH, timeout=5)
    return (r.get("stdout") or "").strip()


def _normalize_compose_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _compose_text_from_xml(xml_content: str) -> str:
    if not xml_content or "<hierarchy" not in xml_content:
        return ""
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return ""
    parts: list[str] = []
    for node in root.iter():
        res_id = (node.get("resource-id") or "").lower()
        clazz = (node.get("class") or "").lower()
        if not (
            any(fid in res_id for fid in _COMPOSE_FIELD_IDS)
            or "edittext" in clazz
        ):
            continue
        text = (node.get("text") or "").strip()
        if text and _normalize_compose_text(text) not in _COMPOSE_PLACEHOLDERS:
            parts.append(text)
    return " ".join(parts)


async def _read_compose_text(runner: Runner, *, use_adb: bool) -> str:
    xml_content = await _read_ui_dump(runner, use_adb=use_adb)
    return _compose_text_from_xml(xml_content)


def _message_present_in_compose(compose_text: str, message: str) -> bool:
    compose_n = _normalize_compose_text(compose_text)
    msg_n = _normalize_compose_text(message)
    if not msg_n:
        return False
    if msg_n in compose_n:
        return True
    # Intent pre-fill may truncate or drop punctuation; match a long prefix.
    prefix = msg_n[: min(24, len(msg_n))]
    return len(prefix) >= 8 and prefix in compose_n


async def _verify_message_sent(
    runner: Runner, *, use_adb: bool, message: str, had_text_before: bool
) -> bool:
    """True when compose no longer contains the outgoing message."""
    if not had_text_before:
        return False
    await asyncio.sleep(0.7)
    compose_after = await _read_compose_text(runner, use_adb=use_adb)
    return not _message_present_in_compose(compose_after, message)


async def _attempt_send_actions(
    runner: Runner,
    *,
    use_adb: bool,
    width: int | None,
    height: int | None,
    retries: int,
) -> list[str]:
    """Run tap / enter / coordinate attempts; return methods tried."""
    tried: list[str] = []
    ok, detail = await _tap_ui_targets(
        runner,
        use_adb=use_adb,
        retries=retries,
        match_fn=lambda n: _match_send_button(n, width, height),
    )
    if ok:
        tried.append(detail)
    r = await _device_shell(runner, use_adb, "input", "keyevent", "66", timeout=5)
    if r["exit_code"] == 0:
        tried.append("keyevent ENTER")
    if width and height:
        tap_x, tap_y = int(width * 0.95), int(height * 0.66)
        ok2, detail2 = await _tap_xy(runner, use_adb=use_adb, x=tap_x, y=tap_y)
        if ok2:
            tried.append(f"screen_calc ({tap_x},{tap_y}) on {width}x{height}")
    return tried


def _default_have(binary: str) -> bool:
    return shutil.which(binary) is not None


def _looks_like_phone(value: str) -> bool:
    cleaned = re.sub(r"[\s\-().]", "", value.strip())
    return bool(re.match(r"^\+?\d[\d]{5,}$", cleaned))


def _normalize_phone_key(number: str) -> str:
    """Digits-only key for deduping the same number across Gmail/sync duplicates."""
    digits = re.sub(r"\D", "", number).lstrip("0") or re.sub(r"\D", "", number)
    # Same contact often appears as +91…, 91…, and local 10-digit — keep last 10.
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def _contact_match_score(name: str, needle: str) -> tuple[int, str]:
    nl, nq = name.lower().strip(), needle.lower().strip()
    if nl == nq:
        return (0, nl)
    if nl.startswith(nq):
        return (1, nl)
    return (2, nl)


def _dedupe_and_rank_contacts(matches: list[dict[str, str]], needle: str) -> list[dict[str, str]]:
    """Collapse duplicate numbers; prefer exact name match, then prefix, then contains."""
    ranked = sorted(
        matches,
        key=lambda m: _contact_match_score(m["name"], needle),
    )
    seen: set[str] = set()
    unique: list[dict[str, str]] = []
    for contact in ranked:
        key = _normalize_phone_key(contact["number"])
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(contact)
    return unique


def _parse_contact_list(stdout: str, needle: str) -> list[dict[str, str]]:
    """Match contacts whose display name contains ``needle`` (case-insensitive)."""
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    q = needle.lower().strip()
    matches: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name", "")).strip()
        number = str(row.get("number", "")).strip()
        if q in name.lower() and number:
            matches.append({"name": name, "number": number})
    return matches


async def _lookup_contacts(
    name: str,
    runner: Runner,
    *,
    has_termux: bool,
    have: Callable[[str], bool],
) -> list[dict[str, str]]:
    if not has_termux or not have("termux-contact-list"):
        return []
    r = await runner("termux-contact-list", timeout=20)
    if r["exit_code"] != 0:
        return []
    return _parse_contact_list(r.get("stdout") or "", name)


def _shell_error(result: dict) -> str | None:
    if result.get("exit_code") == 0:
        return None
    detail = (result.get("stderr") or result.get("stdout") or "").strip()
    return detail or "command failed"


async def _resolve_recipient(
    params: dict,
    runner: Runner,
    *,
    has_termux: bool,
    have: Callable[[str], bool],
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """Resolve phone number from ``phone``, ``name``, or ``recipient`` param."""
    recipient = str(
        params.get("phone") or params.get("name") or params.get("recipient") or ""
    ).strip()
    if not recipient:
        return None, None, "phone, name, or recipient + message required"
    if _looks_like_phone(recipient):
        return recipient, None, None
    matches = await _lookup_contacts(recipient, runner, has_termux=has_termux, have=have)
    if not matches:
        return None, None, f"no contact found matching '{recipient}'"
    unique = _dedupe_and_rank_contacts(matches, recipient)
    if not unique:
        return None, None, f"no contact with a usable number matching '{recipient}'"

    try:
        pick = int(params.get("index", 0))
    except (TypeError, ValueError):
        pick = 0
    if pick < 0 or pick >= len(unique):
        return None, None, f"index {pick} out of range (0-{len(unique) - 1})"

    chosen = unique[pick]
    resolved: dict[str, Any] = {"name": chosen["name"], "number": chosen["number"]}
    alternates = [u for i, u in enumerate(unique) if i != pick]
    if alternates:
        resolved["alternates"] = [
            {"name": alt["name"], "number": alt["number"]} for alt in alternates[:5]
        ]
        if len(alternates) > 5:
            resolved["alternates_note"] = f"+{len(alternates) - 5} more numbers"
    return chosen["number"], resolved, None


def _whatsapp_digits(number: str) -> str:
    """E.164 digits only — WhatsApp URIs reject '+'."""
    return re.sub(r"\D", "", number)


async def _get_screen_size(runner: Runner, *, use_adb: bool) -> tuple[int | None, int | None]:
    r = await _device_shell(runner, use_adb, "wm", "size", timeout=5)
    if r["exit_code"] != 0:
        return None, None
    out = r.get("stdout") or ""
    override = re.search(r"Override size:\s*(\d+)\s*x\s*(\d+)", out, re.IGNORECASE)
    if override:
        return int(override.group(1)), int(override.group(2))
    physical = re.search(r"Physical size:\s*(\d+)\s*x\s*(\d+)", out, re.IGNORECASE)
    if physical:
        return int(physical.group(1)), int(physical.group(2))
    generic = re.search(r"(\d+)\s*x\s*(\d+)", out)
    if generic:
        return int(generic.group(1)), int(generic.group(2))
    return None, None


def _node_center(bounds: str) -> tuple[int, int] | None:
    nums = re.findall(r"\d+", bounds)
    if len(nums) < 4:
        return None
    x1, y1, x2, y2 = (int(n) for n in nums[:4])
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1 + x2) // 2, (y1 + y2) // 2


async def _tap_xy(
    runner: Runner, *, use_adb: bool, x: int, y: int
) -> tuple[bool, str]:
    r = await _device_shell(
        runner, use_adb, "input", "tap", str(x), str(y), timeout=5
    )
    if r["exit_code"] == 0:
        return True, f"tap ({x},{y})"
    detail = (r.get("stderr") or r.get("stdout") or "").strip()
    return False, detail or "tap failed"


async def _tap_ui_targets(
    runner: Runner,
    *,
    use_adb: bool,
    match_fn,
    retries: int = 3,
    delay: float = 1.0,
) -> tuple[bool, str]:
    for attempt in range(retries):
        xml_content = await _read_ui_dump(runner, use_adb=use_adb)
        if xml_content and "<hierarchy" in xml_content:
            try:
                root = ET.fromstring(xml_content)
                best: tuple[int, int, int, str] | None = None
                for node in root.iter():
                    hit = match_fn(node)
                    if not hit:
                        continue
                    score, label = hit
                    center = _node_center(node.get("bounds", ""))
                    if center is None:
                        continue
                    cx, cy = center
                    cand = (score, cx, cy, label)
                    if best is None or cand[0] > best[0]:
                        best = cand
                if best:
                    _, cx, cy, label = best
                    ok, detail = await _tap_xy(runner, use_adb=use_adb, x=cx, y=cy)
                    if ok:
                        return True, f"{label} @ ({cx},{cy})"
            except ET.ParseError:
                pass
        if attempt < retries - 1:
            await asyncio.sleep(delay)
    return False, "ui target not found"


def _match_continue_chat(node: ET.Element) -> tuple[int, str] | None:
    desc = (node.get("content-desc") or "").lower()
    text = (node.get("text") or "").lower()
    hay = f"{desc} {text}"
    if any(m in hay for m in _CONTINUE_CHAT_MARKERS) and (node.get("clickable") or "").lower() == "true":
        return 20, "continue-to-chat"
    if "continue" in hay and "chat" in hay:
        return 15, "continue-to-chat"
    return None


def _match_compose_field(node: ET.Element, height: int | None) -> tuple[int, str] | None:
    res_id = (node.get("resource-id") or "").lower()
    clazz = (node.get("class") or "").lower()
    desc = (node.get("content-desc") or "").lower()
    score = 0
    if any(fid in res_id for fid in _COMPOSE_FIELD_IDS):
        score += 15
    if "edittext" in clazz:
        score += 8
    if "type a message" in desc or "message" in desc:
        score += 5
    if score <= 0:
        return None
    center = _node_center(node.get("bounds", ""))
    if center and height and center[1] > int(height * 0.55):
        score += 4
    return score, "compose-field"


def _match_send_button(
    node: ET.Element, width: int | None, height: int | None
) -> tuple[int, str] | None:
    desc = (node.get("content-desc") or "").lower()
    text = (node.get("text") or "").lower()
    res_id = (node.get("resource-id") or "").lower()
    clickable = (node.get("clickable") or "").lower() == "true"
    haystack = f"{desc} {text} {res_id}"
    if "send" not in haystack:
        return None
    if any(neg in haystack for neg in _SEND_NEGATIVE):
        return None
    center = _node_center(node.get("bounds", ""))
    if center is None:
        return None
    cx, cy = center
    score = 0
    if any(p in res_id for p in _SEND_POSITIVE_IDS):
        score += 10
    if desc.strip() in ("send", "send message", "send button"):
        score += 6
    if text.strip() == "send":
        score += 4
    if clickable:
        score += 4
    if height and cy > int(height * 0.60):
        score += 3
    if width and cx > int(width * 0.50):
        score += 2
    if score <= 0:
        return None
    return score, f"send id='{res_id or desc or text}'"


async def _tap_send_button(
    runner: Runner,
    *,
    use_adb: bool,
    message: str,
    had_text_before: bool,
    retries: int = 3,
) -> tuple[bool, str]:
    width, height = await _get_screen_size(runner, use_adb=use_adb)
    tried = await _attempt_send_actions(
        runner, use_adb=use_adb, width=width, height=height, retries=retries
    )
    if await _verify_message_sent(
        runner, use_adb=use_adb, message=message, had_text_before=had_text_before
    ):
        return True, tried[0] if tried else "verified send"
    return False, "send not verified — message still in compose field"


def _find_entry_center(xml_content: str) -> tuple[int, int] | None:
    if not xml_content or "<hierarchy" not in xml_content:
        return None
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        return None
    for node in root.iter():
        res_id = node.get("resource-id") or ""
        if not res_id.endswith("/entry") and "com.whatsapp:id/entry" not in res_id:
            continue
        return _node_center(node.get("bounds", ""))
    return None


async def _tap_whatsapp_entry(
    runner: Runner, *, use_adb: bool, width: int | None, height: int | None
) -> tuple[bool, str]:
    xml_content = await _read_ui_dump(runner, use_adb=use_adb)
    center = _find_entry_center(xml_content)
    if center:
        ok, detail = await _tap_xy(runner, use_adb=use_adb, x=center[0], y=center[1])
        if ok:
            return True, f"entry @ ({center[0]},{center[1]})"
    if width and height:
        cx, cy = int(width * 0.45), int(height * 0.64)
        return await _tap_xy(runner, use_adb=use_adb, x=cx, y=cy)
    return False, "entry not found"


async def _compose_and_type(
    runner: Runner, *, use_adb: bool, message: str
) -> tuple[bool, str, bool]:
    """Return (typed_ok, detail, had_text_before_send)."""
    width, height = await _get_screen_size(runner, use_adb=use_adb)
    await _tap_ui_targets(
        runner,
        use_adb=use_adb,
        retries=2,
        match_fn=lambda n: _match_continue_chat(n),
    )
    await asyncio.sleep(0.8)

    compose_before = await _read_compose_text(runner, use_adb=use_adb)
    if _message_present_in_compose(compose_before, message):
        return True, "pre-filled from intent", True

    tapped, tap_detail = await _tap_whatsapp_entry(
        runner, use_adb=use_adb, width=width, height=height
    )
    if not tapped:
        return False, f"compose field not focused ({tap_detail})", False

    await asyncio.sleep(0.5)
    r = await _device_shell(
        runner, use_adb, "input", "text", _input_text_arg(message), timeout=12
    )
    if r["exit_code"] != 0:
        detail = (r.get("stderr") or r.get("stdout") or "").strip()
        return False, detail or "input text failed", False

    await asyncio.sleep(0.5)
    compose_after = await _read_compose_text(runner, use_adb=use_adb)
    if _message_present_in_compose(compose_after, message):
        return True, "typed via input text", True
    if compose_after.strip():
        return True, "compose has text (partial match)", True
    return False, "typed but compose field still empty", False


async def _open_whatsapp_compose(runner: Runner, phone_digits: str, message: str) -> dict[str, Any]:
    """Open WhatsApp chat with message pre-filled — direct chat URI first."""
    encoded = urllib.parse.quote(message)
    jid = f"{phone_digits}@s.whatsapp.net"
    attempts = [
        (
            "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", f"whatsapp://send?phone={phone_digits}&text={encoded}",
            "-p", _WHATSAPP_PKG,
        ),
        (
            "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", f"https://wa.me/{phone_digits}?text={encoded}",
            "-p", _WHATSAPP_PKG,
        ),
        (
            "am", "start",
            "-a", "android.intent.action.SEND",
            "-t", "text/plain",
            "--es", "android.intent.extra.TEXT", message,
            "--es", "jid", jid,
            "-p", _WHATSAPP_PKG,
        ),
    ]
    last: dict[str, Any] = {"exit_code": -1, "stdout": "", "stderr": "no attempt"}
    for argv in attempts:
        last = await runner(*argv, timeout=15)
        if _launch_ok(last):
            return last
    return last


def build_phone_skills(
    runner: Runner = termux_android_runner, have: Callable[[str], bool] = _default_have
) -> dict[str, SkillFn]:
    """Builds the phone.* skill table, closing over `runner` (the shell
    boundary) and a Termux capability check evaluated once at build time
    (mirrors agent_phone.py's import-time HAS_TERMUX detection)."""
    has_termux = have("termux-sms-send")

    async def skill_battery(params: dict) -> dict:
        if has_termux:
            r = await runner("termux-battery-status", timeout=10)
            if r["exit_code"] == 0:
                try:
                    info = json.loads(r["stdout"])
                    return {
                        "ok": True,
                        "battery": {
                            "level": info.get("percentage"),
                            "charging": info.get("status", "").upper() == "CHARGING",
                            "health": info.get("health"),
                            "temp": info.get("temperature"),
                        },
                    }
                except json.JSONDecodeError:
                    pass
        # Fallback: dumpsys (works on plain adb/Android shells without termux-api)
        r = await runner("dumpsys", "battery", timeout=10)
        info = {}
        for line in r["stdout"].splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip().lower()] = v.strip()
        level = info.get("level")
        return {
            "ok": r["exit_code"] == 0,
            "battery": {
                "level": int(level) if level and level.isdigit() else None,
                "charging": any(
                    info.get(k, "false").lower() == "true"
                    for k in ("ac powered", "usb powered")
                ),
            },
        }

    async def skill_tts(params: dict) -> dict:
        text = str(params.get("text", "")).strip()
        if not text:
            return {"ok": False, "error": "missing text"}
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-tts-speak", text, timeout=60)
        return {"ok": r["exit_code"] == 0, **r}

    async def skill_sms_send(params: dict) -> dict:
        message = str(params.get("message", "")).strip()
        if not message:
            return {"ok": False, "error": "message required"}
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        number, resolved, err = await _resolve_recipient(
            params, runner, has_termux=has_termux, have=have
        )
        if err:
            return {"ok": False, "error": err}
        r = await runner("termux-sms-send", "-n", number, message, timeout=20)
        ok = r["exit_code"] == 0
        out: dict[str, Any] = {"ok": ok, "phone": number, "message": message}
        if not ok:
            out["error"] = _shell_error(r)
        if resolved:
            out["resolved_contact"] = resolved
        return out

    async def skill_torch(params: dict) -> dict:
        on = bool(params.get("on", True))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-torch", "on" if on else "off", timeout=5)
        return {"ok": r["exit_code"] == 0, "on": on, **r}

    async def skill_vibrate(params: dict) -> dict:
        ms = int(params.get("ms", 500))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-vibrate", "-d", str(ms), timeout=max(10, ms // 1000 + 3))
        return {"ok": r["exit_code"] == 0, "ms": ms, **r}

    async def skill_notify(params: dict) -> dict:
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner(
            "termux-notification",
            "--id", str(int(params.get("id", 9000))),
            "--title", str(params.get("title", "JARVIS")),
            "--content", str(params.get("content", "")),
            timeout=10,
        )
        return {"ok": r["exit_code"] == 0, **r}

    async def skill_ring(params: dict) -> dict:
        """Find-my-phone: flash, vibrate, and loud TTS for N seconds."""
        seconds = max(1, min(60, int(params.get("seconds", 10))))
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        await runner("termux-volume", "music", "15", timeout=5)
        await runner("termux-torch", "on", timeout=3)

        async def _flasher():
            for _ in range(seconds):
                await runner("termux-vibrate", "-d", "400", timeout=3)
                await asyncio.sleep(0.5)

        async def _talker():
            for _ in range(seconds):
                await runner(
                    "termux-tts-speak", "-r", "0.9", "-p", "1.3",
                    "Jarvis locator ping", timeout=4,
                )
                await asyncio.sleep(0.2)

        try:
            async with asyncio.timeout(seconds + 5):
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(_flasher())
                    tg.create_task(_talker())
        finally:
            await runner("termux-torch", "off", timeout=3)
        return {"ok": True, "seconds": seconds}

    async def skill_location(params: dict) -> dict:
        if not has_termux:
            return {"ok": False, "error": "termux-api not installed"}
        r = await runner("termux-location", "-p", "gps", timeout=30)
        if r["exit_code"] != 0:
            return {"ok": False, **r}
        try:
            return {"ok": True, "location": json.loads(r["stdout"])}
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad json from termux-location", **r}

    async def skill_open_app(params: dict) -> dict:
        app = str(params.get("app", "")).strip()
        if not app:
            return {"ok": False, "error": "missing app"}
        PKGS = {
            "whatsapp": "com.whatsapp", "chrome": "com.android.chrome",
            "youtube": "com.google.android.youtube",
            "gmail": "com.google.android.gm", "spotify": "com.spotify.music",
            "instagram": "com.instagram.android", "settings": "com.android.settings",
            "maps": "com.google.android.apps.maps",
        }
        pkg = PKGS.get(app.lower(), app)

        # am start must run in native Termux (see termux_android_runner).
        r = await runner(
            "am", "start",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-p", pkg,
            timeout=15,
        )
        if not _launch_ok(r):
            resolved = await runner(
                "cmd", "package", "resolve-activity", "--brief",
                "-c", "android.intent.category.LAUNCHER", pkg,
                timeout=10,
            )
            lines = [
                ln.strip()
                for ln in (resolved.get("stdout") or "").splitlines()
                if ln.strip() and "/" in ln
            ]
            if lines:
                r = await runner("am", "start", "-n", lines[-1], timeout=15)
        if not _launch_ok(r) and have("monkey"):
            r = await runner(
                "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1",
                timeout=15,
            )
        return {"ok": _launch_ok(r), "package": pkg, **r}

    async def skill_whatsapp_send(params: dict) -> dict:
        msg = str(params.get("message", "")).strip()
        if not msg:
            return {"ok": False, "error": "message required"}
        auto_send = params.get("auto_send", True)
        if isinstance(auto_send, str):
            auto_send = auto_send.lower() not in ("0", "false", "no")
        number, resolved, err = await _resolve_recipient(
            params, runner, has_termux=has_termux, have=have
        )
        if err:
            return {"ok": False, "error": err}
        phone_digits = _whatsapp_digits(number or "")
        if len(phone_digits) < 8:
            return {"ok": False, "error": "invalid phone number for WhatsApp"}

        r = await _open_whatsapp_compose(runner, phone_digits, msg)
        if not _launch_ok(r):
            return {
                "ok": False,
                "error": _shell_error(r) or "could not open WhatsApp",
                "phone": phone_digits,
            }

        out: dict[str, Any] = {
            "ok": True,
            "phone": phone_digits,
            "message": msg,
            "sent": False,
        }
        if resolved:
            out["resolved_contact"] = resolved

        if not auto_send:
            out["note"] = "WhatsApp opened — tap Send on your phone"
            return out

        use_adb = await _adb_has_device(runner)
        if not use_adb:
            out["ok"] = False
            out["error"] = (
                "WhatsApp auto-send needs Wireless Debugging + adb connect on the phone"
            )
            out["hint"] = (
                "On the phone: Developer options -> Wireless debugging ON, then run "
                "bash ~/Jarvis-2.0/scripts/setup_whatsapp_adb.sh — or use phone.sms.send "
                "for fully automatic text delivery"
            )
            return out

        await asyncio.sleep(4.0)
        typed, type_detail, had_text = await _compose_and_type(
            runner, use_adb=use_adb, message=msg
        )
        if not typed:
            out["ok"] = False
            out["error"] = f"Could not type message in WhatsApp ({type_detail})"
            out["hint"] = "Open WhatsApp to the chat screen and retry, or use auto_send:false"
            return out

        await asyncio.sleep(0.8)
        tapped, method = await _tap_send_button(
            runner, use_adb=use_adb, message=msg, had_text_before=had_text
        )
        if tapped:
            out["sent"] = True
            out["send_method"] = method
            out["typed"] = type_detail
            return out

        out["ok"] = False
        out["error"] = "Message in compose but Send could not be verified"
        out["hint"] = method or "Check WhatsApp chat — tap Send manually"
        out["typed"] = type_detail
        return out

    return {
        "phone.battery": skill_battery,
        "phone.tts": skill_tts,
        "phone.sms.send": skill_sms_send,
        "phone.torch": skill_torch,
        "phone.vibrate": skill_vibrate,
        "phone.notify": skill_notify,
        "phone.ring": skill_ring,
        "phone.location": skill_location,
        "phone.app.open": skill_open_app,
        "phone.whatsapp.send": skill_whatsapp_send,
    }
