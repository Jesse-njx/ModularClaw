# Module base class (`core.py`)

`Module` is the abstract shape the **`Runtime`** expects: a small object with optional lifecycle hooks and injected **`_runtime`** / **`_name`** after registration.

Class constant:

- **`VERSION`** — Optional but recommended. If present, **`Runtime.register_module`** compares it to the **`version`** field in that module’s JSON config (loaded by name) and raises **`VersionMismatchError`** on mismatch.

## Constructor

### `__init__(self)`

- Sets **`self._runtime = None`** and **`self._name = None`**. The runtime overwrites these in **`register_module`**.

---

## Properties

### `runtime` → `Runtime`

- Returns **`self._runtime`**. After registration this is the owning runtime instance; before registration it is `None`.

### `name` → `str | None`

- Returns **`self._name`**. After registration this is the string key passed to **`register_module`**; before registration it is `None`.

---

## Lifecycle hooks

All three default to **no-ops** (`pass`). Subclasses override selectively.

### `on_loop(self, session: Session)`

- Invoked by **`Runtime.newloop`** for each registered module only if **`hasattr(module, 'on_loop')`**. The base **`Module`** class defines **`on_loop`**, so any normal **`Module`** subclass inherits it and **is called every new loop** (with the default no-op unless overridden). Unusual setups (e.g. registering a non-`Module` object) could omit the attribute.
- Intended for work at the **start of a new loop** for a session. The runtime has already incremented **`session.loop_count`** and reset **`session.tick_count`** before calling hooks.

### `on_tick(self, session: Session)`

- Invoked by **`Runtime.tick`** under the same **`hasattr(module, 'on_tick')`** rule. Again, the base class defines **`on_tick`**, so typical subclasses are invoked every tick unless they rely on an instance that drops the attribute (rare).
- Intended for small, periodic work once per global tick for that session.

### `on_session_start(self, session: Session)`

- Invoked by **`Runtime.create_session`** for **every** registered module when a new session is created.
- Intended for per-session setup (subscriptions, initial status, etc.).

---

## Contract summary

| Responsibility | Owner |
|----------------|--------|
| Registering the module | Caller of `Runtime.register_module` |
| Setting `_runtime` / `_name` | `Runtime.register_module` (do not rely on manual assignment) |
| Implementing hooks | Subclass of `Module` |
| Version alignment | `Module.VERSION` vs config `version` when both set |

This document does not name or describe concrete modules in `modules/`; it only describes the base type they extend.
