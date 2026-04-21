# Jarvis-Core Android Layer

This folder contains everything needed to run the Termux side of Jarvis-Core
and keep it alive via a Tasker watchdog.

## One-time setup

1. Install from **F-Droid** (not Google Play — Play Store builds cannot
   cross-communicate):
   - Termux
   - Termux:API
   - Termux:Tasker
   - Tasker

2. In Termux, bootstrap the environment:

   ```bash
   pkg update && pkg upgrade -y
   pkg install python python-pip termux-api git -y
   mkdir -p ~/jarvis-core
   cd ~/jarvis-core

   # Copy phone_executor.py and phone.example.yaml from the PC via scp/syncthing/git.
   cp phone.example.yaml phone.yaml
   # Edit phone.yaml — set mqtt.host to your PC's Tailscale IP (100.x.x.x).

   pip install -r requirements-termux.txt
   ```

3. Create `~/.jarvis_env` (mode 600) containing the HMAC secret shared with
   the PC brain:

   ```bash
   cat > ~/.jarvis_env <<'EOF'
   export JARVIS_HMAC_KEY=<paste 64-char hex from PC .env>
   EOF
   chmod 600 ~/.jarvis_env
   ```

4. Copy the Tasker scripts into place and mark them executable:

   ```bash
   mkdir -p ~/.termux/tasker
   cp start_phone_executor.sh ~/.termux/tasker/
   cp probe_phone_executor.sh ~/.termux/tasker/
   chmod +x ~/.termux/tasker/*.sh
   ```

5. Import `Tasker_Watchdog.xml` in Tasker (Menu → Data → Restore → Profiles)
   and toggle the profile ON. It fires every 10 minutes.

6. Battery: disable battery optimisation for Termux, Termux:API, and Tasker
   (Android Settings → Apps → → Battery → Unrestricted). Otherwise Doze
   kills the MQTT socket.

## What the watchdog does

Every 10 minutes the profile:

1. Acquires `termux-wake-lock` (no-op if already held).
2. Runs `probe_phone_executor.sh` — emits PID(s) on stdout.
3. If stdout is empty, runs `start_phone_executor.sh` to relaunch the agent
   and fires a local notification so you can see crash-loops.

## Health signals

- Liveness: `jarvis/heartbeat/phone` retained topic — updated every 30 s.
- Death: MQTT LWT on the same topic delivers `{"status":"offline"}` when the
  broker notices the socket drop. The PC scheduler picks this up and writes
  `NEED_ATTENTION.md` if the phone is silent for longer than
  `scheduler.phone_offline_after_seconds`.
