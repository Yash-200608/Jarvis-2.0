"""JARVIS node SDK — MQTT transport, HMAC signing, and filesystem sandboxing.

Extracted from ``jarvis_core/core/`` (ADR-014) as the reusable, cognition-free
half of Jarvis-Core: the primitives a physical node needs to talk to the rest
of the ecosystem, independent of any planning/memory/LLM logic. Cognition
(``brain.py``, ``memory.py``, ``scheduler.py``) stays in ``jarvis_core/`` for
now and imports these modules by their new location.

See also: ``jarvis_core/agent/phone_executor.py`` — the Termux-side
counterpart of this SDK's ``signing.py``, intentionally NOT part of this
package. It ships as a single dependency-free file for Termux deployment and
duplicates the signing/verification algorithm by hand rather than importing
it. Keep its ``verify()`` in sync with ``signing.py``'s
``sign_envelope()``/``verify_envelope()`` when either changes.
"""

from __future__ import annotations

from .chimera_contract import CONTRACT_VERSION, ChimeraEnvelope, RiskTier, sign, verify
from .command_handler import handle_command
from .config import JarvisConfig, MqttConfig, load_config
from .logging_setup import configure_logging, get_logger
from .sandbox import SandboxViolation, is_inside_sandbox, safe_path
from .sensors import SensorEvent, SensorLoop
from .signing import MAX_SKEW_SECONDS, sign_envelope, verify_envelope
from .transport import MqttTransport

__all__ = [
    "JarvisConfig",
    "MqttConfig",
    "load_config",
    "configure_logging",
    "get_logger",
    "SandboxViolation",
    "is_inside_sandbox",
    "safe_path",
    "SensorEvent",
    "SensorLoop",
    "MAX_SKEW_SECONDS",
    "sign_envelope",
    "verify_envelope",
    "MqttTransport",
    "CONTRACT_VERSION",
    "ChimeraEnvelope",
    "RiskTier",
    "sign",
    "verify",
    "handle_command",
]
