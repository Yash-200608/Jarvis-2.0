#!/data/data/com.termux/files/usr/bin/bash
# Chimera phone-node installer for Termux (Priority #4 Milestone 9).
# Run INSIDE Termux on the phone, from a copy of the JARVIS repo (or with
# jarvis_node_sdk/ + run_chimera_node.py copied alongside this script).
#
# Prerequisites the operator does by hand first (see
# docs/ops/NODE_PROVISIONING.md §4): Termux + Termux:API from F-Droid,
# Tailscale app logged into the tailnet, battery optimization disabled
# for Termux.
set -eu

echo "[1/4] Installing packages (python, termux-api bridge)..."
pkg update -y
pkg install -y python termux-api

echo "[2/4] Installing Python dependencies..."
pip install --upgrade pip >/dev/null
pip install aiomqtt paho-mqtt pydantic loguru python-dotenv pyyaml

echo "[3/4] Locating node code..."
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [ ! -f "$REPO_DIR/run_chimera_node.py" ]; then
    echo "run_chimera_node.py not found next to scripts/ -- copy the JARVIS repo (or at least"
    echo "jarvis_node_sdk/ + run_chimera_node.py) onto the phone first." >&2
    exit 1
fi

echo "[4/4] Writing ~/.chimera/node.env ..."
mkdir -p "$HOME/.chimera"
printf "Node id [nothing-phone-3a]: "
read -r NODE_ID
NODE_ID="${NODE_ID:-nothing-phone-3a}"
printf "Spine HMAC key (from Brain/.env JARVIS_MQTT_HMAC_KEY): "
read -r HMAC_KEY
printf "Broker host (the PC's Tailscale IP): "
read -r BROKER_HOST

cat > "$HOME/.chimera/node.env" <<EOF
CHIMERA_NODE_ID=$NODE_ID
CHIMERA_HMAC_KEY=$HMAC_KEY
CHIMERA_BROKER_HOST=$BROKER_HOST
CHIMERA_BROKER_PORT=1883
EOF
chmod 600 "$HOME/.chimera/node.env"

echo
echo "Done. Start the node with:"
echo "  cd $REPO_DIR && python run_chimera_node.py --env-file \$HOME/.chimera/node.env"
echo
echo "For auto-restart: pkg install termux-services, then create"
echo "  ~/.config/sv/chimera-node/run  execing that command, and sv-enable chimera-node."
