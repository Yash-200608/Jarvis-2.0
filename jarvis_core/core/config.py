"""Typed config loader for Jarvis-Core.

Loads the YAML at ``configs/jarvis_core.yaml`` (or ``$JARVIS_CONFIG``) and the
sibling ``.env`` file, then validates everything through Pydantic models so the
rest of the codebase can rely on attribute access instead of dict lookups.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH: Path = PROJECT_ROOT / "configs" / "jarvis_core.yaml"


class MqttTopics(BaseModel):
    phone_cmd: str
    phone_state: str
    pc_cmd: str
    pc_state: str
    heartbeat_phone: str
    heartbeat_pc: str
    sensors: str
    alerts: str


class MqttConfig(BaseModel):
    host: str
    port: int = 1883
    client_id: str
    keepalive: int = 30
    clean_session: bool = False
    qos: int = 1
    topics: MqttTopics
    username: str | None = None
    password: str | None = None


class OllamaConfig(BaseModel):
    base_url: str
    planner_model: str
    critic_model: str
    idle_unload_seconds: int = 600
    request_timeout_seconds: int = 60


class BrainConfig(BaseModel):
    inbox_dir: str
    history_log: str
    knowledge_graph: str
    history_token_limit: int = 4000
    critic_rejection_threshold: int = 3
    critic_min_safety_score: int = 8


class MemoryConfig(BaseModel):
    chroma_path: str
    collection: str
    embedding_model: str


class SandboxConfig(BaseModel):
    root: str


class SchedulerConfig(BaseModel):
    maintenance_interval_seconds: int = 3600
    phone_offline_after_seconds: int = 180


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "./logs/jarvis_core.log"
    rotation: str = "10 MB"
    retention: str = "14 days"


class JarvisConfig(BaseModel):
    """Top-level config bundle returned by :func:`load_config`."""

    mqtt: MqttConfig
    ollama: OllamaConfig
    brain: BrainConfig
    memory: MemoryConfig
    sandbox: SandboxConfig
    scheduler: SchedulerConfig
    logging: LoggingConfig

    # Resolved later from $JARVIS_HMAC_KEY — never lives in YAML.
    hmac_key: str = Field(default="", repr=False)

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT


def _resolve_path(maybe_relative: str) -> str:
    """Make any relative path live under the project root."""
    p = Path(maybe_relative)
    return str(p if p.is_absolute() else PROJECT_ROOT / p)


@lru_cache(maxsize=1)
def load_config(path: str | os.PathLike[str] | None = None) -> JarvisConfig:
    """Load + validate config. Cached so repeat calls return the same object."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)

    cfg_path = Path(path or os.environ.get("JARVIS_CONFIG") or DEFAULT_CONFIG_PATH)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Jarvis-Core config not found: {cfg_path}")

    with cfg_path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh)

    # Inject env-derived secrets before validation.
    raw.setdefault("mqtt", {})
    raw["mqtt"]["username"] = os.environ.get("MQTT_USERNAME") or None
    raw["mqtt"]["password"] = os.environ.get("MQTT_PASSWORD") or None
    if env_url := os.environ.get("OLLAMA_BASE_URL"):
        raw.setdefault("ollama", {})["base_url"] = env_url

    cfg = JarvisConfig.model_validate(raw)
    cfg.hmac_key = os.environ.get("JARVIS_HMAC_KEY", "")

    # Normalise filesystem paths relative to the project root so the rest of
    # the codebase doesn't have to care about the CWD it was launched from.
    cfg.brain.inbox_dir = _resolve_path(cfg.brain.inbox_dir)
    cfg.brain.history_log = _resolve_path(cfg.brain.history_log)
    cfg.brain.knowledge_graph = _resolve_path(cfg.brain.knowledge_graph)
    cfg.memory.chroma_path = _resolve_path(cfg.memory.chroma_path)
    cfg.sandbox.root = _resolve_path(cfg.sandbox.root)
    cfg.logging.file = _resolve_path(cfg.logging.file)
    return cfg
