"""
JARVIS Voice I/O Primitives
===========================
Reusable pieces for voice INPUT (mic → STT → wake-word gated utterances) and
voice OUTPUT (TTS). Used by awesome_chat.py to let Ian type OR speak to JARVIS
interchangeably in the same CLI session.

Design:
  VoiceInput  — background thread, puts utterances on a queue.Queue.
                Either in "wake-word" mode (utterances must start with a wake
                word) or "open" mode (every utterance is forwarded).
  Speaker     — TTS queue + worker thread. .speak(text) is non-blocking;
                .wait_until_done() blocks until the queue drains.

Both are best-effort: if an STT/TTS dep is missing, they log once and fall
back silently rather than crashing the CLI.

Dependencies (install only what you plan to use):
  pip install SpeechRecognition pyaudio pyttsx3
  pip install openai-whisper                 # optional, local Whisper STT
  pip install elevenlabs                     # optional, ElevenLabs TTS
"""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Callable, Iterable, Optional

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

# ElevenLabs: try v2+ client first, then legacy v0.x API as fallback
HAS_ELEVENLABS = False
_ELEVENLABS_API = None      # "v2_client" | "legacy" | None
try:
    from elevenlabs.client import ElevenLabs as _ElevenLabsClient
    _ELEVENLABS_API = "v2_client"
    HAS_ELEVENLABS = True
except ImportError:
    try:
        from elevenlabs import generate as _ev_generate  # type: ignore
        from elevenlabs import play as _ev_play          # type: ignore
        from elevenlabs import set_api_key as _ev_set_key  # type: ignore
        _ELEVENLABS_API = "legacy"
        HAS_ELEVENLABS = True
    except ImportError:
        pass

# Known voice-name → voice-id mappings (v2 API requires voice_id).
# User can also pass a raw voice_id in config and it's used as-is.
ELEVENLABS_VOICE_IDS = {
    "rachel":   "21m00Tcm4TlvDq8ikWAM",
    "adam":     "pNInz6obpgDQGcFmaJgB",
    "antoni":   "ErXwobaYiN019PkySvjV",
    "arnold":   "VR6AewLTigWG4xSOukaG",
    "bella":    "EXAVITQu4vr4xnSDxMaL",
    "domi":     "AZnzlk1XvdvUeBnXmlld",
    "elli":     "MF3mGyEYCl7XYWbV9V6O",
    "josh":     "TxGEqnHWrfWFTfGW9XjX",
    "sam":      "yoZ06aMxZJJ28mfd3POQ",
    "charlie":  "IKne3meq5aSn9XLyUdCD",
    "clyde":    "2EiwWnXFnvU5JabPnv8n",
}

# ── Terminal colours ──────────────────────────────────────────────────────────
GREEN  = "\033[92m"
CYAN   = "\033[96m"
YELLOW = "\033[93m"
RED    = "\033[91m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

DEFAULT_WAKE_WORDS = ("hey jarvis", "jarvis", "okay jarvis", "ok jarvis")
DEFAULT_STOP_PHRASES = ("stop listening", "jarvis sleep", "sleep jarvis", "jarvis stop")


# ═══════════════════════════════════════════════════════════════════════════════
#  Speaker (TTS)
# ═══════════════════════════════════════════════════════════════════════════════

