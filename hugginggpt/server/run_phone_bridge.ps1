# JARVIS – Phone Bridge Launcher (Windows PowerShell)
# ──────────────────────────────────────────────────────────────────────────────
# Prerequisites:
#   1. ADB installed: https://developer.android.com/tools/releases/platform-tools
#   2. Add ADB to PATH: $env:Path += ";C:\path\to\platform-tools"
#   3. Enable USB Debugging on your Android phone
#   4. Connect phone via USB and accept the "Allow USB Debugging" prompt
#   5. Verify with: adb devices
# ──────────────────────────────────────────────────────────────────────────────

$env:BRIDGE_DEVICE   = "phone"
$env:BRIDGE_HOST     = "0.0.0.0"
$env:BRIDGE_PORT     = "8091"
$env:BRIDGE_TOKEN    = "REPLACE_WITH_PHONE_BRIDGE_TOKEN"
$env:BRIDGE_DRY_RUN  = "false"    # Change to "true" for testing

python phone_bridge.py --port 8091
