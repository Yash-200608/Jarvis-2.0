#!/data/data/com.termux/files/usr/bin/bash
# Launched by Tasker:Watchdog when phone_executor.py is not running.
# Place under ~/.termux/tasker/start_phone_executor.sh and chmod +x.

set -euo pipefail
cd "$HOME/jarvis-core"

# Keep Termux awake across Doze — harmless if already held.
termux-wake-lock || true

# Re-export the HMAC secret from a private file (never inline here).
if [[ -f "$HOME/.jarvis_env" ]]; then
  # shellcheck disable=SC1091
  source "$HOME/.jarvis_env"
fi

nohup python phone_executor.py --config phone.yaml \
  >> "$HOME/jarvis-core/phone.log" 2>&1 &
echo "started phone_executor.py pid=$!"
