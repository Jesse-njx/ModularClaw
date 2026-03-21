# Memory module (`modules/memory.py`)

`Memory` persists short text snippets the model saves via tool calls. Data is stored as JSON under the workspace (default: `workspace/Memory/memories.json`, configurable).

## What it handles

- Watches context for JSON tool calls on items with **`type` == `Text`** and **`label` == `"json"`** (same convention as `Executor` / `FileSystem` for model-emitted tools):

```json
{"type": "tool_call", "name": "save_memory", "arguments": {"content": "...", "tags": [], "importance": 3, "metadata": {}}}
```

```json
{"type": "tool_call", "name": "search_memory", "arguments": {"query": "substring", "tags": [], "max_results": 10}}
```

- **`save_memory`**: requires **`content`** (string). Optional **`tags`** (list), **`importance`** (default `3`), **`metadata`** (object). Appends a record with auto-increment **`id`** and ISO **`timestamp`**, then writes the file.
- **`search_memory`**: if both **`query`** and **`tags`** are empty/omitted, returns the most recent memories (up to **`max_results`**, clamped `1`–`500`). Otherwise filters by tag subset and/or case-insensitive substring match in **`content`**, sorted by **`importance`** (desc) then **`timestamp`**.

## Session integration

- On **`on_session_start`**, if **`config/memory.json`** → **`prompt`** is non-empty, it is appended as **`SystemText`** so the model sees tool instructions.
- On each tick: finds matching tool calls, **`claim_region`**, replaces the JSON with a **`ToolResult`** payload (`ok`, `message`, plus result fields), then **`mark_claimed_region_finished`**.
- **`Ready to send`**: **`pending`** while any unhandled `save_memory` / `search_memory` tool call remains in context; **`ready`** when none are pending—so **`Sender`** waits until memory work finishes, like other tool modules.

## Config mapping

- Runtime module name: **`memory`**
- Module file: **`modules/memory.py`**
- Config file: **`config/memory.json`**

### Notable config keys

- **`prompt`**: injected as system text at session start (tool schema and rules for the model).
- **`path_policy.workspace_root`**: base directory for storage; empty string uses the process current working directory.
- **`storage.relative_dir`** / **`storage.memories_file`**: directory under workspace root and JSON filename (defaults: `Memory`, `memories.json`).
- **`supported_tools`**: documentation for humans/tools.json-style consumers; execution logic is fixed to `save_memory` and `search_memory`.
