# Memory Architecture

Status: Planning phase - pre-implementation
Last updated: 2026-07-01

## Purpose and Scope

This document defines the target memory model for the Chimera/JARVIS Brain and
maps it against what actually exists in the repository today. It is written
against the finding, established in the full repo audit, that **three
unrelated memory implementations exist and none of them talk to each other**:
`core/memory.py` (root), `jarvis_core/core/memory.py` (ChromaDB RAG store),
and `hugginggpt/server/memory_module.py` (legacy conversation buffer). A
fourth artifact, `jarvis_core/brain/knowledge_graph.md`, is referenced in
`CLAUDE.md`-adjacent design docs as a knowledge layer but is confirmed to be
an unpopulated template.

Per CLAUDE.md's design principle **"Brain owns memory. Agents never access
databases directly,"** none of the three existing stores currently live
inside anything that could be called "the Brain" — they are each private to
the island that defines them. This document describes the target memory
service and the concrete gap between it and today's reality. Related
documents: [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) for how the
memory service fits into the overall layer diagram,
[`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md) for how agents are meant to
request memory rather than touch storage directly, and
[`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md) for the
Brain process that will eventually own this service.

---

## 1. Target Memory Types

The Chimera vision (CLAUDE.md: "Memory Manager (SQLite / PostgreSQL /
VectorDB)") implies a Brain with several distinct memory subsystems, each
with different lifetimes, storage backends, and consumers. These are the
target categories this architecture is designed around:

| # | Memory type | Lifetime | Typical backend | Primary consumer |
|---|---|---|---|---|
| 1 | **Working memory** | Single request / single agent turn | In-process dict/object | Whichever agent is currently executing |
| 2 | **Conversation memory** | Minutes to hours (a session) | In-memory ring buffer, optionally persisted | Jarvis interaction layer, NL routing |
| 3 | **Project/task memory** | Days to weeks (life of a task or job) | SQLite (job/task tables) | Agent Manager, schedulers, Reflector/circuit-breaker logic |
| 4 | **Semantic memory** | Indefinite | Vector DB (embeddings) | RAG retrieval for reasoning (Planner/Critic) |
| 5 | **Episodic memory** | Indefinite, append-only | SQLite / log table + optional vector index | Reflection, audit, "what happened when" queries |
| 6 | **Knowledge graph** | Indefinite, curated/compressed | Structured file or graph store, human-editable | Long-horizon reasoning, pattern detection across episodes |
| 7 | **Reflection memory** | Indefinite | SQLite or append-only doc | Self-critique loop (Critic), NEED_ATTENTION escalation history |
| 8 | **Tool memory** | Indefinite (schema), ephemeral (invocation state) | Static registry (code/config) + SQLite for invocation history | Executor, tool-routing layer |
| 9 | **Long-term memory (umbrella)** | Indefinite | Combination of semantic + episodic + knowledge graph | Brain as a whole |

These map to CLAUDE.md's "Memory Manager" and "Knowledge Layer" boxes in the
Brain layer diagram — currently both boxes are empty; nothing in the repo
implements them as a shared service.

---

## 2. Current State: What Exists, Mapped to Target Types

This section is the load-bearing part of the document: each real file is
mapped to the target type(s) it partially implements, and what is missing.

### 2.1 `core/memory.py` (root execution-safety module)

```python
MAX_MEMORY_ITEMS = 1000
memory = OrderedDict()
_lock = RLock()

def set_context(key, value): ...
def get_context(key): ...
```

- **What it is**: a 24-line, thread-safe, bounded (1000-item) LRU-like
  in-process key/value cache using `OrderedDict` + `RLock`. No persistence,
  no embeddings, no schema beyond arbitrary key/value.
- **Target type covered**: (1) Working memory only — and even that is a
  partial fit, since it is a single global module-level dict (`memory = OrderedDict()`),
  not scoped per-agent, per-task, or per-session. Any two callers in the same
  process share the same namespace unless they manually prefix keys.
  Nothing else in root `core/` is confirmed to call `set_context`/`get_context`
  in production paths — its only confirmed callers are its own test suite
  (`core/tests/`, `tests/test_adversarial_execution.py`,
  `tests/test_executor_safety.py`).
- **Completely missing**: conversation memory, semantic/vector memory,
  episodic memory, knowledge graph, reflection memory, tool invocation
  history, any persistence at all (a process restart loses everything).
