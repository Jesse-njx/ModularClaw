# Sender module (`modules/sender.py`)

`Sender` is the module that talks to the chat API (or returns a stub string when no API key is set). It does **not** run on a fixed timer alone: on each tick it sends **as soon as** every *other* registered module reports **`Ready to send` == `"ready"`** and its own **`pending_confirmation`** latch is true. There is no separate “arm” flag—readiness (including CLI and tool modules finishing) is the only gate.

This page explains how that gate works and **how your own module can cooperate with or block `Sender`**.

---

## Registration name matters

`Sender` looks up status using runtime module keys (`self.runtime.modules`). In the current runtime, names are strict: the key is expected to match the module file stem exactly (for example `file_system`).

When deciding whether to send, `Sender` loops over **`self.runtime.modules`** and, for every entry **except itself**, reads:

```text
session.get_status(<that module's name>, "Ready to send")
```

So your module should use **`self.name`** (after registration) when writing status.

---

## Dispatch gate: `Ready to send` and `session.awaiting_user_input`

### 1. `Ready to send` status (per module)

For every other module in the runtime, `Sender` requires:

```text
session.get_status(module_name, "Ready to send") == "ready"
```

If any module has another value (e.g. `"pending"`) or missing status, `all_ready` stays false and no API call runs.

**How to interfere:**

- **Block the send:** `session.set_status(self.name, "Ready to send", "pending")` (or any string other than `"ready"`).
- **Allow the send (for your module):** `session.set_status(self.name, "Ready to send", "ready")`.

You can use additional keys on the same module namespace (`set_status(self.name, "something_else", ...)`) for your own logic; they do not affect this gate unless you also break `"Ready to send"`.

**CLI** in particular: it sets `"pending"` when there is no `UserText` in context yet (so the model is not called on an empty chat) and when **`session.awaiting_user_input`** is true after a **`user_input`** tool—until the user submits another line.

### 2. `session.awaiting_user_input` (session flag, enforced via CLI)

When the model emits a **`user_input`** tool call, `Sender` converts it to a `ToolResult` and sets **`awaiting_user_input = True`**. `Sender` does **not** read this flag directly; **`cli`** keeps **`Ready to send`** as **`"pending"`** until the user types, which clears the flag and sets **`"ready"`** again.

Instructions for the model live in **`config/sender.json`** → **`user_input_tool_prompt`**.

---

## `pending_confirmation` (internal latch)

On each `on_loop`, `Sender` sets `self.pending_confirmation = True` and broadcasts / emits an event:

```181:184:modules/sender.py
    def on_loop(self, session: Session):
        self.runtime.broadcast(f"[{self.name}] Waiting for confirmation to send", session.id)
        self.pending_confirmation = True
        self.runtime.emit("sender_waiting", session.id)
```

The actual send only runs when **`all_ready and self.pending_confirmation`**. After `_send_to_ai` starts, it sets `pending_confirmation = False` so another send is not triggered until the next `on_loop` arms it again.

**Practical effect:** Even if everything is `"ready"`, you need a **new loop** (from `runtime.newloop(session)`) to re-arm `pending_confirmation`. The last successful send calls `newloop` for you. On the **next tick(s)**, as soon as every module (including CLI) is **`"ready"`** again—e.g. after tools finish—`Sender` fires **without** a separate dispatch arm.

**How to interfere from outside:** Subscribe with `runtime.register_callback("sender_waiting", your_fn)`. Your callback receives `session_id`; you can use it to coordinate UI, logging, or to flip statuses before the next tick. You cannot set `pending_confirmation` from another module without subclassing or changing `Sender`—work through **`Ready to send`** (and CLI / `awaiting_user_input` for human gating) instead.

---

## What `Sender` reads from context

When building the API payload, `_send_to_ai` walks `session.get_context()` and only maps certain types:

| Context `type`        | Role in messages                          |
|-----------------------|--------------------------------------------|
| `Text`, `UserText`    | `user`                                     |
| `SystemText`          | `system`                                   |
| `ProtectedText`       | `assistant`                                |
| `ToolResult`          | `tool` (content = `data`)                |

Everything else is skipped for the HTTP `messages` list. So **how to interfere with content:** add or rewrite context items of those types before the send; avoid relying on custom types if you expect them in the model call unless you extend `Sender`.

---

## What `Sender` writes back

The model’s reply string is split into plain text and valid JSON segments (`_split_response_into_segments`). JSON segments are stored as `Text` with **`label="json"`**—which is what the executor watches for tool calls. Plain parts are `Text` without that label.

**How to interfere:** If another module should consume model output before execution, run **before** `Sender` in the same tick ordering won’t help—`Sender` produces the output. Instead, use another tick after the send, or hook `newloop`/callbacks, or post-process context in your module’s `on_tick` after the item appears.

---

## Configuration surface

Constructor pulls defaults from `Config.get("sender", "api", {})`: API URL, model, timeout, temperature, max tokens, and key. Missing key triggers simulation text instead of HTTP. **`user_input_tool_prompt`** (and **`system_prompt`**) are loaded from `config/sender.json`; **`system.system_prompt`** is merged into the main system string. The **`user_input`** tool is resolved inside `Sender`; **CLI** enforces the wait by **`Ready to send`**.

---

## Summary cheat sheet for module authors

| Goal                         | Typical action |
|-----------------------------|----------------|
| Hold until your work finishes | `set_status(self.name, "Ready to send", "pending")` until safe, then `"ready"` |
| Hold the whole pipeline     | `set_status(self.name, "Ready to send", "pending")` |
| Release your slot             | `set_status(self.name, "Ready to send", "ready")` |
| React when a loop arms send | `runtime.register_callback("sender_waiting", ...)` |
| Control what the model sees   | Add/update `Text` / `UserText` / `SystemText` / `ProtectedText` / `ToolResult` in context |

The executor and logger modules are two concrete examples of “interference” via `Ready to send` and claimed regions; see [`executor.md`](executor.md) for the beginner-oriented walkthrough of the executor side.
