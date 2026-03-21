# ModularClaw

For a **core-only** reference (every function on `Runtime`, `Module`, `Session`, and `Config`, with no module catalog), see **[`docs/main/core/README.md`](main/core/README.md)**.

Module interaction notes:

- **[`docs/main/sender.md`](main/sender.md)** — how `Sender` decides to call the API and how other modules block or allow it.
- **[`docs/main/executor.md`](main/executor.md)** — beginner-friendly: what `Executor` does and how it keeps `Sender` from sending too early.

ModularClaw is a modular, session-driven agent runtime. It lets you wire independent modules (CLI input, LLM calling, command execution, logging, and web status UI) into a loop where each module contributes work until the system is ready to send the next AI request.

## What This Tool Does

At a high level, this project is an agent orchestration framework:

- Accepts user input from a CLI module
- Stores conversation and tool data in a shared `Session` context
- Lets an LLM module (`Sender`) read the context and produce responses
- Lets an execution module (`Executor`) run shell commands requested via structured tool calls
- Lets a file system module (`FileSystem`, registered as `file_system`) handle `edit_file` tool calls (read/write, search, list, and related operations within configured path policy)
- Feeds tool results back into context so the LLM can continue reasoning
- Exposes live status, context, and logs over a small web dashboard (`Web`)

In short: **ModularClaw coordinates multi-step AI + tool workflows by passing shared session state through pluggable modules on every tick.**

## Architecture

```
User Input (CLI)
    -> Session Context
    -> Modules run on each tick (Logger / Executor / Web / Sender)
    -> Sender calls AI when all modules are ready
    -> AI output returns to Session Context
    -> New loop starts
```

## Core Concepts

### Runtime

`Runtime` owns:

- Registered modules
- Active sessions
- Tick/loop flow (`tick()`, `newloop()`, `run()`)
- Version checks between code and config

### Session

`Session` is the shared state modules read/write:

- `context`: ordered items (`Text`, `ToolResult`, etc.)
- `status_list`: per-module readiness/status flags
- `logs`: bounded history of log entries
- region claims: guards for safe in-place context updates
- counters: `loop_count`, `tick_count`

### Module

Each module extends `Module` and can implement:

- `on_loop(session)`: called when a new loop starts
- `on_tick(session)`: called every runtime tick

## Included Modules

### `CLI`

- Collects terminal input
- Appends user text to session context
- Marks `user_message_ready` and module readiness

### `Sender`

- Waits for all other modules to report `Ready to send = ready`
- Builds LLM message payload from session context
- Calls configured chat-completions endpoint
- Appends AI response back into context and starts a new loop

If no API key is configured, it returns a simulated response.

### `Executor`

- Scans context for JSON tool calls:

```json
{
  "type": "tool_call",
  "name": "execute_command",
  "arguments": {"command": "ls -la"}
}
```

- Claims that context region
- Executes the command asynchronously
- Rewrites the item as a `ToolResult` payload
- Releases the claim and updates readiness

### `FileSystem` (`file_system`)

- Scans context for JSON tool calls:

```json
{
  "type": "tool_call",
  "name": "edit_file",
  "arguments": {"action": "write", "path": "notes.txt", "content": "Hello"}
}
```

- Supports actions such as `read`, `write`, `append`, `replace`, `list`, `glob`, `search`, `rename`, `delete`, `mkdir`, and `metadata` (see [main/file-system.md](main/file-system.md))
- Enforces `path_policy` from `config/file_system.json` (`write_scope`, roots, `allow_read_all_system`)
- Returns a structured `tool_result` with `ok`, `message`, and `path` (and `content` when applicable)

### `Logger`

- Tracks whether there is pending claimed work
- Updates `Ready to send` status accordingly

### `Web`

- Hosts a minimal HTTP status page (`/` and `/session/<id>`)
- Displays module statuses, context preview, and recent logs

## Configuration

All config lives in `config/` and is loaded by module name with version validation.

### `config/system.json`

- `runtime.tick_interval`: default sleep interval in run loops
- `runtime.max_sessions`: max concurrent sessions (informational currently)
- `runtime.ticks_per_loop`: threshold used for loop tick logging
- `runtime.max_loops`: hard cap on session loops
- `session.max_context_items`: bounded context size
- `session.max_logs`: bounded log size

### Module configs

- `config/cli.json`
- `config/sender.json`
- `config/executor.json`
- `config/logger.json`
- `config/file_system.json`
- `config/web.json`

Each includes a `version` that must match the module `VERSION` constant.

## Quick Start

Run the demo CLI runtime:

```bash
python -m modules.cli
```

Then:

1. Type a prompt in the terminal
2. Let modules process ticks in the background
3. Open the status page at `http://localhost:8080`
4. Type `exit` / `quit` / `q` to stop

## Programmatic Usage

```python
from core import Runtime
from modules import Sender, Executor, Logger, CLI, Web

runtime = Runtime()
runtime.register_module("logger", Logger())
runtime.register_module("sender", Sender())
runtime.register_module("executor", Executor())
runtime.register_module("cli", CLI())
runtime.register_module("web", Web())

session = runtime.create_session("session-001")
runtime.newloop(session)
```

## Extending With Custom Modules

```python
from core import Module
from session import Session

class MyModule(Module):
    VERSION = "1.0.0"

    def on_loop(self, session: Session):
        pass

    def on_tick(self, session: Session):
        session.set_status(self.name, "Ready to send", "ready")
```

Register it with:

```python
runtime.register_module("mymodule", MyModule())
```

Add `config/mymodule.json` (file stem matches the runtime registration name) with a matching `version`.

## API Reference

### Runtime

- `create_session(session_id=None)`
- `get_session(session_id)`
- `register_module(name, module)`
- `newloop(session)`
- `tick()`
- `run(interval=0.1)`
- `stop()`
- `broadcast(message, session_id=None)`
- `register_callback(event, callback)`
- `emit(event, *args, **kwargs)`

### Session

- `add_context(content_type, data, module=None, claimed_since=None, info=None, label=None)` — optional `label` (e.g. `"json"`) tags context entries for modules such as the executor.
- `get_context()`
- `set_status(module, key, value)`
- `get_status(module, key=None)`
- `get_all_statuses()`
- `append_log(message)`
- `claim_region(region_index, module)`
- `release_region(region_index)`
- `is_claimed(region_index)`
- `get_claimant(region_index)`
- `update_region(region_index, new_data, new_type=None)`
- `mark_claimed_region_finished(region_index, module)`
