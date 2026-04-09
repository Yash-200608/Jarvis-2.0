"""
JARVIS Voice Module
===================
Adds always-on voice control to JARVIS.

Pipeline:
  Microphone → Wake Word ("Hey Jarvis") → Speech Recognition → awesome_chat.py → TTS Response

Quick start:
  pip install SpeechRecognition pyaudio pyttsx3
  python voice_module.py --server http://localhost:8004

Optional (better quality):
  pip install openai-whisper          # local Whisper STT
  pip install elevenlabs              # ElevenLabs TTS (set ELEVENLABS_API_KEY)

Controls:
  Ctrl-C  – quit
  Say "Hey Jarvis, stop listening"  – disable wake word detection until restart
"""

import argparse
import json
import os
import queue
import sys
import threading
import time
import wave

import requests

# ── STT back-ends ─────────────────────────────────────────────────────────────
try:
    import speech_recognition as sr
    HAS_SR = True
except ImportError:
    HAS_SR = False

try:
    import whisper as openai_whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

# ── TTS back-ends ─────────────────────────────────────────────────────────────
try:
    import pyttsx3
    HAS_PYTTSX3 = True
except ImportError:
    HAS_PYTTSX3 = False

try:
    from elevenlabs import generate, play, set_api_key
    HAS_ELEVENLABS = True
except ImportError:
    HAS_ELEVENLABS = False

# ─────────────────────────────────────────────────────────────────────────────

WAKE_WORDS    = ["hey jarvis", "jarvis", "okay jarvis"]
STOP_PHRASES  = ["stop listening", "go to sleep", "jarvis stop"]

# ANSI colours for terminal output
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


# ─────────────────────────────────────────────────────────────────────────────
# TTS engine
# ─────────────────────────────────────────────────────────────────────────────

