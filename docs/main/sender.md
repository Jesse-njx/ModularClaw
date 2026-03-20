# Sender module (`modules/sender.py`)

`Sender` is the module that talks to the chat API (or returns a stub string when no API key is set). It does **not** run on a fixed timer alone: it only attempts a send when the session asks for a new loop **and** every *other* registered module reports that it is safe to send.

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

## Two switches: `NeedLoop` and `Ready to send`

### 1. `session.set_need_loop(True)`

In `on_tick`, `Sender` **returns immediately** if `session.needs_loop()` is false:

```124:125:modules/sender.py
        if not session.needs_loop():
            return
```

So if nothing ever sets `NeedLoop` to true, `Sender` never even checks readiness. Typical pattern: when user input or downstream work should trigger an AI call, something calls `session.set_need_loop(True)` (see the CLI module after adding a user message).

After a successful send, `Sender` clears the flag:

```139:141:modules/sender.py
        # Consume the trigger before sending so future sends require fresh work.
        self.pending_confirmation = False
        session.set_need_loop(False)
```

**How to interfere:** To *prevent* sends until you are ready, leave `NeedLoop` false. To *allow* the readiness check to run, set it true when your module has finished whatever must happen before the model is called.

### 2. `Ready to send` status (per module)

For every other module in the runtime, `Sender` requires:

```text
session.get_status(module_name, "Ready to send") == "ready"
```

If any module has another value (e.g. `"pending"`) or missing status, `all_ready` stays false and no API call runs.

**How to interfere:**

- **Block the send:** `session.set_status(self.name, "Ready to send", "pending")` (or any string other than `"ready"`).
- **Allow the send (for your module):** `session.set_status(self.name, "Ready to send", "ready")`.

You can use additional keys on the same module namespace (`set_status(self.name, "something_else", ...)`) for your own logic; they do not affect this gate unless you also break `"Ready to send"`.

---

## `pending_confirmation` (internal latch)

On each `on_loop`, `Sender` sets `self.pending_confirmation = True` and broadcasts / emits an event:

```118:121:modules/sender.py
    def on_loop(self, session: Session):
        self.runtime.broadcast(f"[{self.name}] Waiting for confirmation to send", session.id)
        self.pending_confirmation = True
        self.runtime.emit("sender_waiting", session.id)
```

The actual send only runs when **`all_ready and self.pending_confirmation`**. After `_send_to_ai` starts, it sets `pending_confirmation = False` so another send is not triggered until the next `on_loop` arms it again.

**Practical effect:** Even if everything is `"ready"`, you need a **new loop** (from `runtime.newloop(session)`) to re-arm confirmation. Other modules normally drive that by calling `set_need_loop(True)` and having the runtime start a new loop; the last successful send already calls `newloop` for you.

**How to interfere from outside:** Subscribe with `runtime.register_callback("sender_waiting", your_fn)`. Your callback receives `session_id`; you can use it to coordinate UI, logging, or to flip statuses before the next tick. You cannot set `pending_confirmation` from another module without subclassing or changing `Sender`—work through **`NeedLoop`** and **`Ready to send`** instead.

---

## What `Sender` reads from context

When building the API payload, `_send_to_ai` walks `session.get_context()` and only maps certain types:

| Context `type`        | Role in messages                          |
|-----------------------|--------------------------------------------|
| `Text`                | `user`                                     |
| `ProtectedText`       | `assistant`                                |
| `ToolResult`          | `tool` (content = `data`)                |

Everything else is skipped for the HTTP `messages` list. So **how to interfere with content:** add or rewrite context items of those types before the send; avoid relying on custom types if you expect them in the model call unless you extend `Sender`.

---

## What `Sender` writes back

The model’s reply string is split into plain text and valid JSON segments (`_split_response_into_segments`). JSON segments are stored as `Text` with **`label="json"`**—which is what the executor watches for tool calls. Plain parts are `Text` without that label.

**How to interfere:** If another module should consume model output before execution, run **before** `Sender` in the same tick ordering won’t help—`Sender` produces the output. Instead, use another tick after the send, or hook `newloop`/callbacks, or post-process context in your module’s `on_tick` after the item appears.

---

## Configuration surface

Constructor pulls defaults from `Config.get("sender", "api", {})`: API URL, model, timeout, temperature, max tokens, and key. Missing key triggers simulation text instead of HTTP.

---

## Summary cheat sheet for module authors

| Goal                         | Typical action |
|-----------------------------|----------------|
| Let `Sender` consider sending | `session.set_need_loop(True)` |
| Hold the whole pipeline     | `set_status(self.name, "Ready to send", "pending")` |
| Release your slot             | `set_status(self.name, "Ready to send", "ready")` |
| React when a loop arms send | `runtime.register_callback("sender_waiting", ...)` |
| Control what the model sees   | Add/update `Text` / `ProtectedText` / `ToolResult` in context |

The executor and logger modules are two concrete examples of “interference” via `Ready to send` and claimed regions; see [`executor.md`](executor.md) for the beginner-oriented walkthrough of the executor side.
