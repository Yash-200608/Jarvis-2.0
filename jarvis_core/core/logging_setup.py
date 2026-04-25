"""Loguru configuration with per-component colour coding.

Every module in Jarvis-Core logs through ``loguru.logger.bind(component="...")``.
The colour map below makes it immediately obvious whether a line came from the
Planner, the Critic, the Executor, the Sensor loop, or the Transport layer.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

from loguru import logger

# Colour map: matches the spec — different colours for [PLANNER]/[CRITIC]/[EXECUTOR].
_COMPONENT_COLOURS: Final[dict[str, str]] = {
    "PLANNER": "<cyan>",
    "CRITIC": "<magenta>",
    "EXECUTOR": "<yellow>",
    "SENSOR": "<green>",
    "TRANSPORT": "<blue>",
    "BRAIN": "<light-cyan>",
    "MEMORY": "<light-magenta>",
    "SCHED": "<light-yellow>",
    "PHONE": "<light-green>",
    "CORE": "<white>",
}


def _format(record: dict) -> str:
    """Per-record formatter that colours the [COMPONENT] tag."""
    component = record["extra"].get("component", "CORE")
    colour = _COMPONENT_COLOURS.get(component, "<white>")
    close = colour.replace("<", "</")
    record["extra"]["component_tag"] = f"{colour}[{component}]{close}"
    return (
        "<dim>{time:HH:mm:ss.SSS}</dim> "
        "<level>{level: <8}</level> "
        "{extra[component_tag]} "
        "<level>{message}</level>\n"
    )


def configure_logging(
    log_file: str | Path | None = None,
    level: str = "INFO",
    rotation: str = "10 MB",
    retention: str = "14 days",
) -> None:
    """Reset Loguru sinks and install our colourised stdout + rotating file sink.

    Safe to call multiple times — earlier sinks are removed first so we don't
    end up with duplicate log lines after a hot reload.
    """
    logger.remove()
    logger.add(sys.stdout, level=level, format=_format, colorize=True)
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            str(log_file),
            level=level,
            format=_format,
            rotation=rotation,
            retention=retention,
            enqueue=True,  # async-safe writes from multiple coroutines
        )


def get_logger(component: str):
    """Return a logger pre-bound with the given component tag."""
    return logger.bind(component=component.upper())
