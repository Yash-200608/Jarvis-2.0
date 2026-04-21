"""
Structured logging utilities for JARVIS.
Provides JSON-formatted logging with request correlation IDs.
"""

import logging
import json
import os
from datetime import datetime
from typing import Optional, Dict, Any
import threading

# Thread-local storage for request IDs
_request_local = threading.local()

def get_request_id() -> str:
    """Get the current request ID from thread-local storage."""
    return getattr(_request_local, 'request_id', None)

def set_request_id(request_id: str) -> None:
    """Set the request ID for the current thread."""
    _request_local.request_id = request_id

class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs log records as JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add request ID if present
        request_id = get_request_id()
        if request_id:
            log_entry["request_id"] = request_id

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields if present
        for key, value in record.__dict__.items():
            if key not in ['name', 'msg', 'args', 'created', 'filename', 'funcName',
                          'levelname', 'levelno', 'lineno', 'module', 'msecs',
                          'pathname', 'process', 'processName', 'relativeCreated',
                          'stack_info', 'exc_info', 'exc_text', 'thread', 'threadName',
                          'message', 'task', 'request_id']:
                try:
                    json.dumps(value)  # Check if serializable
                    log_entry[key] = value
                except (TypeError, ValueError):
                    log_entry[key] = str(value)

        return json.dumps(log_entry)


class RequestFilter(logging.Filter):
    """Filter that adds request ID to log records."""

    def __init__(self, request_id: Optional[str] = None):
        super().__init__()
        self.request_id = request_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = self.request_id or get_request_id()
        return True


def setup_structured_logging(
    name: str,
    level: int = logging.INFO,
    log_file: Optional[str] = None,
    console_output: bool = True,
    request_id: Optional[str] = None,
) -> logging.Logger:
    """
    Set up structured JSON logging for a component.

    Args:
        name: Logger name (usually __name__)
        level: Logging level (default: INFO)
        log_file: Optional file path to write logs to
        console_output: Whether to output to console (default: True)
        request_id: Optional request ID for correlation

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Clear existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create JSON formatter
    json_formatter = JSONFormatter()

    # Console handler
    if console_output:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(json_formatter)
        if request_id:
            console_handler.addFilter(RequestFilter(request_id))
        logger.addHandler(console_handler)

    # File handler
    if log_file:
        os.makedirs(os.path.dirname(log_file) or '.', exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(json_formatter)
        if request_id:
            file_handler.addFilter(RequestFilter(request_id))
        logger.addHandler(file_handler)

    return logger


def log_event(
    logger: logging.Logger,
    event_type: str,
    message: str,
    level: int = logging.INFO,
    **extra_data: Any,
) -> None:
    """
    Log a structured event with additional context.

    Args:
        logger: Logger instance
        event_type: Type of event (e.g., "task_start", "api_call", "error")
        message: Human-readable message
        level: Logging level
        **extra_data: Additional key-value pairs to include
    """
    extra_data["event_type"] = event_type
    logger.log(level, message, extra=extra_data)


class RequestContext:
    """Context manager for request-scoped logging with correlation ID."""

    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or str(uuid.uuid4())
        self._previous_id = None

    def __enter__(self):
        self._previous_id = get_request_id()
        set_request_id(self.request_id)
        return self.request_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        set_request_id(self._previous_id)
        return False


# Import uuid for RequestContext
import uuid
