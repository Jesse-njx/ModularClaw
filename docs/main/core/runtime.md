# Runtime (`core.py`)

The `Runtime` class is the orchestrator: it holds all registered modules and active sessions, enforces version alignment with system config, advances simulation time via **ticks** and **loops**, and provides a tiny **pub/sub** channel for internal events.

Class constant:

- **`VERSION`** — Semantic version string for the runtime implementation. Compared to `version` in the system config (`Config.load("system")`) during initialization; a mismatch raises `VersionMismatchError`.

## Constructor

### `__init__(self)`

- Initializes empty dicts: `modules`, `sessions`, `_callbacks`, and `_session_ticks`. In the current `core.py`, `_session_ticks` is not read after construction (only `{}` is assigned).
- Reads from **`Config.get("system", "runtime", {})`**:
  - **`ticks_per_loop`** — default `10`. After this many ticks in a session, the runtime appends a log line that the tick limit was reached for the current loop (it does not automatically call `newloop`; callers may do that separately).
  - **`max_loops`** — default `5`. Used in `newloop` and `tick` to cap how many loops a session may go through before the runtime stops driving that session forward in loops / skips further loop work in `tick`.
- Sets **`running`** to `False`.
- Calls **`_verify_system_version()`** so startup fails fast if system config version ≠ `Runtime.VERSION`.

---

## Version and registration

### `_verify_system_version(self)` *(private)*

- Loads **`Config.load("system")`** and reads optional **`version`**.
- If **`version`** is set and not equal to **`Runtime.VERSION`**, raises **`VersionMismatchError`** with a message naming both versions.
- If **`version`** is missing, no check is applied.

### `register_module(self, name: str, module)`

- Enforces strict naming via **`_validate_module_name`**: the register key must exactly match the module file stem.
  - Example: class imported from `modules/file_system.py` must be registered as `"file_system"`.
- Rejects duplicate runtime keys (same module name registered twice) with `NameConflictError`.
- Calls **`_verify_module_version(name, module)`** so module config version matches module code.
- Assigns **`module._runtime = self`** and **`module._name = name`** (intended for use by the base `Module` properties).
- Stores **`self.modules[name] = module`**.

### `_verify_module_version(self, name: str, module)` *(private)*

- If **`module`** has no **`VERSION`** attribute, returns immediately (no config version check).
- Otherwise loads **`Config.load(name)`**.
- If config is missing, raises `NameConflictError` (strict rule expects `config/{name}.json`).
- Reads optional **`version`**.
- If config **`version`** is set and differs from **`module.VERSION`**, raises **`VersionMismatchError`** naming the module and both versions.

### `auto_register_modules(self, package_name: str = "modules")`

- Discovers module files under the package directory (for example `modules/*.py`).
- For each file, imports it and selects the single class that:
  - is a subclass of `Module`,
  - is defined in that file’s module namespace (not re-exported).
- Instantiates and registers each discovered module using the **file stem** as the registration name.
- Raises `NameConflictError` for invalid discovery states (for example multiple `Module` subclasses in one file, duplicate names, or no discovered runtime modules).

This removes the need to manually import and register each module in entrypoints like `run_cli.py`.

---

## Sessions

### `create_session(self, session_id: str = None) -> Session`

- If **`session_id`** is `None`, generates one as **`str(int(time.time() * 1000))`** (millisecond timestamp string).
- Constructs **`Session(session_id)`** and stores it in **`self.sessions[session_id]`**.
- For **each** registered module, if the module implements the hook, calls **`module.on_session_start(session)`** (see [Module base class](module-base-class.md)).
- Returns the new **`Session`**.

### `get_session(self, session_id: str) -> Session | None`

- Returns **`self.sessions.get(session_id)`** — the session or `None` if unknown.

### `broadcast(self, message: str, session_id: str = None)`

- If **`session_id`** is set: resolves the session; if it exists, **`session.append_log(f"[BROADCAST] {message}")`**.
- If **`session_id`** is `None`: appends the same prefixed message to **every** session’s log.

---

## Loops and ticks

### `newloop(self, session: Session)`

- Increments **`session.loop_count`** and resets **`session.tick_count`** to `0`.
- Appends a log line marking the start of loop **`session.loop_count` / `self._max_loops`** for **`session.id`**.
- If **`session.loop_count > self._max_loops`**, logs that max loops was reached and **returns** without calling `on_loop`.
- Otherwise, for each registered module that defines **`on_loop`**, calls **`module.on_loop(session)`**.

### `tick(self)`

- Iterates over **`list(self.sessions.values())`** (a snapshot copy so mutations during iteration are safer).
- Skips sessions where **`session.loop_count > self._max_loops`** (no further tick processing for that session in this design).
- Increments **`session.tick_count`** by 1.
- For each registered module with **`on_tick`**, calls **`module.on_tick(session)`**.
- If **`session.tick_count >= self._ticks_per_loop`**, appends a log that the tick limit was reached for the current loop (again, does not by itself start a new loop).

### `run(self, interval: float = 0.1)`

- Sets **`self.running = True`**.
- Enters a **`while self.running:`** loop: calls **`self.tick()`** then **`time.sleep(interval)`**.
- This is a blocking “main loop” suitable for long-running processes; stop it with **`stop()`**.

### `stop(self)`

- Sets **`self.running = False`**, which causes **`run()`**’s loop to exit on the next iteration.

---

## Internal events (callbacks)

### `register_callback(self, event: str, callback: Callable)`

- Ensures **`self._callbacks[event]`** exists as a list, then **`append(callback)`**.
- Multiple callbacks per event are allowed; order is registration order.

### `emit(self, event: str, *args, **kwargs)`

- If **`event`** exists in **`self._callbacks`**, invokes each registered **`callback(*args, **kwargs)`** in order.
- Unknown events are ignored (no error).

---

## Relationships (summary)

- **Modules** are invoked from **`create_session`**, **`newloop`**, and **`tick`** via optional hooks.
- **Sessions** store per-run state; the runtime only appends logs and adjusts loop/tick counters in **`newloop`** / **`tick`**.
- **Config** supplies **`system.runtime`** knobs and version fields used by **`_verify_system_version`** and **`_verify_module_version`**.