- **Note on scope**: root `core/` is, per the audit, a dormant
  execution-safety kernel (idempotency, sandbox, undo, risk, safety,
  validator, executor, retry, tools registry) that nothing outside its own
  test files imports. Its `memory.py` should be read as "scratch space for
  the safety kernel's own bookkeeping," not as a candidate Brain memory
  service. See [`SECURITY_MODEL.md`](SECURITY_MODEL.md) for the confirmed
  live safety/risk-ordering bug in this module's `executor.py`, which is
  unrelated to `memory.py` but lives in the same package.

### 2.2 `jarvis_core/core/memory.py` (`MemoryStore`, ChromaDB RAG)

```python
class MemoryStore:
    def __init__(self, cfg: JarvisConfig) -> None:
        self._client = chromadb.PersistentClient(path=cfg.memory.chroma_path, ...)
        self._col = self._client.get_or_create_collection(cfg.memory.collection)
        ...
    async def remember(self, text, *, kind="observation", extra=None) -> str: ...
    async def recall(self, query, *, k=5) -> list[dict]: ...
```

- **What it is**: the most architecturally complete memory implementation in
  the repo. It wraps a persistent ChromaDB collection (on disk under
  `brain/chroma/`), embeds text via a local Ollama `nomic-embed-text` model
  (fully local, no data leaves the machine), and exposes exactly two
  operations: `remember(text, kind=...)` (write + embed) and
  `recall(query, k=5)` (embed query, similarity search, return top-k with
  distance scores). Entries are tagged with a `kind` field (default
  `"observation"`) and a timestamp, which is the closest thing in the repo to
  a typed-memory schema.
- **Target types covered**:
  - (4) Semantic memory — this is a real, if never-exercised, vector-DB
    implementation matching CLAUDE.md's "VectorDB" tech-stack line.
  - (5) Episodic memory, partially — the `kind`/`ts` metadata on each
    `remember()` call could represent discrete events, but there is no
    querying-by-time-range or event-sequence API; retrieval is
    similarity-only (`recall`), not chronological.
  - (7) Reflection memory, partially — nothing prevents a Critic rejection or
    a NEED_ATTENTION escalation from being written via `remember(kind="reflection")`,
    but no calling code in the audited snapshot actually does this; the
    `kind` taxonomy is undefined beyond the `"observation"` default.
- **Completely missing**: working memory (no in-process scratch layer),
  conversation memory (no turn-based, ordered retrieval — everything is
  similarity search over a flat collection), tool memory (no invocation-log
  table), and critically, **it has never been exercised end to end**. Per
  the audit: `jarvis_core` was committed once (`5dfa65f`, "V1") and never
  touched again through `4b32760` ("V2") and `fe41bf5` ("V2.1"); there is no
  `.env`, no `history.log`, no `logs/`, and **no `brain/chroma/` directory on
  disk at all** — confirmed by direct filesystem inspection
  (`jarvis_core/brain/` contains only `inbox/.gitkeep` and
  `knowledge_graph.md`). This means zero embeddings have ever been persisted;
  the class is real code that has never run against live data.
- **Naming-collision risk**: this module is importable as bare `core` when
  `jarvis_core/` is on `sys.path` (which `jarvis_core/tests/conftest.py`
  does deliberately), and root `core/` is importable as bare `core` when the
  repo root is on `sys.path` instead. The two `memory.py` files are
  unrelated in content and API shape; do not assume they are versions of the
  same module. See [`SECURITY_MODEL.md`](SECURITY_MODEL.md) for the full
  collision writeup and the required rename before either is promoted.

### 2.3 `hugginggpt/server/memory_module.py` (`ConversationMemory`)

```python
class ConversationMemory:
    def __init__(self, max_turns: int = 10, max_age_seconds: int = 3600):
        self.history = deque(maxlen=max_turns * 2)
        ...
    def add_turn(self, user_message, assistant_response) -> None: ...
    def get_context(self, max_turns=None) -> List[Dict[str, str]]: ...
    def clear(self) -> None: ...
```

- **What it is**: a single global (`_global_memory` module-level singleton,
  accessed via `get_memory()`) turn-based conversation buffer — a `deque`
  capped at `max_turns * 2` entries (default 10 turns / 20 messages) with a
  time-based expiry (`max_age_seconds`, default 3600s = 1 hour). Built
  specifically to let `awesome_chat.py` answer follow-up questions with
  short-term context.
- **Target type covered**: (2) Conversation memory — this is the only
  implementation in the repo that actually matches this target type's shape
  (ordered turns, role-tagged, windowed, time-decayed). It is also, per the
  audit, part of the **legacy stack** — the one system in real daily use
  (Python 3.10, `jarvis_env` venv, driven over ADB).
