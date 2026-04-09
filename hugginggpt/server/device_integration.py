import requests


class DeviceAdapter:
    def execute(self, action, params):
        raise NotImplementedError()


class EndpointDeviceAdapter(DeviceAdapter):
    def __init__(self, base_url, token, device_name):
        self.base_url = (base_url or "").rstrip("/")
        self.token = token or ""
        self.device_name = device_name

    def configured(self):
        return bool(self.base_url and self.token)

    def execute(self, action, params):
        if not self.configured():
            return {"ok": False, "error": f"{self.device_name} integration is not configured."}

        url = f"{self.base_url}/execute"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        payload = {"action": action, "params": params}
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": f"{self.device_name} endpoint error {response.status_code}: {response.text}",
            }
        try:
            body = response.json()
        except Exception:
            body = {"raw": response.text}
        return {"ok": True, "result": body}


class DeviceOrchestrator:
    def __init__(self, adapters):
        self.adapters = adapters or {}

    def available(self):
        return bool(self.adapters)

    def execute(self, command):
        if not self.available():
            return {"ok": False, "error": "No phone/computer integration is configured."}
        target = command.get("target")
        adapter = self.adapters.get(target)
        if adapter is None:
            return {"ok": False, "error": f"Target device '{target}' is not configured."}
        action = command.get("action")
        params = command.get("params", {})
        return adapter.execute(action, params)

