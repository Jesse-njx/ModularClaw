# Session (`session.py`)

A **`Session`** is the per-run state container: **context** (ordered entries), **status** keyed by module, **logs**, **claimed regions** over context indices, and counters / flags for **loops** and **ticks**. It reads limits from **`Config.get("system", "session", {})`** and **`Config.get("system", "runtime", {})`**.

Class constant:

- **`VERSION`** — Exposed in **`to_dict()`** for serialization or debugging.

## Constructor

### `__init__(self, id: str)`

- **`self.id`** — External identifier for this session.
- **`self.context`** — List of context entries (see **`add_context`** / **`update_region`**).
- **`self.status_list`** — Nested dict: module name → key → value.
- **`self.logs`** — List of timestamped strings (see **`append_log`**).
- **`self._claimed_regions`** — Maps context index → module name (see claim helpers).
- From config **`system.session`**: **`max_context_items`** (default `1000`), **`max_logs`** (default `5000`).
- **`self.loop_count`**, **`self.tick_count`** — Initialized to `0`; typically updated by **`Runtime`**.
- **`self._loop_limit`** — From **`system.runtime.max_loops`** (default `5`); reserved for callers that want a session-side notion of loop cap (the runtime uses its own `_max_loops` as well).
- **`self.awaiting_user_input`** — When true (after a `user_input` tool), the **CLI** keeps `"Ready to send"` as `"pending"` until the user types another line.

---

## Context

### `add_context(self, content_type: str, data: str, module: str = None, claimed_since: int = None, info: dict = None, label: str = None) -> int`

- If **`len(self.context) >= self._max_context`**, removes the **oldest** entry with **`pop(0)`** (FIFO eviction).
- Appends a dict: **`type`**, **`data`**, and optionally **`label`**.
- If **`module`** is provided, also sets **`module`**, **`claimedSince`**, and **`info`** (default empty dict). This ties the entry to a claiming module and optional metadata.
- Returns the **index** of the new entry in **`self.context`** (`len - 1`).

### `get_context(self) -> list`

- Returns **`self.context`** (the live list; mutating the return value mutates session state).

### `update_region(self, region_index: int, new_data: str, new_type: str = None)`

- If **`0 <= region_index < len(self.context)`**, sets **`context[region_index]["data"] = new_data`**.
- If **`new_type`** is truthy, also sets **`context[region_index]["type"] = new_type`**.

---

## Status

### `set_status(self, module: str, key: str, value)`

- Ensures **`self.status_list[module]`** exists, then sets **`status_list[module][key] = value`**.

### `get_status(self, module: str, key: str = None)`

- If **`module`** not in **`status_list`**, returns **`None`**.
- If **`key`** is **`None`**, returns the **entire** status dict for that module.
- Otherwise returns **`status_list[module].get(key)`** (value or implicit `None` if key missing).

### `get_all_statuses(self) -> dict`

- Returns **`self.status_list`** (live nested dict).

---

## Logs

### `append_log(self, message: str)`

- If **`len(self.logs) >= self._max_logs`**, drops the oldest log with **`pop(0)`**.
- Appends **`f"[{int(time.time())}] {message}"`** (Unix seconds as integer in the prefix).

---

## Region claims

Regions are **indices** into **`self.context`**. **`_claimed_regions`** maps index → owning module name.

### `claim_region(self, region_index: int, module: str)`

- Sets **`self._claimed_regions[region_index] = module`**.

### `release_region(self, region_index: int)`

- If **`region_index`** is present, **`del self._claimed_regions[region_index]`**.

### `is_claimed(self, region_index: int) -> bool`

- Returns whether **`region_index`** is in **`_claimed_regions`**.

### `get_claimant(self, region_index: int)`

- Returns **`self._claimed_regions.get(region_index)`** (module name or `None`).

### `mark_claimed_region_finished(self, region_index: int, module: str)`

- If **`region_index`** is claimed **and** the claimant equals **`module`**:
  - Removes **`claimedSince`** and **`module`** keys from **`self.context[region_index]`** if present (entry remains; only those keys are cleared).
  - Calls **`release_region(region_index)`**.

---

## Serialization

### `to_dict(self) -> dict`

- Defines a nested **`convert(obj)`** function:
  - Dicts → recurse values.
  - Lists → recurse elements.
  - Objects with **`__dict__`** → **`str(obj)`** (string fallback, not a deep structure).
  - Scalars → returned as-is.
- Returns a dict with: **`id`**, **`version`**, **`context`**, **`status_list`** (via **`convert`**), **`logs`**, **`claimed_regions`**, **`loop_count`**, **`tick_count`**, **`awaiting_user_input`**.

Use this for snapshots, APIs, or debugging; it is not guaranteed to round-trip arbitrary objects inside **`status_list`** beyond the **`convert`** rules above.
