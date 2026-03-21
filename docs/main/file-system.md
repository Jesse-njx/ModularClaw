# File system module (`modules/file_system.py`)

`FileSystem` provides a single tool interface (`edit_file`) for reading, writing, and managing files and directories under configurable roots.

## What it handles

- Watches context for JSON tool calls:
  - `{"type":"tool_call","name":"edit_file","arguments":{...}}`
- Supports actions (see `config/file_system.json` → `prompt` for full argument lists):
  - `read` — optional `max_chars`, `start_line` / `end_line`
  - `write`, `append`
  - `replace` — optional `occurrence` (default 1), `replace_all`, optional line range (`start_line` and `end_line` together)
  - `list` — directory listing; optional `recursive`, `include_hidden`, `files_only`, `dirs_only`
  - `glob` — pattern match under a base path; optional `recursive` (default true)
  - `search` — text search (uses `rg` when available); optional `max_results`
  - `rename` — move/rename; optional `overwrite`
  - `delete` — optional `recursive` for non-empty directories
  - `mkdir` — optional `recursive` (default true)
  - `metadata` — file/directory metadata

## Safety behavior (`path_policy` in `config/file_system.json`)

- **`write_scope`**: `"workspace"` (default) or `"project"`. Writes and most destructive operations must stay under the corresponding root (`_write_root` in code): workspace root vs project root.
- **`workspace_root`** / **`project_root`**: optional overrides; empty strings use defaults (current working directory for workspace, repo parent of `modules/` for project).
- **Relative paths**: resolved under `workspace_root` for read-style actions, under `_write_root` for writes.
- **Absolute paths**: allowed; if **`allow_read_all_system`** is `true`, reads can target any absolute path. Otherwise (and always for writes), the resolved path must lie under `_write_root`.
- **`read`** requires an existing file (not a directory); **`replace`** requires an existing file.
- **`read`** can truncate via `max_chars`.

## Session integration

- On session start, module prompt text is injected from `config/file_system.json`.
- On each handled tool call, the module:
  - claims the region,
  - executes the action,
  - replaces tool call with a `tool_result`,
  - does not set a separate dispatch flag; when done, it sets `"Ready to send"` to `"ready"` so `Sender` can run on the next tick if everyone else is ready,
  - updates `"Ready to send"` status based on pending tool calls.

## Config mapping

- Runtime module name: `file_system`
- Module file: `modules/file_system.py`
- Config file: `config/file_system.json`
