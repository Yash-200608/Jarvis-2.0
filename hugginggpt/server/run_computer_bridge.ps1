# JARVIS – Computer Bridge Launcher (Windows PowerShell)
# ──────────────────────────────────────────────────────────────────────────────
# 1. Set a secret token below (any random string).
# 2. Set BRIDGE_DRY_RUN=false when you're ready for live execution.
# 3. Run:  .\run_computer_bridge.ps1
# ──────────────────────────────────────────────────────────────────────────────

$env:BRIDGE_DEVICE   = "computer"
$env:BRIDGE_HOST     = "0.0.0.0"
$env:BRIDGE_PORT     = "8092"
$env:BRIDGE_TOKEN    = "REPLACE_WITH_COMPUTER_BRIDGE_TOKEN"
$env:BRIDGE_DRY_RUN  = "false"    # Change to "true" for testing without real OS changes

# Email credentials (used by send_email / read_emails actions)
# $env:EMAIL_USERNAME = "you@gmail.com"
# $env:EMAIL_PASSWORD = "your-gmail-app-password"

python bridge_server.py --device computer --port 8092
