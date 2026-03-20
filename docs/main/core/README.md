# Core framework

This folder documents the **core layer** of ModularClaw: runtime orchestration, the pluggable module contract, per-conversation session state, and configuration loading. It intentionally does **not** describe concrete modules under `modules/`—only the APIs they plug into.

## How the pieces fit

| Piece | Role |
|--------|------|
| [`Runtime`](runtime.md) | Owns registered modules and sessions; drives ticks, loops, optional main loop, and internal events. |
| [`Module`](module-base-class.md) | Base class for anything the runtime invokes; subclasses override lifecycle hooks. |
| [`Session`](session.md) | Mutable state for one logical run (context, logs, status, region claims, loop flags). |
| [`Config` / `VersionMismatchError` / `NameConflictError`](config-loader.md) | JSON config discovery and version/naming guardrails used by runtime and sessions. |

At a high level: the runtime discovers/registers modules, creates sessions, and repeatedly calls `on_tick` (and optionally `on_loop` via `newloop`). Sessions carry the data modules read and write. Configuration comes from `config/*.json` via `Config`.

Current naming rule is strict: module filename stem, runtime registration name, and config filename stem must be exactly the same (for example `modules/file_system.py` -> `file_system` -> `config/file_system.json`).

## Document map

- [Runtime (`core.py`)](runtime.md) — `Runtime` class
- [Module base class (`core.py`)](module-base-class.md) — `Module` class
- [Session (`session.py`)](session.md) — `Session` class
- [Config loader (`config_loader.py`)](config-loader.md) — `Config`, `VersionMismatchError`, `NameConflictError`

## Source files (reference)

| Document | Python module |
|----------|----------------|
| Runtime, Module | `core.py` |
| Session | `session.py` |
| Config | `config_loader.py` |
