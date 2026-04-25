#!/data/data/com.termux/files/usr/bin/sh
# ─────────────────────────────────────────────────────────────────────────────
#  JARVIS phone agent installer — run INSIDE Termux on your Android phone.
#
#  What this does:
#    1. Installs python, termux-api, websockets.
#    2. Prompts for RELAY_URL, RELAY_TOKEN, AGENT_ID.
#    3. Writes a launcher at ~/run_jarvis_agent.sh.
#    4. Optionally wires it into Termux:Boot so the agent starts on reboot.
#
#  Prereqs on the phone:
#    • Install Termux from F-Droid (the Play-Store build is too old):
#        https://f-droid.org/packages/com.termux/
#    • Install "Termux:API" from the same F-Droid source.
#    • (Optional, for auto-start) install "Termux:Boot".
#    • In Android Settings, grant Termux:API the SMS / Contacts / Telephony
#      permissions you want it to use.
#
#  Prereqs on the PC side:
#    • relay_server.py must be reachable from the phone at RELAY_URL
#      (e.g. wss://jarvis.yourdomain.com:8093, ws://<LAN-IP>:8093, or an
#       ngrok/Cloudflared tunnel).
#
#  Then copy phone_agent.py to the phone (scp / cloud / "share to Termux")
#  and place it at ~/phone_agent.py before running this script.
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo ">> updating packages (this may take a minute)"
pkg update -y
pkg upgrade -y

echo ">> installing python + termux-api"
pkg install -y python termux-api

echo ">> pip install websockets"
pip install --upgrade pip
pip install websockets

echo ">> allowing Termux to write to shared storage (so screenshots land in /sdcard)"
termux-setup-storage || true

if [ ! -f "$HOME/phone_agent.py" ]; then
    echo ""
    echo "!! phone_agent.py is NOT at ~/phone_agent.py yet."
    echo "   Copy it over (scp, cloud drive, termux-url-opener, etc.)"
    echo "   then re-run this script. Continuing anyway — the launcher will"
    echo "   point at ~/phone_agent.py once you place it there."
    echo ""
fi

echo ""
printf "Relay URL (e.g. wss://your-host:8093): "
read RELAY_URL
printf "Relay shared token: "
read RELAY_TOKEN
printf "Agent ID (unique name for this phone, e.g. ian-pixel-001): "
read AGENT_ID

LAUNCHER="$HOME/run_jarvis_agent.sh"
cat > "$LAUNCHER" <<EOF
#!/data/data/com.termux/files/usr/bin/sh
# JARVIS phone agent launcher — auto-generated
termux-wake-lock
cd "\$HOME"
exec python phone_agent.py \\
    --relay-url  "$RELAY_URL" \\
    --relay-token "$RELAY_TOKEN" \\
    --agent-id    "$AGENT_ID"
EOF
chmod +x "$LAUNCHER"
echo ">> wrote $LAUNCHER"

printf "\nAuto-start on reboot? (requires Termux:Boot installed) [y/N]: "
read AUTOSTART
case "$AUTOSTART" in
    y|Y|yes|YES)
        mkdir -p "$HOME/.termux/boot"
        cp "$LAUNCHER" "$HOME/.termux/boot/start-jarvis-agent"
        chmod +x "$HOME/.termux/boot/start-jarvis-agent"
        echo ">> installed boot script at ~/.termux/boot/start-jarvis-agent"
        echo "   Make sure Termux:Boot is installed from F-Droid and has"
        echo "   been opened at least once so Android whitelists it."
        ;;
    *)
        echo ">> skipping auto-start"
        ;;
esac

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Install complete."
echo "  Start the agent now with:   ~/run_jarvis_agent.sh"
echo "  Stop it with:               Ctrl+C (or kill the Termux session)"
echo "════════════════════════════════════════════════════════════════════"
