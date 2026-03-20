# File system module (`modules/file_system.py`)

`FileSystem` provides a single tool interface (`edit_file`) that can both read and modify files within the project workspace.

## What it handles

- Watches context for JSON tool calls:
  - `{"type":"tool_call","name":"edit_file","arguments":{...}}`
- Supports actions:
  - `read` (with optional `max_chars`)
  - `write`
  - `append`
  - `replace` (first match only)

## Safety behavior

- Paths are resolved relative to project root.
- Any path that escapes the workspace is rejected.
- `read` and `replace` require an existing file.
- `read` rejects directories and can truncate output by `max_chars`.

## Session integration

- On session start, module prompt text is injected from `config/file_system.json`.
- On each handled tool call, the module:
  - claims the region,
  - executes the action,
  - replaces tool call with a `tool_result`,
  - marks loop needed (`set_need_loop(True)`),
  - updates `"Ready to send"` status based on pending tool calls.

## Config mapping

- Runtime module name: `file_system`
- Module file: `modules/file_system.py`
- Config file: `config/file_system.json`
