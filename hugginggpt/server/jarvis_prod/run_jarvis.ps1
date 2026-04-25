# JARVIS one-shot launcher (PC side).
#
# Starts:
#   1. Mosquitto broker (if not already running)
#   2. The PC brain (brain_pc.py) with the right env
#
# Usage:
#   .\run_jarvis.ps1                       # defaults: localhost broker, REPL
#   .\run_jarvis.ps1 -BrokerIp 100.x.x.x   # Tailscale-bound, for remote phone
#   .\run_jarvis.ps1 -NoRepl               # daemon-style, no interactive shell
#
# Prereqs (all installed via winget already):
#   • Python 3.12 at C:\Users\Hp\AppData\Local\Programs\Python\Python312\python.exe
#   • Mosquitto 2.1.2 at C:\Program Files\mosquitto\
#   • Tailscale 1.96.3 at C:\Program Files\Tailscale\  (login separately)
#
# Env overrides:
#   $env:JARVIS_TG_TOKEN    = "bot_token_from_botfather"
#   $env:JARVIS_TG_ADMIN    = "your_telegram_chat_id"

[CmdletBinding()]
param(
    [string]$BrokerIp  = "127.0.0.1",
    [int]   $BrokerPort = 1883,
    [string]$User      = "jarvis",
    # Mosquitto password — pass via -Pass or $env:JARVIS_PASS. Never hard-code.
    [string]$Pass      = $env:JARVIS_PASS,
    [switch]$NoRepl
)

if (-not $Pass) {
    throw "Broker password required. Pass -Pass '<pw>' or set `$env:JARVIS_PASS."
}

$ErrorActionPreference = "Stop"
$Python = "C:\Users\Hp\AppData\Local\Programs\Python\Python312\python.exe"
$Mosq   = "C:\Program Files\mosquitto\mosquitto.exe"
$Conf   = "C:\ProgramData\mosquitto\mosquitto.conf"
$BrainDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# ── 1. start mosquitto if it's not already bound to :1883 ─────────────
$existing = Get-NetTCPConnection -LocalPort $BrokerPort -State Listen -ErrorAction SilentlyContinue
if (-not $existing) {
    Write-Host "starting mosquitto broker ..." -ForegroundColor Cyan
    Start-Process -FilePath $Mosq -ArgumentList "-c", $Conf `
        -WindowStyle Minimized -WorkingDirectory "C:\Program Files\mosquitto"
    Start-Sleep -Seconds 2
} else {
    Write-Host "mosquitto already listening on :$BrokerPort" -ForegroundColor Green
}

# ── 2. launch the brain ───────────────────────────────────────────────
Set-Location $BrainDir

$env:JARVIS_BROKER   = $BrokerIp
$env:JARVIS_PORT     = "$BrokerPort"
$env:JARVIS_USER     = $User
$env:JARVIS_PASS     = $Pass
$env:JARVIS_BRAIN_ID = "ian-pc"
$env:JARVIS_PHONE_ID = "ian-phone"

$brainArgs = @("brain_pc.py")
if ($NoRepl) { $brainArgs += "--no-repl" }

Write-Host ""
Write-Host "JARVIS brain starting (broker=${BrokerIp}:${BrokerPort})" -ForegroundColor Cyan
Write-Host "  Telegram gateway: $(if ($env:JARVIS_TG_TOKEN) { 'enabled' } else { 'DISABLED (set `$env:JARVIS_TG_TOKEN` to enable)' })" -ForegroundColor Gray
Write-Host "  Scheduler:        $(if (Test-Path "$BrainDir\schedule.json") { 'schedule.json found' } else { 'no schedule.json (copy schedule.example.json to enable)' })" -ForegroundColor Gray
Write-Host ""

& $Python @brainArgs
