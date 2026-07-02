# JARVIS / Chimera — Voice System Specification

**Status: Planning phase — pre-implementation**
**Last updated: 2026-07-01**

This document specifies the current state of voice interaction in the JARVIS repository and the phased plan to close the gap to the target voice pipeline described in the project vision. It is grounded entirely in the audited contents of `hugginggpt/server/voice_module.py` and `hugginggpt/server/requirements_jarvis.txt` — no other voice code exists anywhere in the repository.

Related documents: [`vision/JARVIS_VISION.md`](../vision/JARVIS_VISION.md), [`vision/PRODUCT_PRINCIPLES.md`](../vision/PRODUCT_PRINCIPLES.md), [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md), [`architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md), [`architecture/MEMORY_ARCHITECTURE.md`](../architecture/MEMORY_ARCHITECTURE.md), [`architecture/SECURITY_MODEL.md`](../architecture/SECURITY_MODEL.md), [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md), [`specifications/BRAIN_SPEC.md`](BRAIN_SPEC.md), [`specifications/UI_SPEC.md`](UI_SPEC.md), [`specifications/3D_ENGINE.md`](3D_ENGINE.md), [`specifications/COMPUTER_CONTROL.md`](COMPUTER_CONTROL.md), [`roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md), [`context/PROJECT_CONTEXT.md`](../context/PROJECT_CONTEXT.md), [`context/DECISION_LOG.md`](../context/DECISION_LOG.md), [`context/DESIGN_HISTORY.md`](../context/DESIGN_HISTORY.md), [`context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md).

---

## 1. Executive Summary

There is exactly one voice implementation in this repository: `hugginggpt/server/voice_module.py`, a single-file, 419-line, synchronous, wake-word-gated CLI script that belongs to the **legacy stack** (see [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) for the "four islands" framing). It is listed under the `## ── Voice ──` section of `hugginggpt/server/requirements_jarvis.txt`, a dependency file that also pulls in PC automation (`pyautogui`, `pyperclip`, `psutil`), Claude API access (`anthropic`), and HuggingFace/Gradio leftovers (`tiktoken==0.3.3`, `gradio==3.24.1`) — confirming this voice module is bolted onto the legacy Flask/HuggingFace/Claude system, not onto either of the newer MQTT stacks.

**Neither `jarvis_prod` nor `jarvis_core` has any voice interface at all.** `jarvis_prod` (`brain_pc.py` + `agent_phone.py`, MQTT v5/QoS1 over Tailscale) is a text/command transport with no audio I/O anywhere in its dependency tree. `jarvis_core` (Planner/Critic/Executor Reflexion loop over local Ollama) is a text-only reasoning loop with no audio I/O either. Both are silent by construction. This means the voice gap is not "improve the existing voice system" in isolation — it is "the only voice code in the repo is wired to the one stack (legacy) that the rest of the architecture roadmap plans to eventually retire, and the two stacks meant to carry the project forward (`jarvis_prod`, `jarvis_core`) have never had a microphone or speaker connected to them at all."

This document treats `voice_module.py` as the current-state baseline, defines the target pipeline from the project vision, and lays out four incremental phases (A–D) to close the gap without a rewrite-from-scratch. Per [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) Phase 0–2 discipline, none of this work should begin before the canonical-Brain decision (Phase 1 of the master roadmap) is made in writing — building a sophisticated voice pipeline against an undecided backend risks becoming a fifth disconnected island. Phases A–D below are written as "ready to execute once the target Brain is chosen," not as work to start immediately.

---

## 2. Current State: `voice_module.py`

### 2.1 What it is

- **Location:** `hugginggpt/server/voice_module.py` (419 lines, single file, no package structure).
- **Invocation:** `python voice_module.py --server http://localhost:8004` — a standalone CLI process, not a service, not a background daemon, not managed by any process supervisor.
- **Transport to the Brain:** plain synchronous HTTP `POST` via the `requests` library to `{server_url}/hugginggpt` (default `http://localhost:8004`), which is `awesome_chat.py`'s Flask endpoint. This is the legacy stack's port — not an MQTT topic, not a WebSocket, not anything `jarvis_prod` or `jarvis_core` expose.
- **Concurrency model:** one main thread runs a blocking listen → transcribe → HTTP POST → speak loop; a second daemon thread drains a `queue.Queue` for TTS playback so speech output doesn't block the next listen cycle. There is no async runtime (no `asyncio`) anywhere in this file, unlike `jarvis_prod`'s `brain_pc.py`/`agent_phone.py`.

### 2.2 Pipeline as implemented

```
Microphone (blocking capture, sr.Microphone context manager)
   │
   ▼
Wake word check on FULL utterance text ("hey jarvis" / "jarvis" / "okay jarvis")
   │  (string .startswith() match on already-transcribed text — the entire phrase
   │   must first be captured and transcribed before the wake word can be detected)
   ▼
STT: Google Web Speech API (default) OR local openai-whisper (opt-in, --stt whisper)
   │
   ▼
HTTP POST to http://localhost:8004/hugginggpt  (awesome_chat.py, legacy Flask server)
   │  payload: {"messages": [...], "conversation_id": "voice-<timestamp>"}
   │  60-second timeout, no retry, no streaming — one full request/response round trip
   ▼
TTS: pyttsx3 (offline, default) OR ElevenLabs (opt-in, requires ELEVENLABS_API_KEY)
   │  queued on a worker thread so the main loop can resume listening
   ▼
Speaker output (pyttsx3.runAndWait() or elevenlabs.play())
```

### 2.3 Confirmed capabilities and confirmed absences

| Capability | Status | Evidence in `voice_module.py` |
|---|---|---|
| Wake word detection | Present, but naive | `_is_wake_word()` does a `str.startswith()` check against `WAKE_WORDS = ["hey jarvis", "jarvis", "okay jarvis"]` on the *already fully transcribed* utterance — there is no dedicated wake-word model; the whole phrase (including the command after it) must be captured and sent to STT first, which means every wake attempt pays full STT latency even for false positives |
| STT | Present, two backends | `STTEngine.backend` is `"google"` (`sr.Recognizer.recognize_google`, requires internet) by default, or `"whisper"` (`openai_whisper.load_model(whisper_model)`, local) via `--stt whisper` |
| TTS | Present, two backends + fallback | `TTSEngine.backend` resolves via `"auto"` to ElevenLabs (if `HAS_ELEVENLABS` and `ELEVENLABS_API_KEY` set) → pyttsx3 (if installed) → `"print"` (text-only fallback, no audio) |
| Streaming audio (STT or TTS) | **Absent** | STT only runs after `sr.Recognizer.listen()` returns a complete, silence-terminated audio buffer (`pause_threshold = 0.8`s); TTS only starts after the full response text is returned from the HTTP call and queued — no partial-result or token-level streaming exists anywhere |
| Interruption / barge-in | **Absent** | `TTSEngine.speak()` calls `self._engine.runAndWait()` (pyttsx3) or `play(audio)` (ElevenLabs) synchronously inside the TTS worker thread with no cancellation path; there is no code that stops playback if the user starts speaking, and the main listen loop is blocked by the microphone context manager regardless |
| Speaker identification | **Absent** | No voiceprint, embedding, or per-speaker state exists; every utterance is treated identically regardless of who spoke |
| Voice authentication | **Absent** | No authentication of any kind gates command execution — any voice that says a wake word followed by a command is forwarded to `awesome_chat.py` and executed |
| Emotion-aware response | **Absent** | TTS backends are invoked with static parameters (`rate=175` for pyttsx3; a fixed `elevenlabs_voice="Rachel"` and `model="eleven_monolingual_v1"` for ElevenLabs) — no sentiment/emotion signal from STT or the LLM response ever reaches the TTS call |
| Continuous/always-on operation | Present | `run(continuous=True)` loops indefinitely until Ctrl-C or a stop phrase (`"stop listening"`, `"go to sleep"`, `"jarvis stop"`) is heard |
| Ambient noise calibration | Present | `STTEngine.calibrate()` calls `recognizer.adjust_for_ambient_noise()` for 1.5s at startup, adjusting `energy_threshold` (default 300) |

### 2.4 Dependency footprint (`requirements_jarvis.txt`, `## ── Voice ──` section)

```
SpeechRecognition>=3.10.0   # STT wrapper (Google Web Speech API by default)
pyaudio>=0.2.13              # microphone access, requires PortAudio system library
pyttsx3>=2.90                # offline TTS

# Optional, commented out by default:
# openai-whisper>=20230918   # local Whisper STT, ~150MB model download on first use
# elevenlabs>=0.2.24          # cloud TTS with natural voices, requires API key
```

Both optional upgrades (`openai-whisper`, `elevenlabs`) are present in the file only as comments — they are not installed by a default `pip install -r requirements_jarvis.txt` run. This is consistent with the "legacy stack, daily-use, minimal-dependency" character of the rest of that file (Flask 2.2.3, `tiktoken==0.3.3`, `gradio==3.24.1` — all old, unmodified pins per [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md)'s dependency-drift findings).

### 2.5 Why this module cannot simply be "pointed at" `jarvis_prod` or `jarvis_core`

`voice_module.py`'s only integration point is a single hardcoded HTTP call shape (`POST {server}/hugginggpt` with a `messages` array and `conversation_id`). Neither `brain_pc.py` (MQTT command/response over `jarvis/node/<id>/...` topics) nor `jarvis_core`'s Planner/Critic/Executor loop (invoked via direct Python function calls / local Ollama, no HTTP surface at all per the audit) expose anything resembling this endpoint. Re-pointing today's `voice_module.py` at either newer stack is not a config change — it requires writing a new transport adapter, which is exactly the kind of the ad hoc coupling [`architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) and the `jarvis-architect` skill persona (`.claude/skills/jarvis-architect.md`) warn against doing informally. The correct sequencing is: decide the canonical Brain first (master roadmap Phase 1), then design the voice pipeline's transport against that decision — not against `awesome_chat.py`'s Flask endpoint, which is explicitly the system this consolidation is planned to move away from.

---

## 3. Target Architecture (Vision)

The following is the target state per the project vision. **None of it is implemented today.** It is documented here as the destination, not a description of running code.

### 3.1 Target pipeline

```
Microphone (continuous capture, streaming)
   │
   ▼
Silero VAD  ── voice-activity detection, replaces "listen until 0.8s silence"
   │              with real per-frame speech/non-speech classification
   ▼
Streaming Faster Whisper  ── incremental transcription as audio arrives,
   │                          not "wait for full utterance, then transcribe"
   ▼
Wake-word / intent gate  ── evaluated on a rolling partial transcript,
   │                          not on a fully-completed phrase
   ▼
Speaker identification  ── voiceprint match against enrolled speakers
   │                          (gates which memory/permissions apply)
   ▼
Brain (target: consolidated Brain per architecture/SYSTEM_ARCHITECTURE.md,
   │    not awesome_chat.py) — streaming token-level response generation
   ▼
Streaming TTS: Kokoro TTS or XTTS  ── audio begins playing before the full
   │                                   response text is generated
   ▼
Interruption handling  ── barge-in detection cancels in-flight TTS playback
   │                       and re-enters the VAD/STT path immediately
   ▼
Speaker output, optionally emotion-modulated based on response sentiment
```

### 3.2 Target component choices and why (per vision)

| Layer | Current (`voice_module.py`) | Target | Rationale |
|---|---|---|---|
| VAD / turn-taking | `pause_threshold=0.8`s silence timeout via `SpeechRecognition` | Silero VAD | Frame-level speech detection replaces a fixed silence timer, enabling streaming STT and faster wake-word response |
| STT | Google Web Speech API (cloud, blocking) or `openai-whisper` (local, blocking, full-utterance only) | Faster Whisper, streaming | Local-first (per `CLAUDE.md`'s "local-first where practical" principle), lower latency via CTranslate2 inference, incremental partial transcripts instead of one blocking call |
| TTS | `pyttsx3` (offline, robotic) or ElevenLabs (cloud, non-streaming `generate()` + `play()`) | Kokoro TTS or XTTS, streaming | Natural-sounding local-capable TTS with streaming synthesis so playback can begin before the full response is synthesized, and so playback can be cancelled mid-utterance for interruption support |
| Interruption / barge-in | None | Explicit interruption handling (Phase C below) | Required for any voice assistant meant to feel conversational rather than walkie-talkie/half-duplex |
| Speaker identity | None | Speaker identification + voice authentication (Phase D below) | Required before voice can be trusted as an input channel for anything beyond read-only queries, especially once `pc.*`/`phone.*` skill execution (per [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md)) is reachable via voice |
| Emotional affect | Static TTS parameters (`rate=175`, fixed ElevenLabs voice) | Emotion-aware response modulation | TBD — confirm with user whether this means emotion-conditioned TTS prosody, LLM-side tone adjustment, or both |
| Transport to Brain | Synchronous HTTP POST to `awesome_chat.py:8004` | TBD — confirm with user once the canonical Brain (per [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) Phase 1 ADR) is decided; likely candidates are an MQTT topic under the `jarvis/node/<id>/...` namespace (consistent with `jarvis_prod`'s `protocol.py`) or a local IPC/WebSocket bridge if voice runs co-located with the Brain process | Must not be designed until the Phase 1 canonical-Brain ADR lands, per Section 1 above |

---

## 4. Gap Analysis Summary

| Target capability | Exists today? | Where | Gap |
|---|---|---|---|
| Wake word detection | Partial (string match on full transcript) | `voice_module.py::_is_wake_word` | No dedicated wake-word model; detection happens after full STT, not before |
| VAD | Partial (fixed silence timeout) | `voice_module.py::STTEngine` (`pause_threshold=0.8`) | No real VAD model; Silero VAD not integrated anywhere in the repo |
| STT | Yes (non-streaming) | `voice_module.py::STTEngine` (Google / openai-whisper) | No streaming; no Faster Whisper; `openai-whisper` is commented out by default in `requirements_jarvis.txt` |
| TTS | Yes (non-streaming) | `voice_module.py::TTSEngine` (pyttsx3 / ElevenLabs) | No streaming; no Kokoro/XTTS; ElevenLabs is commented out by default |
| Streaming audio pipeline (end-to-end) | No | — | Every stage in `voice_module.py` is blocking and full-buffer; zero streaming anywhere |
| Interruption / barge-in | No | — | Not implemented, not stubbed, not referenced anywhere in the codebase |
| Speaker identification | No | — | Not implemented, not stubbed, not referenced anywhere in the codebase |
| Voice authentication | No | — | Not implemented; any voice can issue any command `awesome_chat.py` accepts |
| Emotion-aware responses | No | — | Not implemented; TTS parameters are static |
| Voice interface on `jarvis_prod` | No | — | `jarvis_prod` has no audio I/O dependency or code path at all |
| Voice interface on `jarvis_core` | No | — | `jarvis_core` has no audio I/O dependency or code path at all |

---

## 5. Phased Plan to Close the Gap

Each phase is scoped to be independently shippable and testable, following the incremental-strategy discipline in [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) (no big-bang rewrite, observable exit criteria per phase, legacy stack untouched until its replacement is proven). Phases are ordered by dependency, not necessarily by calendar priority against the rest of the master roadmap — voice work should not begin before the master roadmap's Phase 1 canonical-Brain ADR is signed off, since Phase A below already assumes a stable target transport.

### Phase A — Silero VAD + streaming Faster Whisper (STT layer only)

**Goal:** Replace the STT half of the pipeline without touching the TTS half, the wake-word logic's external behavior, or the HTTP transport to the Brain. This isolates risk to exactly one layer.

- Swap `STTEngine`'s `sr.Recognizer.listen()` + `pause_threshold` silence-timeout capture for a Silero VAD-gated audio stream: VAD determines speech/non-speech per frame instead of a fixed 0.8s silence timer.
- Replace both existing STT backends (Google Web Speech API, `openai-whisper` full-buffer) with Faster Whisper running in streaming/incremental mode.
- Keep the wake-word check (`_is_wake_word`) operating on transcript text for this phase — do not yet move it to a rolling partial-transcript model; that upgrade is deferred to keep Phase A's surface area small.
- Keep the HTTP POST to the Brain (`{server}/hugginggpt` or its eventual replacement per Section 3.2) completely unchanged in this phase — Phase A is STT-only.
- Keep TTS (`pyttsx3`/ElevenLabs) completely unchanged in this phase.
- **Exit criteria:** wake-word-to-command latency measurably improves versus current `voice_module.py` baseline; transcription accuracy is verified against a fixed test-phrase set; the rest of the pipeline (TTS, HTTP transport) requires zero code changes to keep working.
- **Dependencies:** Silero VAD model, Faster Whisper (CTranslate2-based) runtime — both must be evaluated for CPU-only feasibility on the main desktop PC per `CLAUDE.md`'s "local-first where practical" principle before assuming GPU availability.

### Phase B — Streaming TTS (Kokoro TTS or XTTS)

**Goal:** Replace the TTS half of the pipeline so that audio playback can begin before the full response text has been generated/received, without yet requiring interruption support (that is Phase C).

- Replace `TTSEngine`'s `pyttsx3`/ElevenLabs backends with Kokoro TTS or XTTS running in streaming synthesis mode.
- Requires the upstream response source (Brain) to support streaming/incremental text output rather than a single blocking HTTP response body — this is a hard dependency on whatever the canonical Brain's response transport looks like post-Phase-1-ADR (see Section 3.2's open TBD on transport). If the Brain still returns a single blocking JSON response at the time this phase starts, Phase B should synthesize/play in sentence-level chunks as a fallback rather than blocking on the full response.
- Keep the `_tts_queue`/worker-thread pattern from `voice_module.py` as the concurrency skeleton (it already exists for exactly the "don't block listening on speech output" reason Phase B needs) — extend it to accept streamed audio chunks rather than a single complete text string.
- **Exit criteria:** perceived response latency (time from Brain starting to respond to first audible audio) drops versus Phase A baseline; playback audio quality is verified against Kokoro/XTTS reference samples; Kokoro vs. XTTS selection is finalized based on latency/quality/licensing tradeoffs — TBD, confirm with user which one ships.
- **Open decision:** TBD - confirm with user — Kokoro TTS and XTTS have different licensing, voice-cloning, and latency characteristics; this document does not have grounds to pick one from the audit alone.

### Phase C — Interruption handling (barge-in)

**Goal:** Allow the user to speak over an in-progress TTS response and have the system stop talking and start listening, without losing the wake-word/VAD/STT/TTS work from Phases A–B.

- Requires Phase A's VAD to run concurrently with TTS playback (not just during the listen phase) so that user speech during playback is detected in real time.
- Requires Phase B's streaming TTS to expose a cancellation hook — this is precisely why Phase B must land first: `pyttsx3.runAndWait()` and ElevenLabs' `play()` in the current `voice_module.py` are both synchronous, uncancellable calls; a streaming TTS backend is a prerequisite for being able to stop mid-utterance at all.
- Add explicit state machine transitions (e.g., `LISTENING → SPEAKING → INTERRUPTED → LISTENING`) so that a detected barge-in cleanly cancels the active TTS stream, discards any queued-but-unplayed audio in the `_tts_queue` equivalent, and re-enters the STT path without the "Yes, I'm listening" wake-reconfirmation step that today's `voice_module.py` requires after every wake word.
- **Exit criteria:** a user speaking during TTS playback measurably halts audio output within a defined latency budget (TBD - confirm with user for the target number, e.g. sub-300ms) and the interrupting utterance is correctly captured as the next command.

### Phase D — Speaker identification / voice authentication

**Goal:** Attach identity to voice input so that (a) responses and memory context can be personalized per speaker, and (b) commands with real-world side effects (per [`architecture/TOOL_ROUTING.md`](../architecture/TOOL_ROUTING.md) and the `pc.*`/`phone.*` skill surfaces referenced in [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md)) are not executable by an arbitrary voice.

- Add a voiceprint enrollment step and a speaker-embedding comparison model that runs on captured audio (likely reusing the same audio stream Phase A's Silero VAD already gates) to identify or reject a speaker before a command is forwarded to the Brain.
- Define an explicit authorization policy: which commands require a recognized/authenticated speaker versus which remain open to any voice (e.g., read-only status queries vs. `pc.shell.run`-class commands, which [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) already flags as high-severity and currently unimplemented on the executor side regardless of voice).
- This phase should be designed jointly with whatever the consolidated Brain's confirmation/risk-gating model ends up being (root `core/`'s `risk.py`/`safety.py`, once its confirmed executor ordering bug is fixed, per [`architecture/SYSTEM_ARCHITECTURE.md`](../architecture/SYSTEM_ARCHITECTURE.md) Section on the live safety bypass) — voice authentication should feed into that gate, not duplicate a separate one.
- **Exit criteria:** an unenrolled/unrecognized voice is provably blocked from triggering side-effecting commands in a test scenario; an enrolled voice's identity is available to the Brain's memory/context layer for personalization.
- **Open decision:** TBD - confirm with user — whether voice authentication is a hard gate (block execution outright) or a soft signal (log identity, still require a separate confirmation channel for high-risk actions) is a product decision this audit cannot resolve from repo evidence alone.

### Phase sequencing summary

| Phase | Depends on | Touches | Does not touch |
|---|---|---|---|
| A — VAD + streaming STT | Master roadmap Phase 1 Brain ADR (for transport target) | STT, wake-word input format | TTS, HTTP/transport shape |
| B — Streaming TTS | Phase A landed; Brain's response transport supports streaming or sentence-chunk fallback | TTS | STT, VAD, wake-word logic |
| C — Interruption handling | Phase A + Phase B both landed | VAD (concurrent-with-playback mode), TTS (cancellation hook), state machine | Underlying STT/TTS model choices |
| D — Speaker ID / auth | Phase A landed (reuses audio stream); ideally after Brain's risk/safety gating is fixed | New voiceprint layer, authorization policy | Existing STT/TTS pipeline mechanics |

---

## 6. Hardware Context: Future Dedicated Voice Terminal

Per `CLAUDE.md`'s confirmed hardware inventory, the repo's owner holds salvaged components from a 10.1" Android tablet (motherboard `A6001_MB_PCB_V4`, stored intact) that are earmarked as a future dedicated Jarvis display/voice terminal, distinct from running voice software on the main desktop PC:

- **MEMS microphone** (listed as "Recoverable" in `CLAUDE.md`'s component table) — a candidate input device for a standalone voice-terminal node, as opposed to the current `voice_module.py` model of using whatever microphone is attached to the desktop PC via `sr.Microphone()`.
- **Stereo speakers, part numbers 09C26QA / 09C26QB** (listed as "Intact") — a candidate output device for the same standalone terminal, again distinct from the desktop PC's default audio output that `pyttsx3`/ElevenLabs currently play through.
- **LCD panel, Innolux P101DCA-AB0** (listed as "Likely functional") — while not audio hardware, this is the display component of the same salvage set and is called out in `CLAUDE.md` as a "priority recovery target for a Jarvis display/voice terminal," meaning the intended end state bundles voice I/O with a visual display on one physical node.

None of Phases A–D above assume or require this hardware — all four phases are scoped to software running against whatever microphone/speaker the host PC exposes today, consistent with how `voice_module.py` already operates. The salvaged tablet components become relevant only if/when the project decides to stand up a dedicated physical voice-terminal node (per `CLAUDE.md`'s Roadmap Phase 3, "Repurpose OPPO and Vivo as dedicated nodes," and by extension the tablet components as a fourth node type). That is a hardware-integration and device-registry question — see [`architecture/AGENT_ARCHITECTURE.md`](../architecture/AGENT_ARCHITECTURE.md) for how new nodes are expected to register with the Brain — and is out of scope for this document beyond flagging it as the eventual destination for this hardware.

**Constraints inherited from `CLAUDE.md` that apply if/when this hardware is used:**
- Do not harvest BGA chips (CPU, RAM, eMMC, PMIC) without rework equipment.
- Store the motherboard intact; store the LCD flat; do not bend flex cables.
- Inspect the 3.85V/7000mAh Li-ion battery for swelling/leakage before any use.
- Acquire a digital multimeter and soldering iron (per `CLAUDE.md`'s Hardware Acquisition Priorities) before attempting any diagnostic or repair work on this motherboard.

---

## 7. Open Questions (TBD — confirm with user)

- Which of `jarvis_prod`'s MQTT transport, a new WebSocket/IPC bridge, or something else becomes the voice module's connection to the Brain once the canonical-Brain ADR (master roadmap Phase 1) is decided.
- Kokoro TTS vs. XTTS final selection for Phase B (licensing, latency, and voice-cloning tradeoffs not resolved by this audit).
- What "emotion-aware responses" concretely means for this project — TTS prosody modulation, LLM-side tone adjustment, or both.
- Target interruption-detection latency budget for Phase C.
- Whether Phase D's voice authentication is a hard execution gate or a soft identity signal feeding a separate confirmation mechanism.
- Whether voice input will ever be extended to non-desktop nodes (Nothing Phone 3a Lite, a future tablet-derived voice terminal) and if so, how `voice_module.py`'s current single-process-on-the-PC model generalizes to a distributed, multi-node voice input model consistent with the MQTT node architecture.
