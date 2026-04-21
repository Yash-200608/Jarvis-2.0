#!/data/data/com.termux/files/usr/bin/bash
# JARVIS phone agent launcher.
#
# Invoked by:
#   • Termux:Boot (on device boot, via ~/.termux/boot/ symlink)
#   • Tasker watchdog (every 2 min via Termux:Tasker plugin)
#   • Manually: bash start_agent.sh
#
# The wake-lock is what lets us survive Doze + Phantom Process Killer.
# Without it, Android WILL kill this process the moment the screen
# turns off. The Tasker watchdog is the second line of defense.

set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
LOG="$HERE/agent.log"
PIDFILE="$HERE/agent.pid"

# ─── config: EDIT THESE ────────────────────────────────────────────────
export JARVIS_BROKER="${JARVIS_BROKER:-100.64.0.1}"     # your PC's Tailscale IP
export JARVIS_PORT="${JARVIS_PORT:-1883}"
export JARVIS_USER="${JARVIS_USER:-jarvis}"
export JARVIS_PASS="${JARVIS_PASS:-CHANGE_ME}"
export JARVIS_NODE_ID="${JARVIS_NODE_ID:-ian-phone}"
# export JARVIS_CA_CERT="$HOME/jarvis/ca.crt"           # uncomment for TLS
# ───────────────────────────────────────────────────────────────────────

# Bail silently if we're already running — idempotent for Tasker.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    exit 0
fi

# Wake lock: survive Doze. Safe to call repeatedly.
termux-wake-lock || true

# Ensure deps once per install. Cheap no-op on subsequent runs.
if ! python -c "import aiomqtt" 2>/dev/null; then
    pkg install -y python termux-api >> "$LOG" 2>&1
    pip install --upgrade "aiomqtt>=2.0.0" >> "$LOG" 2>&1
fi

# Rotate log if > 1 MB.
if [ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1048576 ]; then
    mv "$LOG" "$LOG.1"
fi

cd "$HERE"
# `exec` replaces the shell so the PID we write IS the Python process.
exec python -u agent_phone.py >> "$LOG" 2>&1 &
echo $! > "$PIDFILE"
disown
