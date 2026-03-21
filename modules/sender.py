import json
import re
from core import Module
from config_loader import Config
from session import Session


def _next_json_delim(s: str, start: int) -> int:
    i = start
    while i < len(s):
        if s[i] in "{[":
            return i
        i += 1
    return -1


def _json_value_span(s: str, start: int) -> tuple[int, int] | None:
    """If s[start] opens a JSON object or array, return (start, end_exclusive) for balanced span."""
    if start >= len(s) or s[start] not in "{[":
        return None
    closer = "}" if s[start] == "{" else "]"
    stack = [closer]
    i = start + 1
    in_string = False
    escape = False
    while i < len(s) and stack:
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            i += 1
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or ch != stack[-1]:
                return None
            stack.pop()
        i += 1
    if stack:
        return None
    return (start, i)


def _split_response_into_segments(text: str) -> list[tuple[str, str]]:
    """Split LLM output into ('text', ...) and ('json', ...) segments. JSON is validated with json.loads."""
    if not text:
        return []
    n = len(text)
    i = 0
    out: list[tuple[str, str]] = []
    plain: list[str] = []

    def flush_plain():
        nonlocal plain
        if plain:
            merged = "".join(plain)
            if merged:
                out.append(("text", merged))
            plain = []

    while i < n:
        j = _next_json_delim(text, i)
        if j < 0:
            plain.append(text[i:])
            break
        plain.append(text[i:j])
        flush_plain()
        span = _json_value_span(text, j)
        if span is None:
            plain.append(text[j])
            i = j + 1
            continue
        start, end = span
        candidate = text[start:end]
        try:
            json.loads(candidate)
            out.append(("json", candidate))
            i = end
        except json.JSONDecodeError:
            plain.append(text[j])
            i = j + 1

    flush_plain()
    merged: list[tuple[str, str]] = []
    for kind, chunk in out:
        if kind == "text" and merged and merged[-1][0] == "text":
            merged[-1] = ("text", merged[-1][1] + chunk)
        else:
            merged.append((kind, chunk))
    return merged


def _strip_markdown_json_fences(text: str) -> str:
    """Unwrap ``` / ```json fenced blocks so balanced `{...}` scanners see tool JSON."""

    def repl(m: re.Match) -> str:
        return "\n" + m.group(1).strip() + "\n"

    return re.sub(r"```(?:json)?\s*\r?\n?([\s\S]*?)```", repl, text, flags=re.IGNORECASE)


def _fallback_tool_call_json_spans(text: str) -> list[str]:
    """If the model buried tool JSON in prose or fences confused the main splitter, recover tool_call objects."""
    known = frozenset({"edit_file", "execute_command", "user_input"})
    found: list[str] = []
    i = 0
    while i < len(text):
        j = text.find("{", i)
        if j < 0:
            break
        span = _json_value_span(text, j)
        if span is None:
            i = j + 1
            continue
        start, end = span
        candidate = text[start:end]
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            i = j + 1
            continue
        if (
            isinstance(data, dict)
            and data.get("type") == "tool_call"
            and data.get("name") in known
        ):
            found.append(candidate)
            i = end
        else:
            i = j + 1
    return found


def _segment_model_response(raw: str) -> list[tuple[str, str]]:
    """Split assistant output into text vs executable tool JSON (with fence + recovery passes)."""
    normalized = _strip_markdown_json_fences(raw)
    segments = _split_response_into_segments(normalized)
    if not any(kind == "json" for kind, _ in segments):
        for blob in _fallback_tool_call_json_spans(normalized):
            segments.append(("json", blob))
    return segments


