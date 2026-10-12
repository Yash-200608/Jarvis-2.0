# ╔══════════════════════════════════════════════════════════════════════════╗
# ║  JARVIS launcher                                                         ║
# ║                                                                          ║
# ║  Default (USB): phone_bridge (8091, ADB) + bridge_server (8092),         ║
# ║  then awesome_chat.py --mode cli --voice in the foreground.              ║
# ║                                                                          ║
# ║  -Relay mode: phone_relay_bridge (8091) + bridge_server (8092).          ║
# ║  The phone runs phone_agent.py inside Termux and connects outbound to    ║
# ║  relay_server.py — which can run on this PC (-RelayHost), on a cloud     ║
# ║  VPS, or through a tunnel. Works across any networks.                    ║
# ║                                                                          ║
# ║  Usage:                                                                  ║
# ║    .\run_jarvis.ps1                        # USB mode, voice + keyboard  ║
# ║    .\run_jarvis.ps1 -Relay                 # internet-connected phone    ║
# ║    .\run_jarvis.ps1 -Relay -RelayHost      # also host the relay here    ║
# ║    .\run_jarvis.ps1 -NoVoice               # keyboard only               ║
# ║    .\run_jarvis.ps1 -NoBridges             # don't auto-start bridges    ║
# ║    .\run_jarvis.ps1 -OpenMic               # skip 'Hey Jarvis' wake word ║
# ║                                                                          ║
# ║  Relay config (env or args):                                             ║
# ║    -RelayUrl   wss://host:8093    (RELAY_URL)                            ║
# ║    -RelayToken <shared-secret>    (RELAY_TOKEN)                          ║
# ║    -AgentId    ian-phone          (AGENT_ID)                             ║
# ║                                                                          ║
# ║  Bridge tokens (env only, never hard-coded):                             ║
# ║    JARVIS_PHONE_BRIDGE_TOKEN, JARVIS_COMPUTER_BRIDGE_TOKEN               ║
# ╚══════════════════════════════════════════════════════════════════════════╝

param(
    [switch]$NoVoice,
    [switch]$NoBridges,
    [switch]$OpenMic,
    [switch]$Relay,
    [switch]$RelayHost,
    [string]$Config     = "configs/config.default.yaml",
    [string]$RelayUrl   = $env:RELAY_URL,
    [string]$RelayToken = $env:RELAY_TOKEN,
    [string]$AgentId    = $env:AGENT_ID
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# ── venv ──────────────────────────────────────────────────────────────────────
$venvActivate = Resolve-Path "..\..\jarvis_env\Scripts\Activate.ps1" -ErrorAction SilentlyContinue
if (-not $venvActivate) {
    Write-Host "[launcher] jarvis_env not found. Activate it manually, then rerun." -ForegroundColor Yellow
} else {
    . $venvActivate
    Write-Host "[launcher] jarvis_env activated." -ForegroundColor Green
}

# ── Shared env ────────────────────────────────────────────────────────────────
$env:BRIDGE_DRY_RUN = "false"

# ── Defaults for relay ───────────────────────────────────────────────────────
if ($Relay) {
    if (-not $RelayUrl)   { $RelayUrl   = "ws://localhost:8093" }
    if (-not $RelayToken) { throw "[launcher] -Relay needs -RelayToken or `$env:RELAY_TOKEN (shared secret, never hard-coded)." }
    if (-not $AgentId)    { $AgentId    = "ian-phone" }
}

# ── Bridge tokens (env-only; must match ${...} refs in config.default.yaml) ──
$PhoneToken    = $env:JARVIS_PHONE_BRIDGE_TOKEN
$ComputerToken = $env:JARVIS_COMPUTER_BRIDGE_TOKEN
if (-not $NoBridges -and (-not $PhoneToken -or -not $ComputerToken)) {
    throw "[launcher] Set `$env:JARVIS_PHONE_BRIDGE_TOKEN and `$env:JARVIS_COMPUTER_BRIDGE_TOKEN (or pass -NoBridges)."
}

# ── Generic bridge launcher ──────────────────────────────────────────────────
function Start-Bridge($name, $script, $token, $port) {
    $existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "[launcher] $name already listening on :$port — skipping." -ForegroundColor DarkGray
        return
    }
    Write-Host "[launcher] starting $name on :$port" -ForegroundColor Cyan
    $args = "-NoProfile -Command `"`$env:BRIDGE_TOKEN='$token'; `$env:BRIDGE_DRY_RUN='false'; & '$root\..\..\jarvis_env\Scripts\python.exe' '$root\$script' --port $port`""
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Minimized | Out-Null
}

function Start-RelayServer($token, $port) {
    $existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "[launcher] relay_server already listening on :$port — skipping." -ForegroundColor DarkGray
        return
    }
    Write-Host "[launcher] starting relay_server on :$port" -ForegroundColor Cyan
    $args = "-NoProfile -Command `"`$env:RELAY_TOKEN='$token'; & '$root\..\..\jarvis_env\Scripts\python.exe' '$root\relay_server.py' --port $port`""
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Minimized | Out-Null
}

function Start-PhoneRelayBridge($httpToken, $relayUrl, $relayToken, $agentId, $port) {
    $existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host "[launcher] phone_relay_bridge already listening on :$port — skipping." -ForegroundColor DarkGray
        return
    }
    Write-Host "[launcher] starting phone_relay_bridge on :$port (agent=$agentId, relay=$relayUrl)" -ForegroundColor Cyan
    $cmd = "`$env:PHONE_HTTP_TOKEN='$httpToken'; `$env:RELAY_URL='$relayUrl'; `$env:RELAY_TOKEN='$relayToken'; `$env:AGENT_ID='$agentId'; & '$root\..\..\jarvis_env\Scripts\python.exe' '$root\phone_relay_bridge.py' --port $port"
    $args = "-NoProfile -Command `"$cmd`""
    Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Minimized | Out-Null
}

if (-not $NoBridges) {
    if ($Relay) {
        if ($RelayHost) {
            Start-RelayServer $RelayToken 8093
            Start-Sleep -Seconds 1
        }
        Start-PhoneRelayBridge $PhoneToken $RelayUrl $RelayToken $AgentId 8091
        Start-Bridge "bridge_server"  "bridge_server.py"  $ComputerToken    8092
        Write-Host "[launcher] relay mode: waiting ~4s for phone_agent to connect…" -ForegroundColor DarkGray
        Start-Sleep -Seconds 4
    } else {
        Start-Bridge "phone_bridge"   "phone_bridge.py"   $PhoneToken 8091
        Start-Bridge "bridge_server"  "bridge_server.py"  $ComputerToken    8092
        Write-Host "[launcher] giving bridges ~3s to warm up…" -ForegroundColor DarkGray
        Start-Sleep -Seconds 3
    }
}

# ── Launch JARVIS CLI ─────────────────────────────────────────────────────────
$chatArgs = @("awesome_chat.py", "--config", $Config, "--mode", "cli")
if (-not $NoVoice) { $chatArgs += "--voice" }
if ($OpenMic)      { $chatArgs += "--no-wake-word" }

Write-Host "[launcher] launching JARVIS: python $($chatArgs -join ' ')" -ForegroundColor Green
python @chatArgs