class Speaker:
    """
    Non-blocking TTS. .speak(text) enqueues; a worker thread plays audio.
    Safe to call from the main loop without stalling input.
    """

    def __init__(self, backend: str = "auto",
                 elevenlabs_voice: str = "Rachel",
                 elevenlabs_model: str = "eleven_monolingual_v1",
                 rate: int = 180,
                 verbose: bool = True):
        self.verbose = verbose
        self.backend = self._pick_backend(backend)
        self._engine = None
        self._elevenlabs_voice = elevenlabs_voice
        self._elevenlabs_model = elevenlabs_model
        self._el_client = None     # v2 client instance
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._stopped = False

        if self.backend == "pyttsx3":
            self._engine = pyttsx3.init()
            self._engine.setProperty("rate", rate)
            # Prefer a clear Windows voice (Zira/David) if present
            for v in self._engine.getProperty("voices") or []:
                name = (v.name or "").lower()
                if "zira" in name or "david" in name:
                    self._engine.setProperty("voice", v.id)
                    break
        elif self.backend == "elevenlabs":
            key = os.getenv("ELEVENLABS_API_KEY", "")
            if _ELEVENLABS_API == "v2_client":
                try:
                    self._el_client = _ElevenLabsClient(api_key=key) if key else _ElevenLabsClient()
                except Exception as e:
                    if self.verbose:
                        print(f"{RED}[voice-out] ElevenLabs client init failed: {e}{RESET}")
                    self.backend = "pyttsx3" if HAS_PYTTSX3 else "print"
            elif _ELEVENLABS_API == "legacy" and key:
                _ev_set_key(key)

        self._thread = threading.Thread(target=self._worker, daemon=True,
                                        name="JarvisSpeaker")
        self._thread.start()

        if self.verbose:
            print(f"{GREEN}[voice-out]{RESET} TTS backend: {self.backend}")

    @staticmethod
    def _pick_backend(requested: str) -> str:
        if requested == "auto":
            if HAS_ELEVENLABS and os.getenv("ELEVENLABS_API_KEY"):
                return "elevenlabs"
            if HAS_PYTTSX3:
                return "pyttsx3"
            return "print"
        if requested == "elevenlabs" and not (HAS_ELEVENLABS and os.getenv("ELEVENLABS_API_KEY")):
            return "pyttsx3" if HAS_PYTTSX3 else "print"
        if requested == "pyttsx3" and not HAS_PYTTSX3:
            return "print"
        return requested

    def speak(self, text: str) -> None:
        if self._stopped or not text or not text.strip():
            return
        self._queue.put(text)

    def wait_until_done(self, timeout: Optional[float] = None) -> None:
        """Block until the queue is empty. Useful right before exit."""
        end = None if timeout is None else time.time() + timeout
        while not self._queue.empty():
            if end and time.time() > end:
                break
            time.sleep(0.05)

    def stop(self) -> None:
        self._stopped = True
        self._queue.put(None)

    def _resolve_voice_id(self, voice: str) -> str:
        """Map 'Rachel' → '21m00Tcm4TlvDq8ikWAM'. Pass through raw IDs."""
        v = (voice or "").strip()
        # ElevenLabs voice IDs are 20-char alphanumeric. Treat longer/mixed as raw ID.
        if len(v) >= 18 and v.isalnum():
            return v
        return ELEVENLABS_VOICE_IDS.get(v.lower(), v)

    def _speak_elevenlabs_v2(self, text: str) -> None:
        """Generate with ElevenLabs v2 client, play as WAV via winsound (Windows)."""
        voice_id = self._resolve_voice_id(self._elevenlabs_voice)
        audio_iter = self._el_client.text_to_speech.convert(
            voice_id       = voice_id,
            text           = text,
            model_id       = self._elevenlabs_model,
            output_format  = "pcm_22050",
        )
        pcm_bytes = b"".join(audio_iter)
        if not pcm_bytes:
            return

        # Wrap PCM in a WAV container so winsound can play it.
        import io as _io, wave as _wave
        wav_buf = _io.BytesIO()
        with _wave.open(wav_buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)       # 16-bit
            wf.setframerate(22050)
            wf.writeframes(pcm_bytes)

        try:
            import winsound
            winsound.PlaySound(wav_buf.getvalue(), winsound.SND_MEMORY)
            return
        except ImportError:
            pass

        # Non-Windows fallback: write to a temp .wav and play with default player
        import tempfile, subprocess
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(wav_buf.getvalue())
            path = f.name
        for player in ("ffplay", "afplay", "aplay"):
            try:
                subprocess.run([player, "-nodisp", "-autoexit", path]
                               if player == "ffplay" else [player, path],
                               check=True, capture_output=True, timeout=60)
                return
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue

    def _worker(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                break
            try:
                if self.backend == "pyttsx3" and self._engine:
                    self._engine.say(text)
                    self._engine.runAndWait()
                elif self.backend == "elevenlabs":
                    if _ELEVENLABS_API == "v2_client" and self._el_client:
                        self._speak_elevenlabs_v2(text)
                    elif _ELEVENLABS_API == "legacy":
                        audio = _ev_generate(text=text, voice=self._elevenlabs_voice,
                                             model="eleven_monolingual_v1")
                        _ev_play(audio)
                # "print" backend: no-op (caller already printed text)
            except Exception as e:
                # TTS failures are non-fatal — just note it.
                if self.verbose:
                    print(f"{RED}[voice-out] TTS error: {e}{RESET}")
            finally:
                self._queue.task_done()


# ═══════════════════════════════════════════════════════════════════════════════
#  VoiceInput (STT + wake word)
# ═══════════════════════════════════════════════════════════════════════════════

class VoiceInput:
    """
    Listens on the default microphone, transcribes speech, and — if a wake
    word is required — only forwards utterances that begin with one. Each
    forwarded utterance is pushed to `output_queue` and also passed to
    `on_utterance(text)` if provided.

    Runs on a background daemon thread. Call .start() once and let it run.
    """

    def __init__(self,
                 output_queue: "queue.Queue[str]",
                 *,
                 wake_words: Iterable[str] = DEFAULT_WAKE_WORDS,
                 stop_phrases: Iterable[str] = DEFAULT_STOP_PHRASES,
                 stt_backend: str = "google",
                 whisper_model: str = "base",
                 require_wake_word: bool = True,
                 on_wake: Optional[Callable[[], None]] = None,
                 on_utterance: Optional[Callable[[str], None]] = None,
                 on_status: Optional[Callable[[str], None]] = None,
                 energy_threshold: int = 300,
                 pause_threshold: float = 0.8,
                 phrase_limit: int = 20,
                 listen_timeout: int = 10,
                 calibrate_seconds: float = 1.2,
                 verbose: bool = True):
        if not HAS_SR:
            raise RuntimeError(
                "SpeechRecognition is not installed. Run: "
                "pip install SpeechRecognition pyaudio"
            )

        self.output_queue      = output_queue
        self.wake_words        = tuple(w.lower().strip() for w in wake_words)
        self.stop_phrases      = tuple(s.lower().strip() for s in stop_phrases)
        self.require_wake_word = require_wake_word
        self.on_wake           = on_wake
        self.on_utterance      = on_utterance
        self.on_status         = on_status
        self.phrase_limit      = phrase_limit
        self.listen_timeout    = listen_timeout
        self.calibrate_seconds = calibrate_seconds
        self.verbose           = verbose

        self._stop_event = threading.Event()
        self._paused     = threading.Event()   # when set, temporarily skip listening
        self._thread     = None

        self._recognizer = sr.Recognizer()
        self._recognizer.pause_threshold           = pause_threshold
        self._recognizer.energy_threshold          = energy_threshold
        self._recognizer.dynamic_energy_threshold  = True

        # STT back-end setup
        self._stt_backend = stt_backend
        self._whisper     = None
        if stt_backend == "whisper":
            if not HAS_WHISPER:
                self._note(f"{YELLOW}openai-whisper not installed, falling back to Google STT{RESET}")
                self._stt_backend = "google"
            else:
                self._note(f"Loading Whisper '{whisper_model}' model…")
                self._whisper = openai_whisper.load_model(whisper_model)
                self._note("Whisper model loaded ✓")

    # ── public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background listener thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="JarvisVoiceInput")
        self._thread.start()

    def stop(self) -> None:
        """Signal the listener to shut down. Does not block."""
        self._stop_event.set()

    def pause(self) -> None:
        """Temporarily stop forwarding utterances (mic stays warm)."""
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── internals ─────────────────────────────────────────────────────────────

    def _note(self, msg: str) -> None:
        if self.verbose:
            print(f"{CYAN}[voice-in]{RESET} {msg}")
        if self.on_status:
            self.on_status(msg)

    def _calibrate(self) -> None:
        try:
            with sr.Microphone() as src:
                self._note(f"Calibrating microphone for {self.calibrate_seconds}s…")
                self._recognizer.adjust_for_ambient_noise(src, duration=self.calibrate_seconds)
                self._note(f"Calibration done (energy={self._recognizer.energy_threshold:.0f})")
        except Exception as e:
            self._note(f"{YELLOW}Calibration skipped: {e}{RESET}")

    def _listen_phrase(self, *, timeout: int, phrase_limit: int):
        try:
            with sr.Microphone() as src:
                audio = self._recognizer.listen(
                    src, timeout=timeout, phrase_time_limit=phrase_limit,
                )
            return self._transcribe(audio)
        except sr.WaitTimeoutError:
            return None
        except OSError as e:
            # Microphone not available on this box
            self._note(f"{RED}Microphone error: {e}{RESET}")
            time.sleep(2)
            return None
        except Exception as e:
            self._note(f"{RED}Listen error: {e}{RESET}")
            return None

    def _transcribe(self, audio) -> Optional[str]:
        try:
            if self._stt_backend == "whisper" and self._whisper is not None:
                import numpy as np
                raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
                arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                result = self._whisper.transcribe(arr, fp16=False)
                return (result.get("text") or "").strip() or None
            # Google STT (requires internet)
            return self._recognizer.recognize_google(audio)
        except sr.UnknownValueError:
            return None
        except sr.RequestError as e:
            self._note(f"{RED}STT API error: {e}{RESET}")
            return None

    def _strip_wake_word(self, text: str):
        """Return (is_wake, remainder)."""
        if not text:
            return False, ""
        lower = text.lower().strip()
        for ww in self.wake_words:
            if lower == ww:
                return True, ""
            # Allow punctuation right after the wake word ("jarvis, …")
            if lower.startswith(ww + " ") or lower.startswith(ww + ","):
                return True, text[len(ww):].lstrip(" ,!.?").strip()
        return False, text

    def _is_stop(self, text: str) -> bool:
        lower = (text or "").lower()
        return any(sp in lower for sp in self.stop_phrases)

    def _run(self) -> None:
        self._calibrate()
        self._note(f"Listening. Wake words: {', '.join(self.wake_words)}"
                   if self.require_wake_word else "Listening (open mode).")

        while not self._stop_event.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue

            text = self._listen_phrase(timeout=self.listen_timeout,
                                       phrase_limit=self.phrase_limit)
            if self._stop_event.is_set():
                break
            if not text:
                continue

            if self.verbose:
                print(f"{GREEN}[heard]{RESET} {text}")

            if self._is_stop(text):
                self._note("Stop phrase detected — voice disabled for this session.")
                self._stop_event.set()
                break

            if self.require_wake_word:
                is_wake, remainder = self._strip_wake_word(text)
                if not is_wake:
                    continue
                if self.on_wake:
                    try:
                        self.on_wake()
                    except Exception:
                        pass
                # Wake word with no tail — capture next phrase as the command
                if not remainder:
                    self._note("Wake word detected, waiting for command…")
                    remainder = self._listen_phrase(timeout=8, phrase_limit=self.phrase_limit)
                    if not remainder:
                        self._note(f"{YELLOW}No command heard after wake word.{RESET}")
                        continue
                utterance = remainder
            else:
                utterance = text

            try:
                if self.on_utterance:
                    self.on_utterance(utterance)
                self.output_queue.put(utterance)
            except Exception as e:
                self._note(f"{RED}Dispatch error: {e}{RESET}")

        self._note("Voice input stopped.")
