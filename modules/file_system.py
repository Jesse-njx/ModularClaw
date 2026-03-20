import json
import os
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
            session.add_context("Text", self._tool_prompt)

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
        session.set_need_loop(True)
        session.append_log(f"[{self.name}] edit_file -> {result['message']}")

    def _run_action(self, action: str, path: str, arguments: dict) -> dict:
        """Execute read/write/append/replace and return a uniform result dictionary."""
        if action not in ("read", "write", "append", "replace"):
            return self._error(path, "Invalid action. Use read, write, append, or replace.")

        write_actions = {"write", "append", "replace"}
        resolve_for_write = action in write_actions
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
                truncated = content[:max_chars]
                if len(content) > max_chars:
                    return self._ok(path, "File read (truncated).", content=truncated)
                return self._ok(path, "File read.", content=truncated)

            if action == "write":
                content = arguments.get("content")
                if content is None:
                    return self._error(path, "Missing content for write action.")
                self._ensure_parent_dir(abs_path)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(content)
                return self._ok(path, "File written.")

            if action == "append":
                content = arguments.get("content")
                if content is None:
                    return self._error(path, "Missing content for append action.")
                self._ensure_parent_dir(abs_path)
                with open(abs_path, "a", encoding="utf-8") as f:
                    f.write(content)
                return self._ok(path, "Content appended.")

            old_text = arguments.get("old_text")
            new_text = arguments.get("new_text")
            if old_text is None or new_text is None:
                return self._error(path, "replace needs old_text and new_text.")
            if not os.path.exists(abs_path):
                return self._error(path, "File not found for replace action.")

            with open(abs_path, "r", encoding="utf-8") as f:
                original = f.read()

            if old_text not in original:
                return self._error(path, "old_text not found in file.")

            updated = original.replace(old_text, new_text, 1)
            with open(abs_path, "w", encoding="utf-8") as f:
                f.write(updated)
            return self._ok(path, "Text replaced.")
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