- **Explicit multi-user gap acknowledged in the code itself**: the module's
  own docstring/comment states "Global conversation memory instance (per-user
  session) ... In multi-user scenarios, this should be keyed by
  session/user ID" — there is exactly one conversation buffer for the entire
  process, shared across every caller. This is a known, self-documented
  limitation, not a hidden one.
- **Completely missing**: persistence (a process restart loses all
  conversation history — no SQLite/file backing), semantic memory, episodic
  memory beyond the 1-hour/10-turn window, knowledge graph, reflection
  memory, tool memory. It has no relationship to either `core/memory.py` or
  `jarvis_core/core/memory.py` — three independent implementations, zero
  shared interface.

### 2.4 `jarvis_core/brain/knowledge_graph.md` (Knowledge Layer)

- **What it is, confirmed by direct read**: a 5-line file consisting only of
  a title and two sentences of template header text:

  ```markdown
  # Jarvis-Core Knowledge Graph

  Long-term compressed memory — appended to by the hourly maintenance pass.
  Human-editable. Older summaries are kept so patterns can be diffed over time.
  ```

  There is no appended content, no summaries, no diff history. The design
  intent (an hourly `MaintenanceScheduler` pass compresses episodic memory
  into durable summaries here) is real and documented in the file itself,
  but **the scheduler that would populate it has never run** — consistent
  with the broader finding that `jarvis_core` has never been executed
  end-to-end.
- **Target type covered**: (6) Knowledge graph — in name and intent only.
  Zero percent implemented in practice; this is a stub, not a partial
  implementation. Anyone building against this file today would be building
  against an empty template.
- **Completely missing**: everything. There is no graph structure at all
  (the name "knowledge graph" is aspirational — the file format is plain
  Markdown prose, not a graph/triple store), no compression logic wired to
  any scheduler, no read API used by the Planner/Critic loop.

### 2.5 Summary Coverage Matrix

| Target memory type | Covered by | Coverage | Persisted today? |
|---|---|---|---|
| 1. Working memory | `core/memory.py` | Partial (single global namespace, in-process only) | No |
| 2. Conversation memory | `hugginggpt/server/memory_module.py` | Good fit, but single-user/single-process only | No |
| 3. Project/task memory | *(none)* | Not implemented anywhere as a first-class store; closest analog is jarvis_prod's SQLite JobLog for execution history, which is transport/job bookkeeping, not task memory | N/A |
| 4. Semantic memory | `jarvis_core/core/memory.py` (`MemoryStore`) | Real implementation, never exercised | No (no `brain/chroma/` dir exists) |
| 5. Episodic memory | `jarvis_core/core/memory.py` (partial, via `kind`/`ts` metadata) | Weak — no chronological query API | No |
| 6. Knowledge graph | `jarvis_core/brain/knowledge_graph.md` | Stub only — template header, zero content | N/A (nothing to persist) |
| 7. Reflection memory | *(none, theoretically routable through `MemoryStore.remember(kind=...)`)* | Not implemented — no `NEED_ATTENTION.md` has ever been written, no Critic-rejection log exists | No |
| 8. Tool memory | `core/tools.py` (`TOOLS` registry, schema only) | Static schema only; no invocation-history table | N/A |
| 9. Long-term memory (umbrella) | *(none)* | No component unifies 4+5+6+7 into one queryable service | No |

**Bottom line**: of nine target memory types, one (conversation memory) has
a working, if single-process, implementation in daily use; one (semantic
memory) has real but never-executed code; one (working memory) has a
minimal, unused-in-production primitive; and the rest — project/task memory,
episodic memory as a first-class store, knowledge graph, reflection memory,
tool invocation memory, and any unifying long-term memory service — do not
exist in any form beyond a template file or a design intention documented in
a docstring.

---

## 3. Target Architecture: Brain-Owned Memory Service

Per CLAUDE.md: "Brain owns memory. Agents never access databases directly,"
and the tech stack line "Storage (planned): SQLite → PostgreSQL + Vector DB."
The target is a single **Memory Manager** service, owned exclusively by the
Brain process (see [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md)),
exposed to agents only through a request/response API — never through direct
database handles. This directly mirrors the pattern jarvis_prod already
proved out for MQTT transport (agents talk over a protocol, not over shared
state) and extends it to memory.

### 3.1 Proposed storage layout

