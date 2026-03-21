import json
import glob as glob_lib
import os
import shutil
import subprocess
import tempfile
from core import Module
from config_loader import Config
from session import Session


class FileSystem(Module):
    """File system tool module used by the runtime."""

    VERSION = "1.0.0"

    def __init__(self):
        super().__init__()
        fs_config = Config.get("file_system") or {}
        self._tool_prompt = fs_config.get("prompt", "")
        policy = fs_config.get("path_policy", {})
        self.write_scope = policy.get("write_scope", "workspace")
        self.workspace_root = os.path.abspath(policy.get("workspace_root") or os.getcwd())
        self.project_root = os.path.abspath(
            policy.get("project_root")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self.allow_read_all_system = policy.get("allow_read_all_system", True)
        self._write_root = self.project_root if self.write_scope == "project" else self.workspace_root

    def on_session_start(self, session: Session):
        """Inject tool instructions into the session when a session starts."""
        if self._tool_prompt:
            session.add_context("SystemText", self._tool_prompt)

    def on_tick(self, session: Session):
        """
        Look for JSON tool calls in context.
        If we find edit_file calls, process them.
        """
        context = session.get_context()

        for i, ctx in enumerate(context):
            if ctx.get("type") != "Text" or ctx.get("label") != "json":
                continue

            try:
                data = json.loads(ctx["data"])
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(data, dict):
                continue

            if data.get("type") == "tool_call" and data.get("name") == "edit_file":
                if session.is_claimed(i):
                    continue
                self._handle_edit_file(session, i, data)

        self._check_if_all_clear(session)

    def _handle_edit_file(self, session: Session, index: int, tool_call: dict):
        """Claim a tool call slot, run action, and write back tool_result."""
        arguments = tool_call.get("arguments", {})
        action = arguments.get("action")
        path = arguments.get("path")

        session.claim_region(index, self.name)
        result = self._run_action(action, path, arguments)

        payload = {
            "type": "tool_result",
            "tool": "edit_file",
            "ok": result["ok"],
            "message": result["message"],
            "path": result["path"],
        }
        if "content" in result:
            payload["content"] = result["content"]

        session.update_region(index, json.dumps(payload), "ToolResult")
        session.mark_claimed_region_finished(index, self.name)
        session.append_log(f"[{self.name}] edit_file -> {result['message']}")

    def _run_action(self, action: str, path: str, arguments: dict) -> dict:
        """Execute read/write/append/replace and return a uniform result dictionary."""
        allowed_actions = {
            "read",
            "write",
            "append",
            "replace",
            "list",
            "glob",
            "search",
            "rename",
            "delete",
            "mkdir",
            "metadata",
        }
        if action not in allowed_actions:
            return self._error(path, "Invalid action.")

        write_actions = {"write", "append", "replace", "rename", "delete", "mkdir"}
        resolve_for_write = action in write_actions
        if action in {"search", "list", "glob", "metadata", "read"} and not path:
            path = "."
        abs_path = self._resolve_path(path, for_write=resolve_for_write)
        if not abs_path:
            if resolve_for_write:
                return self._error(path, f"Invalid path. Write actions must stay inside {self._write_root}.")
            return self._error(path, "Invalid path.")

        try:
            if action == "read":
                if not os.path.exists(abs_path):
                    return self._error(path, "File not found for read action.")
                if os.path.isdir(abs_path):
                    return self._error(path, "Path points to a directory, not a file.")

                max_chars = arguments.get("max_chars", 12000)
                if not isinstance(max_chars, int) or max_chars <= 0:
                    return self._error(path, "max_chars must be a positive integer.")

                with open(abs_path, "r", encoding="utf-8") as f:
                    content = f.read()

                start_line = arguments.get("start_line")
                end_line = arguments.get("end_line")
                if start_line is not None or end_line is not None:
                    if not isinstance(start_line, int) or start_line <= 0:
                        return self._error(path, "start_line must be a positive integer.")
                    if end_line is None:
                        end_line = start_line
                    if not isinstance(end_line, int) or end_line < start_line:
                        return self._error(path, "end_line must be >= start_line.")
                    lines = content.splitlines(keepends=True)
                    content = "".join(lines[start_line - 1:end_line])

                truncated = content[:max_chars]
                if len(content) > max_chars:
                    return self._ok(path, "File read (truncated).", content=truncated)
                return self._ok(path, "File read.", content=truncated)

            if action == "write":
                content = arguments.get("content")
                if content is None:
                    return self._error(path, "Missing content for write action.")
                self._atomic_write(abs_path, content)
                return self._ok(path, "File written.")

            if action == "append":
                content = arguments.get("content")
                if content is None:
                    return self._error(path, "Missing content for append action.")
                existing = ""
                if os.path.exists(abs_path):
                    if os.path.isdir(abs_path):
                        return self._error(path, "Path points to a directory, not a file.")
                    with open(abs_path, "r", encoding="utf-8") as f:
                        existing = f.read()
                self._atomic_write(abs_path, existing + content)
                return self._ok(path, "Content appended.")

            if action == "replace":
                old_text = arguments.get("old_text")
                new_text = arguments.get("new_text")
                if old_text is None or new_text is None:
                    return self._error(path, "replace needs old_text and new_text.")
                if not os.path.exists(abs_path):
                    return self._error(path, "File not found for replace action.")
                if os.path.isdir(abs_path):
                    return self._error(path, "Path points to a directory, not a file.")

                with open(abs_path, "r", encoding="utf-8") as f:
                    original = f.read()

                start_line = arguments.get("start_line")
                end_line = arguments.get("end_line")
                if (start_line is None) != (end_line is None):
                    return self._error(path, "replace line-range requires both start_line and end_line.")

                target_text = original
                prefix = ""
                suffix = ""
                if start_line is not None:
                    if not isinstance(start_line, int) or not isinstance(end_line, int) or start_line <= 0 or end_line < start_line:
                        return self._error(path, "Invalid start_line/end_line.")
                    lines = original.splitlines(keepends=True)
                    prefix = "".join(lines[:start_line - 1])
                    target_text = "".join(lines[start_line - 1:end_line])
                    suffix = "".join(lines[end_line:])

                occurrence = arguments.get("occurrence", 1)
                replace_all = bool(arguments.get("replace_all", False))
                if replace_all:
                    count_to_replace = target_text.count(old_text)
                    if count_to_replace == 0:
                        return self._error(path, "old_text not found in target range.")
                    replaced_text = target_text.replace(old_text, new_text)
                else:
                    if not isinstance(occurrence, int) or occurrence <= 0:
                        return self._error(path, "occurrence must be a positive integer.")
                    positions = []
                    start_idx = 0
                    while True:
                        idx = target_text.find(old_text, start_idx)
                        if idx == -1:
                            break
                        positions.append(idx)
                        start_idx = idx + len(old_text)
                    if len(positions) < occurrence:
                        return self._error(path, "Requested occurrence not found in target range.")
                    pos = positions[occurrence - 1]
                    replaced_text = (
                        target_text[:pos]
                        + new_text
                        + target_text[pos + len(old_text):]
                    )

                updated = prefix + replaced_text + suffix
                self._atomic_write(abs_path, updated)
                return self._ok(path, "Text replaced.")

            if action == "list":
                if not os.path.exists(abs_path):
                    return self._error(path, "Path not found for list action.")
                if not os.path.isdir(abs_path):
                    return self._error(path, "Path points to a file, not a directory.")
                recursive = bool(arguments.get("recursive", False))
                include_hidden = bool(arguments.get("include_hidden", False))
                files_only = bool(arguments.get("files_only", False))
                dirs_only = bool(arguments.get("dirs_only", False))
                if files_only and dirs_only:
                    return self._error(path, "Cannot set both files_only and dirs_only.")
                entries = []
                if recursive:
                    for root, dirs, files in os.walk(abs_path):
                        if not include_hidden:
                            dirs[:] = [d for d in dirs if not d.startswith(".")]
                        rel_root = os.path.relpath(root, abs_path)
                        if not dirs_only:
                            for f_name in files:
                                if include_hidden or not f_name.startswith("."):
                                    rel_path = os.path.join(rel_root, f_name) if rel_root != "." else f_name
                                    entries.append({"path": rel_path, "type": "file"})
                        if not files_only:
                            for d_name in dirs:
                                rel_path = os.path.join(rel_root, d_name) if rel_root != "." else d_name
                                entries.append({"path": rel_path, "type": "dir"})
                else:
                    with os.scandir(abs_path) as scan:
                        for entry in scan:
                            if not include_hidden and entry.name.startswith("."):
                                continue
                            if files_only and not entry.is_file():
                                continue
                            if dirs_only and not entry.is_dir():
                                continue
                            entries.append({"path": entry.name, "type": "dir" if entry.is_dir() else "file"})
                return self._ok(path, "Directory listed.", content=json.dumps(entries))

            if action == "glob":
                pattern = arguments.get("pattern")
                if not pattern or not isinstance(pattern, str):
                    return self._error(path, "glob needs a string pattern.")
                recursive = bool(arguments.get("recursive", True))
                base_dir = abs_path if os.path.isdir(abs_path) else os.path.dirname(abs_path)
                matches = glob_lib.glob(os.path.join(base_dir, pattern), recursive=recursive)
                rel_matches = []
                for match in matches:
                    rel_matches.append(os.path.relpath(os.path.abspath(match), base_dir))
                return self._ok(path, "Glob completed.", content=json.dumps(rel_matches))

            if action == "search":
                query = arguments.get("query")
                if not query or not isinstance(query, str):
                    return self._error(path, "search needs a string query.")
                target_dir = abs_path
                if os.path.isfile(target_dir):
                    target_dir = os.path.dirname(target_dir)
                if not os.path.isdir(target_dir):
                    return self._error(path, "Search target must be a directory or file path.")
                max_results = arguments.get("max_results", 200)
                if not isinstance(max_results, int) or max_results <= 0:
                    return self._error(path, "max_results must be a positive integer.")
                rg_path = shutil.which("rg")
                results = []
                if rg_path:
                    cmd = [
                        rg_path,
                        "--line-number",
                        "--with-filename",
                        "--color",
                        "never",
                        query,
                        target_dir,
                    ]
                    proc = subprocess.run(cmd, capture_output=True, text=True)
                    if proc.returncode not in (0, 1):
                        return self._error(path, f"rg failed: {proc.stderr.strip()}")
                    for line in proc.stdout.splitlines():
                        parts = line.split(":", 2)
                        if len(parts) != 3:
                            continue
                        file_path, line_number, line_text = parts
                        results.append(
                            {
                                "path": os.path.relpath(file_path, target_dir),
                                "line": int(line_number),
                                "text": line_text,
                            }
                        )
                        if len(results) >= max_results:
                            break
                else:
                    for root, _, files in os.walk(target_dir):
                        for f_name in files:
                            file_path = os.path.join(root, f_name)
                            try:
                                with open(file_path, "r", encoding="utf-8") as f:
                                    for idx, line_text in enumerate(f, start=1):
                                        if query in line_text:
                                            results.append(
                                                {
                                                    "path": os.path.relpath(file_path, target_dir),
                                                    "line": idx,
                                                    "text": line_text.rstrip("\n"),
                                                }
                                            )
                                            if len(results) >= max_results:
                                                break
                                if len(results) >= max_results:
                                    break
                            except Exception:
                                continue
                        if len(results) >= max_results:
                            break
                return self._ok(path, "Search completed.", content=json.dumps(results))

            if action == "rename":
                new_path = arguments.get("new_path")
                if not new_path or not isinstance(new_path, str):
                    return self._error(path, "rename needs new_path.")
                abs_new_path = self._resolve_path(new_path, for_write=True)
                if not abs_new_path:
                    return self._error(new_path, f"Invalid destination path. Must stay inside {self._write_root}.")
                if not os.path.exists(abs_path):
                    return self._error(path, "Source path not found for rename.")
                overwrite = bool(arguments.get("overwrite", False))
                if os.path.exists(abs_new_path) and not overwrite:
                    return self._error(new_path, "Destination already exists. Set overwrite=true to replace it.")
                self._ensure_parent_dir(abs_new_path)
                os.replace(abs_path, abs_new_path)
                return self._ok(path, "Path renamed/moved.")

            if action == "delete":
                if not os.path.exists(abs_path):
                    return self._error(path, "Path not found for delete action.")
                recursive = bool(arguments.get("recursive", False))
                if os.path.isdir(abs_path):
                    if recursive:
                        shutil.rmtree(abs_path)
                    else:
                        os.rmdir(abs_path)
                else:
                    os.remove(abs_path)
                return self._ok(path, "Path deleted.")

            if action == "mkdir":
                recursive = bool(arguments.get("recursive", True))
                if os.path.exists(abs_path):
                    return self._ok(path, "Directory already exists.")
                if recursive:
                    os.makedirs(abs_path, exist_ok=True)
                else:
                    os.mkdir(abs_path)
                return self._ok(path, "Directory created.")

            if action == "metadata":
                exists = os.path.exists(abs_path)
                payload = {"exists": exists}
                if exists:
                    st = os.stat(abs_path)
                    payload.update(
                        {
                            "is_file": os.path.isfile(abs_path),
                            "is_dir": os.path.isdir(abs_path),
                            "size": st.st_size,
                            "mtime": st.st_mtime,
                            "ctime": st.st_ctime,
                        }
                    )
                return self._ok(path, "Metadata retrieved.", content=json.dumps(payload))
        except Exception as e:
            return self._error(path, f"Edit failed: {e}")

    def _resolve_path(self, path: str, for_write: bool = True):
        """Convert a user path to an absolute path with write-scope controls."""
        if not path or not isinstance(path, str):
            return None

        expanded = os.path.expanduser(path)
        if os.path.isabs(expanded):
            candidate = os.path.abspath(expanded)
        else:
            base = self._write_root if for_write else self.workspace_root
            candidate = os.path.abspath(os.path.join(base, expanded))

        if (not for_write) and self.allow_read_all_system:
            return candidate

        root = self._write_root
        if candidate == root or candidate.startswith(root + os.sep):
            return candidate
        return None

    def _ensure_parent_dir(self, file_path: str):
        """Create parent directory for a file path if it doesn't exist."""
        parent = os.path.dirname(file_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _atomic_write(self, file_path: str, content: str):
        """Write a file atomically with best-effort rollback on failure."""
        self._ensure_parent_dir(file_path)
        parent = os.path.dirname(file_path) or "."
        backup_path = None
        tmp_path = None
        try:
            if os.path.exists(file_path):
                backup_path = f"{file_path}.bak"
                shutil.copy2(file_path, backup_path)

            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=parent, delete=False) as tmp:
                tmp.write(content)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = tmp.name

            os.replace(tmp_path, file_path)
            if backup_path and os.path.exists(backup_path):
                os.remove(backup_path)
        except Exception:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            if backup_path and os.path.exists(backup_path):
                shutil.copy2(backup_path, file_path)
                os.remove(backup_path)
            raise

    def _ok(self, path: str, message: str, content: str = None):
        """Success payload helper."""
        payload = {"ok": True, "message": message, "path": path}
        if content is not None:
            payload["content"] = content
        return payload

    def _error(self, path: str, message: str):
        """Error payload helper."""
        return {"ok": False, "message": message, "path": path}

    def _check_if_all_clear(self, session: Session):
        """Set module readiness based on whether edit_file calls are still pending."""
        context = session.get_context()
        has_pending_tool = False

        for ctx in context:
            if ctx.get("type") == "Text" and ctx.get("label") == "json":
                try:
                    data = json.loads(ctx["data"])
                    if isinstance(data, dict) and data.get("type") == "tool_call" and data.get("name") == "edit_file":
                        has_pending_tool = True
                except (json.JSONDecodeError, TypeError):
                    pass

        if has_pending_tool:
            session.set_status(self.name, "Ready to send", "pending")
        else:
            session.set_status(self.name, "Ready to send", "ready")
