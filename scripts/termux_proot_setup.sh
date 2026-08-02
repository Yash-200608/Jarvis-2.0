#!/data/data/com.termux/files/usr/bin/bash
# One-time proot-distro bootstrap for the Chimera phone node (Termux native).
#
# Run from Termux with the JARVIS repo already at ~/Jarvis-2.0:
#   cd ~/Jarvis-2.0 && bash scripts/termux_proot_setup.sh
#
# Prerequisites (operator): Termux + Termux:API (F-Droid), Tailscale on phone,
# Mosquitto on PC bound to Tailscale IP (see docs/ops/NODE_PROVISIONING.md §5).
set -eu

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "[1/3] Installing proot-distro..."
pkg update -y
pkg install -y proot-distro

echo "[2/3] Installing Ubuntu rootfs (skip if already present)..."
if proot-distro list 2>&1 | grep -qw ubuntu; then
  echo "  ubuntu already installed"
else
  proot-distro install ubuntu || {
  if proot-distro list 2>&1 | grep -qw ubuntu; then
    echo "  ubuntu already installed (install skipped)"
  else
    echo "Failed to install ubuntu rootfs." >&2
    exit 1
  fi
  }
fi

if [ ! -f "$REPO_DIR/run_chimera_node.py" ]; then
  echo "run_chimera_node.py not found in $REPO_DIR" >&2
  echo "Clone or git pull the JARVIS repo on the phone first." >&2
  exit 1
fi

echo "[3/3] Running proot_ubuntu_install.sh inside Ubuntu..."
proot-distro login ubuntu -- bash "$REPO_DIR/scripts/proot_ubuntu_install.sh"

echo
echo "Bootstrap complete. Start the node from Termux:"
echo "  bash $REPO_DIR/scripts/run_chimera_proot.sh"
