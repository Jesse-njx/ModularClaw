# Config loader (`config_loader.py`)

Configuration is loaded from JSON files under the project’s **`config/`** directory (resolved relative to **`config_loader.py`**’s package location). The **`Runtime`** and **`Session`** read values through **`Config.get`**, and version fields in those files participate in startup validation.

---

## `Config`

Class-level cache and path:

- **`_configs`** — Dict mapping logical config **name** → parsed JSON **`dict`**. Populated by **`load`**.
- **`_config_dir`** — Absolute path to **`.../ModularClaw/config`**.

### `load(cls, name: str) -> dict`

- **`name`** — Logical key (e.g. **`"system"`**, **`"executor"`**). File matching is **strict and exact**: `Config.load("executor")` looks for `config/executor.json` (same case and spelling).
- If **`name`** is already in **`_configs`**, returns the cached dict (no disk read).
- Otherwise resolves **`config/{name}.json`** by exact stem match. If the file **exists**, reads JSON, stores in **`_configs[name]`**, and returns it.
- If the file **does not exist**, returns **`{}`** (and does not cache a miss in the shown code—subsequent calls will hit the filesystem again).

Before loading, the config loader indexes all config file stems and checks for case-only collisions. If two files differ only by case (for example `Sender.json` and `sender.json`), it raises `NameConflictError`.

### `get(cls, name: str, key: str = None, default=None)`

- Loads **`config = cls.load(name)`** (cached or from disk).
- If **`key`** is **`None`**, returns the **full** config dict for **`name`**.
- If **`key`** is set, returns **`config.get(key, default)`** — **`default`** is used when the key is missing or the file was absent (empty dict).

Typical usage in the core:

- **`Config.get("system", "runtime", {})`** — runtime tick/loop settings.
- **`Config.get("system", "session", {})`** — session size limits.
- **`Config.load("system")`** — full system dict for **`version`** checks.

---

## `VersionMismatchError`

### `class VersionMismatchError(Exception)`

- Empty subclass of **`Exception`**, used as a **distinct type** for version skew.
- Raised by **`Runtime._verify_system_version`** when **`system`** config **`version`** ≠ **`Runtime.VERSION`**.
- Raised by **`Runtime._verify_module_version`** when a module’s config **`version`** ≠ **`module.VERSION`** (when both are defined).

Callers may catch **`VersionMismatchError`** specifically to present configuration errors separate from other failures.

---

## `NameConflictError`

### `class NameConflictError(Exception)`

- Raised when config naming violates strict uniqueness rules (for example, two files with the same lowercase stem but different case).
- Also used by runtime registration paths for module/config naming conflicts.

---

## File naming convention

| `load(name)` argument | Expected file |
|------------------------|----------------|
| `"system"` | `config/system.json` |
| `"executor"` | `config/executor.json` |
| `"file_system"` | `config/file_system.json` |

There is no capitalization transform now. The module registration name, module filename stem, and config filename stem must match exactly.
