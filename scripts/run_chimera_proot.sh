#!/data/data/com.termux/files/usr/bin/bash
# Start the Chimera phone node inside proot Ubuntu (native Termux wrapper).
#
# Usage (from Termux):
#   bash ~/Jarvis-2.0/scripts/run_chimera_proot.sh
#
# Keeps Termux:API on PATH so phone.* skills can call termux-battery-status etc.
set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INNER="$REPO_DIR/scripts/run_chimera_proot_inner.sh"

if [ ! -x "$INNER" ]; then
  echo "Missing $INNER — run scripts/termux_proot_setup.sh first." >&2
  exit 1
fi

exec proot-distro login ubuntu -- bash "$INNER"