class TTSEngine:
    """Abstracts pyttsx3 / ElevenLabs / print-only fallback."""

    def __init__(self, backend="auto", elevenlabs_voice="Rachel", rate=175):
        self.backend = backend
        self._engine = None
        self._lock   = threading.Lock()

        if backend == "auto":
            if HAS_ELEVENLABS and os.getenv("ELEVENLABS_API_KEY"):
                self.backend = "elevenlabs"
            elif HAS_PYTTSX3:
                self.backend = "pyttsx3"
            else:
                self.backend = "print"

        if self.backend == "pyttsx3":
            if not HAS_PYTTSX3:
                print(f"{YELLOW}[voice] pyttsx3 not found, using print fallback{RESET}")
                self.backend = "print"
            else:
                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", rate)
                # Try to pick a more natural-sounding voice
                voices = self._engine.getProperty("voices")
                for v in voices:
                    if "zira" in v.name.lower() or "david" in v.name.lower():
                        self._engine.setProperty("voice", v.id)
                        break

        if self.backend == "elevenlabs":
            if not HAS_ELEVENLABS:
                self.backend = "pyttsx3" if HAS_PYTTSX3 else "print"
            else:
                api_key = os.getenv("ELEVENLABS_API_KEY", "")
                if api_key:
                    set_api_key(api_key)
                self.elevenlabs_voice = elevenlabs_voice

    def speak(self, text):
        print(f"{CYAN}{BOLD}[JARVIS]{RESET} {text}")
        if not text.strip():
            return
        with self._lock:
            try:
                if self.backend == "pyttsx3" and self._engine:
                    self._engine.say(text)
                    self._engine.runAndWait()
                elif self.backend == "elevenlabs":
                    audio = generate(text=text, voice=self.elevenlabs_voice,
                                     model="eleven_monolingual_v1")
                    play(audio)
                # "print" backend already printed above
            except Exception as e:
                print(f"{RED}[voice] TTS error: {e}{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# STT engine
# ─────────────────────────────────────────────────────────────────────────────

class STTEngine:
    """
    Speech-to-text engine.
    backend: "google" (requires internet), "whisper" (local, slower first run)
    """

    def __init__(self, backend="google", whisper_model="base"):
        self.backend = backend
        self._recognizer = None
        self._whisper    = None
        self._mic        = None

        if not HAS_SR:
            raise ImportError(
                "SpeechRecognition is required. Run: pip install SpeechRecognition pyaudio"
            )

        self._recognizer = sr.Recognizer()
        self._recognizer.pause_threshold = 0.8   # seconds of silence before phrase is complete
        self._recognizer.energy_threshold = 300  # adjust for ambient noise level
        self._recognizer.dynamic_energy_threshold = True

        if backend == "whisper":
            if not HAS_WHISPER:
                print(f"{YELLOW}[voice] openai-whisper not found, falling back to Google STT{RESET}")
                self.backend = "google"
            else:
                print(f"[voice] Loading Whisper '{whisper_model}' model (first load may be slow)…")
                self._whisper = openai_whisper.load_model(whisper_model)
                print(f"[voice] Whisper model loaded ✓")

    def calibrate(self, duration=1.0):
        """Calibrate to ambient noise."""
        try:
            with sr.Microphone() as src:
                print(f"[voice] Calibrating microphone for {duration}s…")
                self._recognizer.adjust_for_ambient_noise(src, duration=duration)
                print(f"[voice] Calibration done (energy_threshold={self._recognizer.energy_threshold:.0f})")
        except Exception as e:
            print(f"{YELLOW}[voice] Calibration skipped: {e}{RESET}")

    def listen_once(self, timeout=5, phrase_limit=15):
        """
        Block until a phrase is captured or timeout.
        Returns (text, raw_audio_data) or (None, None) on failure.
        """
        try:
            with sr.Microphone() as src:
                audio = self._recognizer.listen(src, timeout=timeout,
                                                phrase_time_limit=phrase_limit)
            return self._transcribe(audio), audio
        except sr.WaitTimeoutError:
            return None, None
        except Exception as e:
            print(f"{RED}[voice] Listen error: {e}{RESET}")
            return None, None

    def _transcribe(self, audio):
        try:
            if self.backend == "whisper" and self._whisper:
                import numpy as np
                raw  = audio.get_raw_data(convert_rate=16000, convert_width=2)
                arr  = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                result = self._whisper.transcribe(arr, fp16=False)
                return result["text"].strip()
            else:
                return self._recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            print(f"{RED}[voice] STT API error: {e}{RESET}")
            return None


# ─────────────────────────────────────────────────────────────────────────────
# JARVIS Voice Interface
# ─────────────────────────────────────────────────────────────────────────────

class JarvisVoice:
    def __init__(self, server_url, stt_backend="google", tts_backend="auto",
                 whisper_model="base", elevenlabs_voice="Rachel",
                 conversation_id=None):
        self.server_url      = server_url.rstrip("/")
        self.conversation_id = conversation_id or f"voice-{int(time.time())}"
        self.listening       = True
        self.awake           = False  # True when wake word was just heard

        self.tts = TTSEngine(backend=tts_backend, elevenlabs_voice=elevenlabs_voice)
        self.stt = STTEngine(backend=stt_backend, whisper_model=whisper_model)

        # Response queue (TTS runs on separate thread to not block listening)
        self._tts_queue = queue.Queue()
        self._tts_thread = threading.Thread(target=self._tts_worker, daemon=True)
        self._tts_thread.start()

    def _tts_worker(self):
        while True:
            text = self._tts_queue.get()
            if text is None:
                break
            self.tts.speak(text)
            self._tts_queue.task_done()

    def _say(self, text):
        self._tts_queue.put(text)

    def _is_wake_word(self, text):
        if not text:
            return False, text
        lower = text.lower().strip()
        for ww in WAKE_WORDS:
            if lower.startswith(ww):
                command = text[len(ww):].strip(" ,!")
                return True, command
        return False, text

    def _is_stop_phrase(self, text):
        lower = (text or "").lower()
        return any(sp in lower for sp in STOP_PHRASES)

    def _send_to_jarvis(self, text, api_key=None, api_type=None, api_endpoint=None):
        """Send a text command to the awesome_chat.py server and get a response."""
        payload = {
            "messages": [{"role": "user", "content": text}],
            "conversation_id": self.conversation_id,
        }
        if api_key:
            payload["api_key"]      = api_key
        if api_type:
            payload["api_type"]     = api_type
        if api_endpoint:
            payload["api_endpoint"] = api_endpoint

        try:
            r = requests.post(
                f"{self.server_url}/hugginggpt",
                json=payload,
                timeout=60,
            )
            if r.status_code == 200:
                data = r.json()
                return data.get("message", str(data))
            else:
                return f"Server error {r.status_code}: {r.text[:200]}"
        except requests.Timeout:
            return "I'm sorry, the request timed out. Please try again."
        except requests.ConnectionError:
            return (f"Cannot reach JARVIS server at {self.server_url}. "
                    "Make sure awesome_chat.py is running.")
        except Exception as e:
            return f"Unexpected error: {e}"

    def run(self, api_key=None, api_type=None, api_endpoint=None,
            continuous=True, calibrate=True):
        """
        Main voice loop.
        continuous=True: always listen, wait for wake word each time.
        continuous=False: listen once, return text response.
        """
        print(f"\n{GREEN}{BOLD}══════════════════════════════════{RESET}")
        print(f"{GREEN}{BOLD}   JARVIS Voice Interface Online   {RESET}")
        print(f"{GREEN}{BOLD}══════════════════════════════════{RESET}")
        print(f"  Wake word : {CYAN}{', '.join(WAKE_WORDS)}{RESET}")
        print(f"  Server    : {CYAN}{self.server_url}{RESET}")
        print(f"  STT       : {CYAN}{self.stt.backend}{RESET}")
        print(f"  TTS       : {CYAN}{self.tts.backend}{RESET}")
        print(f"  Press {BOLD}Ctrl-C{RESET} to stop.\n")

        if calibrate:
            self.stt.calibrate(duration=1.5)

        self._say("JARVIS online. Say 'Hey Jarvis' followed by your command.")

        while self.listening:
            try:
                print(f"{YELLOW}[voice] Listening…{RESET}", end="\r")
                text, _ = self.stt.listen_once(timeout=10, phrase_limit=20)

                if not text:
                    continue

                print(f"{GREEN}[heard] {text}{RESET}          ")

                # Check for stop phrase
                if self._is_stop_phrase(text):
                    self._say("Going to sleep. Goodbye.")
                    self.listening = False
                    break

                # Check for wake word
                is_wake, command = self._is_wake_word(text)

                if not is_wake and not self.awake:
                    # Waiting for wake word
                    continue

                self.awake = False

                if not command.strip():
                    # Wake word only, no command — wait for next phrase
                    self._say("Yes, I'm listening.")
                    self.awake = True
                    text2, _ = self.stt.listen_once(timeout=8, phrase_limit=20)
                    if text2:
                        command = text2
                    else:
                        self._say("I didn't catch that. Please try again.")
                        continue

                print(f"{CYAN}[command] {command}{RESET}")
                self._say("Processing…")

                response = self._send_to_jarvis(command, api_key, api_type, api_endpoint)
                self._say(response)

                if not continuous:
                    break

            except KeyboardInterrupt:
                print(f"\n{YELLOW}[voice] Interrupted — shutting down.{RESET}")
                self.listening = False
                break
            except Exception as e:
                print(f"{RED}[voice] Error: {e}{RESET}")

        self._tts_queue.put(None)   # signal TTS thread to exit
        print(f"{GREEN}[voice] Voice module stopped.{RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="JARVIS Voice Module")
    parser.add_argument("--server",   default=os.getenv("JARVIS_SERVER", "http://localhost:8004"),
                        help="URL of the awesome_chat.py server")
    parser.add_argument("--stt",      default=os.getenv("JARVIS_STT",    "google"),
                        choices=["google", "whisper"],
                        help="Speech recognition backend")
    parser.add_argument("--tts",      default=os.getenv("JARVIS_TTS",    "auto"),
                        choices=["auto", "pyttsx3", "elevenlabs", "print"],
                        help="Text-to-speech backend")
    parser.add_argument("--whisper-model", default="base",
                        choices=["tiny", "base", "small", "medium", "large"],
                        help="Whisper model size (only used when --stt=whisper)")
    parser.add_argument("--elevenlabs-voice", default="Rachel",
                        help="ElevenLabs voice name (only used when --tts=elevenlabs)")
    parser.add_argument("--api-key",      default=os.getenv("OPENAI_API_KEY", ""),
                        help="API key to pass to the JARVIS server")
    parser.add_argument("--api-type",     default=os.getenv("JARVIS_API_TYPE", "openai"),
                        choices=["openai", "azure", "claude"],
                        help="LLM API type")
    parser.add_argument("--api-endpoint", default=os.getenv("JARVIS_API_ENDPOINT", ""),
                        help="Custom API endpoint (optional)")
    parser.add_argument("--no-calibrate", action="store_true",
                        help="Skip microphone calibration")
    args = parser.parse_args()

    if not HAS_SR:
        print(f"{RED}ERROR: SpeechRecognition not installed.{RESET}")
        print("Run: pip install SpeechRecognition pyaudio")
        sys.exit(1)

    voice = JarvisVoice(
        server_url       = args.server,
        stt_backend      = args.stt,
        tts_backend      = args.tts,
        whisper_model    = args.whisper_model,
        elevenlabs_voice = args.elevenlabs_voice,
    )
    voice.run(
        api_key      = args.api_key      or None,
        api_type     = args.api_type     or None,
        api_endpoint = args.api_endpoint or None,
        calibrate    = not args.no_calibrate,
    )


if __name__ == "__main__":
    main()