class Sender(Module):
    VERSION = "1.0.0"

    def __init__(self, api_key: str = None, api_url: str = None, model: str = None):
        super().__init__()
        api_config = Config.get("sender", "api", {})
        
        self.api_key = api_key or api_config.get("api_key") or api_config.get("key")
        self.api_url = api_url or api_config.get("url", "https://api.z.ai/api/paas/v4/chat/completions")
        self.model = model or api_config.get("model", "glm-5")
        self.timeout = api_config.get("timeout", 30)
        self.temperature = api_config.get("temperature", 0.7)
        self.max_tokens = api_config.get("max_tokens", 2048)
        sender_sp = (Config.get("sender", "system_prompt", "") or "").strip()
        system_sp = (Config.get("system", "system_prompt", "") or "").strip()
        self.system_prompt = "\n\n".join(p for p in (sender_sp, system_sp) if p)
        self._user_input_tool_prompt = (Config.get("sender", "user_input_tool_prompt", "") or "").strip()

        self.pending_confirmation = False

    def on_session_start(self, session: Session):
        if self.system_prompt:
            session.add_context("SystemText", self.system_prompt)
            session.append_log(f"[{self.name}] Added system prompt to session context")
        if self._user_input_tool_prompt:
            session.add_context("SystemText", self._user_input_tool_prompt)
            session.append_log(f"[{self.name}] Added user_input tool instructions to session context")

    def on_loop(self, session: Session):
        self.runtime.broadcast(f"[{self.name}] Waiting for confirmation to send", session.id)
        self.pending_confirmation = True
        self.runtime.emit("sender_waiting", session.id)

    def on_tick(self, session: Session):
        all_ready = True
        for module_name, module in self.runtime.modules.items():
            if module_name == self.name:
                continue
            status = session.get_status(module_name, "Ready to send")
            if status != "ready":
                all_ready = False

        if all_ready and self.pending_confirmation:
            self._send_to_ai(session)

    def _send_to_ai(self, session: Session):
        # Consume the trigger before sending so future sends require fresh work.
        self.pending_confirmation = False
        session.append_log(f"[{self.name}] All modules ready, sending to AI...")
        
        messages = []
        for ctx in session.get_context():
            t = ctx.get("type")
            if t in ("Text", "UserText", "ProtectedText", "SystemText"):
                if t in ("Text", "UserText"):
                    role = "user"
                elif t == "ProtectedText":
                    role = "assistant"
                else:
                    role = "system"
                messages.append({
                    "role": role,
                    "content": ctx["data"]
                })
            elif t == "ToolResult":
                messages.append({
                    "role": "tool",
                    "content": ctx["data"]
                })

        session.append_log(f"[{self.name}] Prepared {len(messages)} messages for AI")

        response = self._call_api(messages, session)

        if response:
            segments = _segment_model_response(response)
            ctx_before = len(session.get_context())
            if not segments:
                session.add_context("Text", response)
            else:
                for kind, chunk in segments:
                    if kind == "json":
                        session.add_context("Text", chunk, label="json")
                    else:
                        session.add_context("Text", chunk)
            self._resolve_user_input_tool_calls(session, ctx_before)
            n_seg = len(segments) if segments else 1
            session.append_log(f"[{self.name}] Received AI response ({n_seg} segment(s))")
            self.runtime.newloop(session)

    def _resolve_user_input_tool_calls(self, session: Session, index_start: int):
        """Turn synthetic user_input tool_call JSON into ToolResult and block dispatch until CLI input."""
        ctx = session.get_context()
        for i in range(index_start, len(ctx)):
            item = ctx[i]
            if item.get("type") != "Text" or item.get("label") != "json":
                continue
            try:
                data = json.loads(item["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            if data.get("type") != "tool_call" or data.get("name") != "user_input":
                continue
            args = data.get("arguments") or {}
            prompt = args.get("prompt") or args.get("message") or ""
            payload = {
                "type": "tool_result",
                "tool": "user_input",
                "ok": True,
                "message": "Awaiting user input in the CLI.",
                "prompt": prompt,
            }
            item["data"] = json.dumps(payload)
            item["type"] = "ToolResult"
            item.pop("label", None)
            session.awaiting_user_input = True
            session.append_log(
                f"[{self.name}] user_input tool: CLI will not report ready until user sends a message"
            )

    def _call_api(self, messages: list, session: Session) -> str:
        if not self.api_key:
            session.append_log(f"[{self.name}] No API key configured, simulating AI response")
            return "Simulated AI response - configure API key to enable real AI calls"
        
        try:
            import requests
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            data = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens
            }
            response = requests.post(self.api_url, headers=headers, json=data, timeout=self.timeout)
            session.append_log(f"[{self.name}] Response status: {response.status_code}")
            if response.status_code != 200:
                session.append_log(f"[{self.name}] Response body: {response.text}")
            response.raise_for_status()
            result = response.json()
            return result["choices"][0]["message"]["content"]
        except Exception as e:
            session.append_log(f"[{self.name}] API call failed: {e}")
            return f"Error: {e}"
