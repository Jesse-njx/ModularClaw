import subprocess
import json
import threading
import os
import re
import shlex
from core import Module
from config_loader import Config
from session import Session


class Executor(Module):
    VERSION = "1.0.0"

    def __init__(self):
        super().__init__()
        exec_config = Config.get("executor", "execution", {})

        self.timeout = exec_config.get("timeout_seconds", 60)
        self.shell = exec_config.get("shell", "bash")
        self.enforce_command_policy = exec_config.get("enforce_command_policy", True)
        self.read_access_all_system = exec_config.get("read_access_all_system", True)
        self.write_scope = exec_config.get("write_scope", "workspace")
        self.workspace_root = os.path.abspath(exec_config.get("workspace_root") or os.getcwd())
        self.project_root = os.path.abspath(
            exec_config.get("project_root")
            or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        self._write_root = self.project_root if self.write_scope == "project" else self.workspace_root
        self.running_processes = {}
        self._tool_prompt = (Config.get("executor") or {}).get("prompt") or ""

    def on_tick(self, session: Session):
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
                
            if data.get("type") == "tool_call" and data.get("name") == "execute_command":
                if session.is_claimed(i):
                    continue
                    
                self._handle_execute_command(session, i, data)

        self._check_finished_processes(session)
        self._check_if_all_clear(session)
    
    def on_session_start(self, session: Session):
        if self._tool_prompt:
            session.add_context("SystemText", self._tool_prompt)

    def _handle_execute_command(self, session: Session, index: int, tool_call: dict):
        command = tool_call.get("arguments", {}).get("command")
        if not command:
            return

        allowed, reason = self._validate_command_policy(command)
        if not allowed:
            session.claim_region(index, self.name)
            denied_data = json.dumps({
                "type": "tool_result",
                "tool": "execute_command",
                "output": reason,
                "returncode": -1
            })
            session.update_region(index, denied_data, "ToolResult")
            session.mark_claimed_region_finished(index, self.name)
            session.append_log(f"[{self.name}] Blocked command: {command} ({reason})")
            return

        session.claim_region(index, self.name)
        session.set_status(self.name, f"Running:{command}", {"started": True, "timestamp": session.get_status("_system", "timestamp") or 0})

        session.append_log(f"[{self.name}] Claimed region {index}, executing: {command}")

        def run_command():
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    cwd=self._write_root,
                    executable=self.shell
                )
                output = result.stdout if result.returncode == 0 else result.stderr
                self.running_processes[session.id, index] = {
                    "output": output,
                    "returncode": result.returncode
                }
            except subprocess.TimeoutExpired:
                self.running_processes[session.id, index] = {
                    "output": "Command timed out",
                    "returncode": -1
                }
            except Exception as e:
                self.running_processes[session.id, index] = {
                    "output": f"Error: {str(e)}",
                    "returncode": -1
                }

        thread = threading.Thread(target=run_command)
        thread.start()

    def _validate_command_policy(self, command: str):
        if not self.enforce_command_policy:
            return True, ""

        if not isinstance(command, str) or not command.strip():
            return False, "Empty command."

        blocked_patterns = [r"\bsudo\b", r"\bsu\b", r"\bmount\b", r"\bumount\b", r"\bmkfs\b"]
        for pattern in blocked_patterns:
            if re.search(pattern, command):
                return False, "Command contains blocked privileged/system operation."

        try:
            tokens = shlex.split(command)
        except ValueError:
            return False, "Command parsing failed."

        if not tokens:
            return False, "Empty command."

        # Split simple shell chains and validate each segment independently.
        separators = {"&&", "||", ";", "|"}
        segment = []
        segments = []
        for token in tokens:
            if token in separators:
                if segment:
                    segments.append(segment)
                    segment = []
            else:
                segment.append(token)
        if segment:
            segments.append(segment)

        for seg in segments:
            if not self._segment_allowed(seg):
                return False, f"Write operation is restricted to {self._write_root}"

        return True, ""

    def _segment_allowed(self, segment_tokens):
        if not segment_tokens:
            return True

        command_name = segment_tokens[0]
        write_commands = {
            "rm", "mv", "cp", "touch", "mkdir", "rmdir", "truncate", "tee", "sed", "perl"
        }
        read_commands = {
            "ls", "cat", "rg", "grep", "find", "head", "tail", "wc", "pwd", "which", "echo"
        }

        if command_name in read_commands and self.read_access_all_system:
            return True

        if command_name not in write_commands:
            return True

        paths = [p for p in self._extract_path_tokens(segment_tokens[1:]) if p]
        if not paths:
            return True

        if command_name == "cp":
            return self._is_within_write_root(paths[-1])
        if command_name == "mv":
            return all(self._is_within_write_root(p) for p in paths)

        return all(self._is_within_write_root(p) for p in paths)

    def _extract_path_tokens(self, tokens):
        candidates = []
        for token in tokens:
            if token.startswith("-"):
                continue
            if token in {"&&", "||", ";", "|"}:
                continue
            if token.startswith(("~", "/", ".", "..")) or "/" in token:
                candidates.append(token)
        return candidates

    def _is_within_write_root(self, path_token: str):
        expanded = os.path.expanduser(path_token)
        if os.path.isabs(expanded):
            candidate = os.path.abspath(expanded)
        else:
            candidate = os.path.abspath(os.path.join(self._write_root, expanded))

        if candidate == self._write_root:
            return True
        return candidate.startswith(self._write_root + os.sep)

    def _check_finished_processes(self, session: Session):
        finished = []
        for key, result in self.running_processes.items():
            session_id, index = key
            if session_id == session.id:
                context = session.get_context()
                if index < len(context):
                    new_data = json.dumps({
                        "type": "tool_result",
                        "tool": "execute_command",
                        "output": result["output"],
                        "returncode": result["returncode"]
                    })
                    session.update_region(index, new_data, "ToolResult")
                    session.mark_claimed_region_finished(index, self.name)
                    session.append_log(f"[{self.name}] Finished executing, updated region {index}")
                    finished.append(key)
        
        for key in finished:
            del self.running_processes[key]

    def _check_if_all_clear(self, session: Session):
        context = session.get_context()
        has_pending_tool = False
        
        for ctx in context:
            if ctx.get("type") == "Text" and ctx.get("label") == "json":
                try:
                    data = json.loads(ctx["data"])
                    if isinstance(data, dict) and data.get("type") == "tool_call" and data.get("name") == "execute_command":
                        has_pending_tool = True
                except (json.JSONDecodeError, TypeError):
                    pass
        
        session_has_running = any(k[0] == session.id for k in self.running_processes.keys())
        if (not has_pending_tool) and (not session_has_running):
            session.set_status(self.name, "Ready to send", "ready")
        else:
            session.set_status(self.name, "Ready to send", "pending")
