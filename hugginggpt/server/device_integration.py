import threading
import time
from collections import defaultdict

import requests


class DeviceAdapter:
    def execute(self, action, params, confirmation_token=None):
        raise NotImplementedError()


class EndpointDeviceAdapter(DeviceAdapter):
    def __init__(self, base_url, token, device_name):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.device_name = device_name

    def configured(self):
        return bool(self.base_url and self.token)

    def execute(self, action, params, confirmation_token=None):
        if not self.configured():
            return {"ok": False, "error": f"{self.device_name} integration is not configured."}

        url = f"{self.base_url}/execute"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"action": action, "params": params}
        if confirmation_token:
            payload["confirmation_token"] = confirmation_token

        # Actions that legitimately take longer than a quick tap
        SLOW_ACTIONS = {
            "send_sms", "send_whatsapp", "send_telegram",
            "send_instagram_dm", "send_message", "make_call",
            "take_screenshot", "record_screen", "push_file", "pull_file",
        }
        timeout = 60 if action in SLOW_ACTIONS else 15
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.ConnectionError:
            return {
                "ok": False,
                "error": (f"{self.device_name} bridge unreachable at {self.base_url} — "
                          f"is it running?"),
            }
        except requests.Timeout:
            return {
                "ok": False,
                "error": f"{self.device_name} bridge timed out after {timeout}s running {action}",
            }
        except requests.RequestException as e:
            return {"ok": False, "error": f"{self.device_name} bridge error: {e}"}

        if response.status_code >= 400:
            try:
                error_body = response.json()
            except Exception:
                error_body = None
            if isinstance(error_body, dict) and error_body.get("confirmation_required"):
                # Structured confirmation-required response -- preserve the
                # confirmation_token and action/params so a caller can
                # resubmit, rather than collapsing it to a generic error.
                return {"ok": False, "confirmation_required": True, "result": error_body}
            return {
                "ok": False,
                "error": f"{self.device_name} endpoint error {response.status_code}: {response.text}",
            }
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}
        return {"ok": True, "result": body}


# How long to wait after an action that opens/switches apps before running the next command
APP_OPEN_ACTIONS = {"open_app", "open_url"}
APP_OPEN_DELAY   = 4.0   # seconds — let the app fully load
DEFAULT_DELAY    = 0.8   # seconds between other sequential commands


class DeviceOrchestrator:
    def __init__(self, adapters):
        self.adapters = adapters or {}

    def available(self):
        return bool(self.adapters)

    def execute(self, command):
        """Execute a single command dict on the specified target device."""
        if not self.available():
            return {"ok": False, "error": "No phone/computer integration is configured."}
        target  = command.get("target")
        adapter = self.adapters.get(target)
        if adapter is None:
            return {"ok": False, "error": f"Target device '{target}' is not configured."}
        action = command.get("action")
        params = command.get("params", {})
        confirmation_token = command.get("confirmation_token")
        return adapter.execute(action, params, confirmation_token=confirmation_token)

    def execute_multi(self, commands):
        """
        Execute a list of commands intelligently:
        - Commands targeting DIFFERENT devices run in PARALLEL (threads).
        - Commands targeting the SAME device run SEQUENTIALLY (preserving order)
          with an automatic delay so apps have time to open before the next action.

        Returns a list of result dicts in the same order as `commands`.
        """
        results = [None] * len(commands)

        # Group commands by target, preserving original index
        device_groups = defaultdict(list)
        for i, cmd in enumerate(commands):
            device_groups[cmd.get("target", "phone")].append((i, cmd))

        def run_device_group(group):
            """Run all commands for one device sequentially."""
            for step, (idx, cmd) in enumerate(group):
                results[idx] = self.execute(cmd)
                # If there's a next command, wait so apps/screens have time to load
                if step < len(group) - 1:
                    action = cmd.get("action", "")
                    delay  = APP_OPEN_DELAY if action in APP_OPEN_ACTIONS else DEFAULT_DELAY
                    time.sleep(delay)

        # One thread per device group — parallel across devices, sequential within
        threads = []
        for target, group in device_groups.items():
            t = threading.Thread(target=run_device_group, args=(group,), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=120)

        return results