| Store | Backend | Holds | Rationale |
|---|---|---|---|
| Working memory | In-process (per-request scope, not global) | Scratch state for the current agent turn only | No persistence needed; must be request/task-scoped, not a single global dict (fixes `core/memory.py`'s current single-namespace flaw) |
| Conversation memory | SQLite table (`conversations`, `turns`), read-through cache in-process | Per-session ordered turns, keyed by session/user/device ID | Fixes `memory_module.py`'s "should be keyed by session/user ID" self-documented gap; SQLite gives durability across restarts, which the current deque-only design lacks |
| Project/task memory | SQLite tables (`tasks`, `task_events`) | Task state, status transitions, retry/circuit-breaker counters | Matches CLAUDE.md's SQLite-first storage line; also the natural home for jarvis_prod's Reflector circuit-breaker state, which today lives in its own standalone SQLite (JobLog), not a shared Brain store |
| Semantic memory | Vector DB (ChromaDB to start, per existing `jarvis_core/core/memory.py`; PostgreSQL + pgvector as the "→ PostgreSQL" migration target per CLAUDE.md) | Embedded observations, retrievable by similarity | Reuse `MemoryStore`'s `remember`/`recall` shape — it is the one piece of real, well-designed code among the three islands; do not rewrite from scratch |
| Episodic memory | SQLite table (`episodes`), append-only | Chronologically queryable event log (what happened, when, which agent) | Needed as a first-class store, not inferred from vector-DB metadata; feeds the knowledge-graph compression pass |
| Knowledge graph | Structured file or lightweight graph table, generated by a scheduled compression job reading from episodic memory | Long-horizon compressed summaries, human-editable | Reuses `knowledge_graph.md`'s stated design intent, but requires the hourly `MaintenanceScheduler` to actually exist and run — currently it does not |
| Reflection memory | SQLite table (`reflections`) or `MemoryStore` with a well-defined `kind="reflection"` taxonomy | Critic rejections, 3-strike NEED_ATTENTION escalations, human-in-the-loop decisions | Currently has no home at all; must be added, not migrated, since nothing populates it today |
| Tool memory | Static registry (code/config, e.g. `core/tools.py`'s `TOOLS` dict shape) + SQLite invocation log | Tool schemas (compile-time) + invocation history (runtime) | Schema half already exists in root `core/tools.py`; invocation-history half does not exist anywhere |

### 3.2 Access pattern

```
Agent (Planner / Critic / Executor / Jarvis shell)
     │
     │  memory.recall(query) / memory.remember(text, kind=...) / memory.get_task(...)
     ▼
Brain :: Memory Manager   (single process, single source of truth)
     │
     ├── SQLite   — conversation, task, episodic, reflection, tool-invocation tables
     └── Vector DB — semantic recall (Chroma now, pgvector-on-Postgres later)
```

No agent — including phone-side or PC-side skill executors — opens a SQLite
connection or a Chroma client directly. This is the single biggest structural
change from today's reality, where `jarvis_core`'s `MemoryStore` is
instantiated directly inside whatever process imports it, with no
intermediating service boundary.

---

## 4. Migration Path from the 3 Scattered Stores

Consistent with the incremental, evidence-anchored strategy in
[`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) (no big-bang
rewrite, nothing deleted until its replacement is proven), the memory
consolidation should proceed as follows:

1. **Do not touch any of the three stores in isolation before the
   canonical-Brain ADR lands.** Per the roadmap's Phase 0/1 discipline,
   memory consolidation is a Phase 3 activity (merge transport + reasoning),
   not a Phase 0 fix — these files are working code (or, in `jarvis_core`'s
   case, unexercised-but-intact code) and should not be moved or renamed
   until Phase 1's ADR names the canonical Brain.

2. **First prove `jarvis_core/core/memory.py` actually works** (Phase 2 of
   the roadmap: "Prove jarvis_core Actually Runs"). Since this is the only
   semantic-memory implementation in the repo, it must be run against a live
   Ollama instance at least once — confirming `brain/chroma/` gets created,
   `remember`/`recall` round-trip correctly — before anything is built on
   top of it. Building the Memory Manager around code that has never
   executed would repeat the exact failure mode the audit identifies
   (designed and committed, never observed working).

3. **Promote `MemoryStore` to be the semantic-memory backend of the Brain's
   Memory Manager**, adding a thin service boundary in front of it so agents
   call `memory.remember(...)` / `memory.recall(...)` over the Brain's
   existing MQTT/request-response transport (from jarvis_prod) rather than
   importing the class directly.

4. **Port `ConversationMemory`'s turn/window/expiry logic into the SQLite
   conversation table**, fixing its single-process, single-user limitation
   by keying on session/device ID (the fix its own docstring already calls
   for) — do not discard the max-turns/max-age design, which is sound; just
   back it with SQLite instead of an in-memory deque so it survives a Brain
   restart. The legacy stack keeps using its own in-process
   `ConversationMemory` unmodified during the transition (per the roadmap's
   "legacy stack is never in the critical path" rule) until the new Brain
   demonstrably replaces it for daily use.

5. **Add project/task memory, episodic memory, reflection memory, and tool
   invocation memory as new SQLite tables** — none of these have existing
   code to migrate; they are net-new work, not consolidation. Task memory
   should absorb jarvis_prod's standalone JobLog/Reflector state so the
   circuit-breaker's failure counters live in the same store as everything
   else, rather than in a second isolated SQLite file.

6. **Wire the knowledge-graph compression job last**, once episodic memory
   has real data flowing into it — an hourly (or otherwise scheduled) pass
   that reads recent episodes and appends compressed summaries to
   `knowledge_graph.md` (or its structured successor). This is explicitly
   the last piece to build, since it depends on episodic memory already
   being populated, which depends on the Brain/agent merge already being
   live.

7. **Root `core/memory.py` is not part of this migration** — it is scratch
   space for the (currently dormant, soon-to-be-fixed) safety kernel's own
   bookkeeping, not a Brain memory candidate. It should continue to exist
   as-is (or be simplified/removed) independent of the Memory Manager work,
   once root `core/` is renamed to resolve its `sys.path` collision with
   `jarvis_core/core/` (see [`SECURITY_MODEL.md`](SECURITY_MODEL.md)).

8. **PostgreSQL + pgvector migration is explicitly deferred** — CLAUDE.md's
   tech stack lists storage as "SQLite → PostgreSQL + Vector DB," and this
   document treats SQLite/Chroma as the correct starting backend. Do not
   introduce Postgres until the SQLite-backed Memory Manager has real usage
   data justifying the migration; TBD - confirm with user on the trigger
   condition (data volume, concurrent-write contention, or a fixed roadmap
   date) for that migration.

### 4.1 Non-goals for this document

- This document does not specify the wire protocol between agents and the
  Memory Manager (that is [`../architecture/TOOL_ROUTING.md`](TOOL_ROUTING.md)
  and [`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md) territory).
- This document does not resolve the root-`core/` vs `jarvis_core/core/`
  naming collision — that is tracked in [`SECURITY_MODEL.md`](SECURITY_MODEL.md)
  and must land before any memory code is physically relocated.
- Exact SQLite table schemas (column-level DDL) are TBD - confirm with user;
  this document fixes the store boundaries and ownership model, not the DDL.

---

## 5. Open Questions

- TBD - confirm with user: should conversation memory be per-device
  (Nothing Phone 3a Lite, future OPPO/Vivo nodes) or per-human-user? The
  hardware inventory in CLAUDE.md implies multiple physical nodes reporting
  to one Brain, which argues for device-scoped session keys, but this has
  not been decided.
- TBD - confirm with user: retention policy for episodic memory and the
  knowledge-graph compression cadence (CLAUDE.md/`knowledge_graph.md`
  mentions "hourly," but no scheduler has ever run to validate that cadence
  is right).
- TBD - confirm with user: whether reflection memory (Critic rejections,
  NEED_ATTENTION escalations) needs its own access-control tier distinct
  from semantic/episodic memory, given it may contain safety-relevant
  decisions.

---

## Cross-References

- [`../README.md`](../README.md) — documentation set index
- [`SYSTEM_ARCHITECTURE.md`](SYSTEM_ARCHITECTURE.md) — overall layer diagram this service fits into
- [`AGENT_ARCHITECTURE.md`](AGENT_ARCHITECTURE.md) — how agents are meant to request memory instead of touching storage
- [`SECURITY_MODEL.md`](SECURITY_MODEL.md) — root `core/` vs `jarvis_core/core/` naming collision; safety-kernel bug unrelated to memory but same package
- [`TOOL_ROUTING.md`](TOOL_ROUTING.md) — tool schema/invocation routing, related to tool memory (section 3.1)
- [`../specifications/BRAIN_SPEC.md`](../specifications/BRAIN_SPEC.md) — the Brain process that will own this Memory Manager
- [`../roadmap/MASTER_ROADMAP.md`](../roadmap/MASTER_ROADMAP.md) — phased plan; memory consolidation is Phase 2-3 work, not Phase 0
- [`../context/CURRENT_STATUS.md`](../context/CURRENT_STATUS.md) — live status of each island referenced here
- [`../context/DECISION_LOG.md`](../context/DECISION_LOG.md) — where the canonical-Brain ADR (a precondition for this migration) is recorded
