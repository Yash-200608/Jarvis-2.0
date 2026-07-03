"""DEPRECATED -- moved to jarvis_node_sdk.transport (Priority #3 Milestone 1).

This shim exists only so any code still importing this module as
``core.transport`` or ``.transport`` (relative to jarvis_core/) keeps
working during Milestone 2's stabilization period. All new code should
import ``jarvis_node_sdk.transport`` directly. Delete this file once
jarvis_node_sdk is proven stable in the MQTT bridge work (Priority #3
Milestone 2+).
"""

from __future__ import annotations

import sys
from pathlib import Path

_JARVIS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_JARVIS_ROOT) not in sys.path:
    sys.path.insert(0, str(_JARVIS_ROOT))

from jarvis_node_sdk.transport import (  # noqa: E402,F401
    Handler,
    JsonDict,
    MqttTransport,
    PendingRequest,
)
