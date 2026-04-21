"""
Conversation memory module for JARVIS.
Enables follow-up questions and context-aware responses.
"""

import time
from typing import List, Dict, Optional, Any
from collections import deque
import logging

logger = logging.getLogger(__name__)


class ConversationMemory:
    """
    Manages conversation history with configurable context window.
    Stores user messages and assistant responses for follow-up context.
    """

    def __init__(self, max_turns: int = 10, max_age_seconds: int = 3600):
        """
        Initialize conversation memory.

        Args:
            max_turns: Maximum number of conversation turns to retain
            max_age_seconds: Maximum age of messages before expiration (default: 1 hour)
        """
        self.max_turns = max_turns
        self.max_age_seconds = max_age_seconds
        self.history = deque(maxlen=max_turns * 2)  # user + assistant per turn
        self._lock = None
        try:
            import threading
            self._lock = threading.Lock()
        except ImportError:
            pass

    def add_turn(self, user_message: str, assistant_response: str) -> None:
        """
        Add a conversation turn (user message + assistant response).

        Args:
            user_message: The user's input
            assistant_response: The assistant's response
        """
        timestamp = time.time()
        with self._lock if self._lock else nullcontext():
            self.history.append({
                "role": "user",
                "content": user_message,
                "timestamp": timestamp,
            })
            self.history.append({
                "role": "assistant",
                "content": assistant_response,
                "timestamp": timestamp,
            })
            self._cleanup_old_messages()

    def get_context(self, max_turns: Optional[int] = None) -> List[Dict[str, str]]:
        """
        Get conversation context for inclusion in prompts.

        Args:
            max_turns: Override default max_turns for this retrieval

        Returns:
            List of message dicts (role, content) for context
        """
        turns = max_turns or self.max_turns
        with self._lock if self._lock else nullcontext():
            self._cleanup_old_messages()
            # Return last N turns (each turn = 2 messages)
            messages = list(self.history)[-turns * 2:]
            # Return only content, not metadata
            return [{"role": m["role"], "content": m["content"]} for m in messages]

    def clear(self) -> None:
        """Clear all conversation history."""
        with self._lock if self._lock else nullcontext():
            self.history.clear()
            logger.info("Conversation memory cleared")

    def _cleanup_old_messages(self) -> None:
        """Remove messages older than max_age_seconds."""
        cutoff = time.time() - self.max_age_seconds
        while self.history and self.history[0].get("timestamp", 0) < cutoff:
            self.history.popleft()

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of the conversation state."""
        with self._lock if self._lock else nullcontext():
            turns = len(self.history) // 2
            return {
                "total_turns": turns,
                "oldest_message": self.history[0]["timestamp"] if self.history else None,
                "newest_message": self.history[-1]["timestamp"] if self.history else None,
            }


# Global conversation memory instance (per-user session)
# In multi-user scenarios, this should be keyed by session/user ID
_global_memory: Optional[ConversationMemory] = None


def get_memory() -> ConversationMemory:
    """Get or create the global conversation memory instance."""
    global _global_memory
    if _global_memory is None:
        _global_memory = ConversationMemory(max_turns=10)
    return _global_memory


def init_memory(max_turns: int = 10, max_age_seconds: int = 3600) -> ConversationMemory:
    """Initialize global conversation memory with custom settings."""
    global _global_memory
    _global_memory = ConversationMemory(max_turns=max_turns, max_age_seconds=max_age_seconds)
    return _global_memory


def nullcontext():
    """Simple null context manager for when locking is not available."""
    yield


# Convenience functions for use in awesome_chat.py
def add_to_memory(user_message: str, assistant_response: str) -> None:
    """Add a conversation turn to global memory."""
    get_memory().add_turn(user_message, assistant_response)


def get_conversation_context(max_turns: Optional[int] = None) -> List[Dict[str, str]]:
    """Get conversation context from global memory."""
    return get_memory().get_context(max_turns)


def clear_memory() -> None:
    """Clear global conversation memory."""
    get_memory().clear()
