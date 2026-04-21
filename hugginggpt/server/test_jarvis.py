"""
Unit tests for JARVIS server components.
Run: pytest test_jarvis.py -v
"""

import pytest
import time
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ═════════════════════════════════════════════════════════════════════════════
#  MEMORY MODULE TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestConversationMemory:
    """Test the ConversationMemory class from memory_module."""

    def test_import_memory_module(self):
        """Verify memory_module can be imported."""
        from memory_module import ConversationMemory, get_memory, add_to_memory, clear_memory
        assert ConversationMemory is not None

    def test_memory_init(self):
        """Test memory initialization with default params."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        assert mem.max_turns == 10
        assert mem.max_age_seconds == 3600
        assert len(mem.history) == 0

    def test_memory_init_custom_params(self):
        """Test memory initialization with custom params."""
        from memory_module import ConversationMemory
        mem = ConversationMemory(max_turns=5, max_age_seconds=300)
        assert mem.max_turns == 5
        assert mem.max_age_seconds == 300

    def test_add_turn(self):
        """Test adding a conversation turn."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        mem.add_turn("Hello", "Hi there!")
        assert len(mem.history) == 2
        assert mem.history[0]["role"] == "user"
        assert mem.history[0]["content"] == "Hello"
        assert mem.history[1]["role"] == "assistant"
        assert mem.history[1]["content"] == "Hi there!"

    def test_add_multiple_turns(self):
        """Test adding multiple conversation turns."""
        from memory_module import ConversationMemory
        mem = ConversationMemory(max_turns=3)
        for i in range(5):
            mem.add_turn(f"Question {i}", f"Answer {i}")
        # Should only keep last 3 turns (6 messages)
        assert len(mem.history) == 6
        assert mem.history[0]["content"] == "Question 2"

    def test_get_context(self):
        """Test retrieving conversation context."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        mem.add_turn("First question", "First answer")
        mem.add_turn("Second question", "Second answer")

        context = mem.get_context()
        assert len(context) == 4
        assert context[0] == {"role": "user", "content": "First question"}
        assert context[1] == {"role": "assistant", "content": "First answer"}

    def test_get_context_limited_turns(self):
        """Test retrieving limited conversation context."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        for i in range(5):
            mem.add_turn(f"Q{i}", f"A{i}")

        context = mem.get_context(max_turns=2)
        assert len(context) == 4
        assert context[0]["content"] == "Q3"

    def test_clear_memory(self):
        """Test clearing conversation memory."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        mem.add_turn("Hello", "Hi")
        assert len(mem.history) == 2
        mem.clear()
        assert len(mem.history) == 0

    def test_get_summary(self):
        """Test getting conversation summary."""
        from memory_module import ConversationMemory
        mem = ConversationMemory()
        mem.add_turn("Hello", "Hi")
        summary = mem.get_summary()
        assert summary["total_turns"] == 1
        assert summary["oldest_message"] is not None
        assert summary["newest_message"] is not None

    def test_global_memory_singleton(self):
        """Test that get_memory returns the same instance."""
        from memory_module import get_memory, clear_memory
        clear_memory()
        mem1 = get_memory()
        mem2 = get_memory()
        assert mem1 is mem2


# ═════════════════════════════════════════════════════════════════════════════
#  DEVICE COMMAND PARSING TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestDeviceCommandParsing:
    """Test the parse_device_command function from awesome_chat."""

    @pytest.fixture
    def parse_device_command(self):
        """Import and return parse_device_command function."""
        from awesome_chat import parse_device_command
        return parse_device_command

    def test_parse_phone_lock(self, parse_device_command):
        """Test parsing phone lock command."""
        result = parse_device_command("lock my phone")
        assert result is not None
        assert result["target"] == "phone"
        assert result["action"] == "lock_device"

    def test_parse_computer_shutdown(self, parse_device_command):
        """Test parsing computer shutdown command."""
        result = parse_device_command("shutdown the computer")
        assert result is not None
        assert result["target"] == "computer"
        assert result["action"] == "shutdown_device"

    def test_parse_phone_mute(self, parse_device_command):
        """Test parsing phone mute command."""
        result = parse_device_command("mute audio on phone")
        assert result is not None
        assert result["target"] == "phone"
        assert result["action"] == "mute_audio"
        assert result["params"]["muted"] == True

    def test_parse_phone_unmute(self, parse_device_command):
        """Test parsing phone unmute command."""
        result = parse_device_command("unmute the phone")
        assert result is not None
        assert result["target"] == "phone"
        assert result["action"] == "mute_audio"
        assert result["params"]["muted"] == False

    def test_parse_volume_control(self, parse_device_command):
        """Test parsing volume control command."""
        result = parse_device_command("set phone volume to 50")
        assert result is not None
        assert result["target"] == "phone"
        assert result["action"] == "set_volume"
        assert result["params"]["level"] == 50

    def test_parse_open_app(self, parse_device_command):
        """Test parsing open app command."""
        result = parse_device_command("open spotify on phone")
        assert result is not None
        assert result["target"] == "phone"
        assert result["action"] == "open_app"
        assert result["params"]["app"] == "spotify"

    def test_parse_pc_control(self, parse_device_command):
        """Test parsing PC control command."""
        result = parse_device_command("control pc lock")
        assert result is not None
        assert result["target"] == "computer"
        assert result["action"] == "lock_device"

    def test_empty_input(self, parse_device_command):
        """Test parsing empty input."""
        result = parse_device_command("")
        assert result is None

    def test_non_device_input(self, parse_device_command):
        """Test parsing non-device input."""
        result = parse_device_command("what is the weather?")
        assert result is None


# ═════════════════════════════════════════════════════════════════════════════
#  STATUS REPORT AND HIGH-RISK DETECTION TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestSafetyChecks:
    """Test safety-related functions from awesome_chat."""

    @pytest.fixture
    def safety_functions(self):
        """Import safety check functions."""
        from awesome_chat import is_status_report_request, is_high_risk_request, has_explicit_confirmation
        return is_status_report_request, is_high_risk_request, has_explicit_confirmation

    def test_status_report_detection(self, safety_functions):
        """Test detection of status report requests."""
        is_status, _, _ = safety_functions
        assert is_status("status report") == True
        assert is_status("system status") == True
        assert is_status("jarvis status") == True
        assert is_status("health check") == True
        assert is_status("what's the weather") == False

    def test_high_risk_detection(self, safety_functions):
        """Test detection of high-risk requests."""
        _, is_high_risk, _ = safety_functions
        assert is_high_risk("unlock door") == True
        assert is_high_risk("transfer money") == True
        assert is_high_risk("delete account") == True
        assert is_high_risk("format computer") == True
        assert is_high_risk("open spotify") == False

    def test_explicit_confirmation_detection(self, safety_functions):
        """Test detection of explicit confirmation."""
        _, _, has_confirm = safety_functions
        assert has_confirm("I confirm") == True
        assert has_confirm("confirmed") == True
        assert has_confirm("I authorize this action") == True
        assert has_confirm("yes do it") == False


# ═════════════════════════════════════════════════════════════════════════════
#  RATE LIMITER TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestRateLimiter:
    """Test the RateLimiter class from bridge servers."""

    def test_rate_limiter_allows_under_limit(self):
        """Test that requests under limit are allowed."""
        from phone_bridge import RateLimiter
        limiter = RateLimiter(max_requests=5, window_seconds=60)

        for i in range(5):
            assert limiter.is_allowed("192.168.1.1") == True

    def test_rate_limiter_blocks_over_limit(self):
        """Test that requests over limit are blocked."""
        from phone_bridge import RateLimiter
        limiter = RateLimiter(max_requests=3, window_seconds=60)

        # Use up all requests
        for i in range(3):
            limiter.is_allowed("192.168.1.1")

        # Next request should be blocked
        assert limiter.is_allowed("192.168.1.1") == False

    def test_rate_limiter_different_ips(self):
        """Test that rate limiting is per-IP."""
        from phone_bridge import RateLimiter
        limiter = RateLimiter(max_requests=1, window_seconds=60)

        limiter.is_allowed("192.168.1.1")
        # Different IP should still be allowed
        assert limiter.is_allowed("192.168.1.2") == True

    def test_rate_limiter_window_expires(self):
        """Test that rate limit window expires."""
        from phone_bridge import RateLimiter
        limiter = RateLimiter(max_requests=2, window_seconds=1)

        # Use up requests
        limiter.is_allowed("192.168.1.1")
        limiter.is_allowed("192.168.1.1")
        assert limiter.is_allowed("192.168.1.1") == False

        # Wait for window to expire
        time.sleep(1.1)
        assert limiter.is_allowed("192.168.1.1") == True


# ═════════════════════════════════════════════════════════════════════════════
#  TASK PARSING HELPER TESTS
# ═════════════════════════════════════════════════════════════════════════════

class TestTaskHelpers:
    """Test helper functions from awesome_chat."""

    @pytest.fixture
    def helpers(self):
        """Import helper functions."""
        from awesome_chat import find_json, field_extract, replace_slot
        return find_json, field_extract, replace_slot

    def test_find_json_in_text(self, helpers):
        """Test extracting JSON from text."""
        find_json, _, _ = helpers
        text = 'Here is the result: {"id": "123", "reason": "test"}'
        result = find_json(text)
        assert '"id": "123"' in result
        assert '"reason": "test"' in result

    def test_field_extract_quoted(self, helpers):
        """Test extracting field from quoted text."""
        _, field_extract, _ = helpers
        text = 'id: "model-123" reason: "best choice"'
        id_val = field_extract(text, "id")
        assert id_val == "model-123"

    def test_replace_slot(self, helpers):
        """Test slot replacement in templates."""
        _, _, replace_slot = helpers
        template = "Hello {{name}}, your task is {{task}}."
        result = replace_slot(template, {"name": "John", "task": "testing"})
        assert result == "Hello John, your task is testing."


# ═════════════════════════════════════════════════════════════════════════════
#  RUN TESTS
# ═════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
