#!/data/data/com.termux/files/usr/bin/bash
# One-time setup: enable WhatsApp auto-send from the Chimera phone node.
# Requires: pkg install android-tools (already done if adb works)
set -euo pipefail
export PATH="$PREFIX/bin:/system/bin:/system/xbin:$PATH"

echo "=== Chimera WhatsApp ADB setup ==="
echo
echo "1) On this phone open: Settings -> Developer options"
echo "2) Enable: USB debugging AND Wireless debugging"
echo "3) In Wireless debugging tap: Pair device with pairing code"
echo "   Note the IP:PORT and the 6-digit pairing code shown there."
echo
read -r -p "Pairing host:port (e.g. 127.0.0.1:37891): " PAIR_ADDR
read -r -p "Pairing code: " PAIR_CODE
adb pair "$PAIR_ADDR" "$PAIR_CODE"
echo
echo "4) Back in Wireless debugging, note the main Debug IP:PORT (not pairing)."
read -r -p "Debug host:port (e.g. 127.0.0.1:5555): " DEBUG_ADDR
adb connect "$DEBUG_ADDR"
echo
adb devices
if adb devices | grep -q '[[:space:]]device$'; then
  echo
  echo "OK — adb is connected. WhatsApp auto-send from JARVIS should work now."
  echo "Tip: re-run 'adb connect $DEBUG_ADDR' after phone reboot if needed."
else
  echo
  echo "No adb device yet — double-check wireless debugging is ON and ports match."
  exit 1
fi
