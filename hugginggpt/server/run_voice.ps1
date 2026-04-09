# JARVIS – Voice Module Launcher (Windows PowerShell)
# ──────────────────────────────────────────────────────────────────────────────
# Prerequisites:
#   pip install SpeechRecognition pyaudio pyttsx3
#   (pyaudio on Windows sometimes needs: pip install pipwin && pipwin install pyaudio)
# ──────────────────────────────────────────────────────────────────────────────

$env:JARVIS_SERVER       = "http://localhost:8004"  # awesome_chat.py address
$env:ANTHROPIC_API_KEY   = "REPLACE_WITH_YOUR_ANTHROPIC_API_KEY_HERE"
$env:JARVIS_API_TYPE     = "claude"

# Optional: Use ElevenLabs for a more natural voice
# $env:ELEVENLABS_API_KEY = "REPLACE_WITH_ELEVENLABS_KEY"

python voice_module.py `
  --server $env:JARVIS_SERVER `
  --stt    google `
  --tts    auto `
  --api-type claude
