#!/data/data/com.termux/files/usr/bin/bash
# Register the Chimera phone node as a termux-services (runit) service so it
# restarts after a crash and comes back after Termux is relaunched.
#
# Usage (native Termux, repo at ~/Jarvis-2.0, proot already set up):
#   bash ~/Jarvis-2.0/scripts/termux_service_install.sh [--uninstall]
#
# Then:  sv status chimera-node     sv down chimera-node     sv up chimera-node
# Logs:  $PREFIX/var/log/sv/chimera-node/current
#
# Notes:
#  - runsvdir only starts after Termux is restarted once following the
#    termux-services install (close all Termux sessions, reopen).
#  - The service takes a wake lock so Doze does not freeze the node; release
#    it with `termux-wake-unlock` if you stop the service for a long time.
#  - Do not also start run_chimera_proot.sh by hand: two nodes sharing one
#    CHIMERA_NODE_ID fight over the MQTT client id (TD-P4-05 failure mode).
set -eu

NAME="chimera-node"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LAUNCHER="$REPO_DIR/scripts/run_chimera_proot.sh"
SVDIR="${PREFIX:-/data/data/com.termux/files/usr}/var/service/$NAME"

if [ "${1:-}" = "--uninstall" ]; then
  command -v sv-disable >/dev/null 2>&1 && sv-disable "$NAME" 2>/dev/null || true
  rm -rf "$SVDIR"
  echo "Removed service $NAME."
  exit 0
fi

if [ ! -f "$LAUNCHER" ]; then
  echo "Missing $LAUNCHER -- git pull the JARVIS repo on the phone first." >&2
  exit 1
fi
if [ ! -f "$REPO_DIR/scripts/run_chimera_proot_inner.sh" ]; then
  echo "Missing run_chimera_proot_inner.sh -- run scripts/termux_proot_setup.sh first." >&2
  exit 1
fi
if [ ! -f "$HOME/.chimera/node.env" ]; then
  echo "Missing ~/.chimera/node.env -- create it first (docs/ops/NODE_PROVISIONING.md §3)." >&2
  exit 1
fi

echo "[1/3] Installing termux-services..."
pkg install -y termux-services

echo "[2/3] Writing service definition at $SVDIR ..."
mkdir -p "$SVDIR/log"
cat >"$SVDIR/run" <<RUN
#!/data/data/com.termux/files/usr/bin/sh
exec 2>&1
termux-wake-lock 2>/dev/null || true
exec bash "$LAUNCHER"
RUN
# svlogger ships with termux-services; rotates logs under \$PREFIX/var/log/sv/
ln -sf "${PREFIX:-/data/data/com.termux/files/usr}/share/termux-services/svlogger" "$SVDIR/log/run"
chmod +x "$SVDIR/run"

echo "[3/3] Enabling..."
# sv-enable needs runsvdir; on first install it only becomes active after a
# Termux restart, so tolerate a failure here.
sv-enable "$NAME" || echo "  (sv-enable deferred: restart Termux once, then run: sv-enable $NAME)"

echo
echo "Done. After restarting Termux:  sv status $NAME"
