import json
import time
from core import Module, Runtime
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
        
        self.pending_confirmation = False

    def on_loop(self, session: Session):
        self.runtime.broadcast(f"[{self.name}] Waiting for confirmation to send", session.id)
        self.pending_confirmation = True
        self.runtime.emit("sender_waiting", session.id)

    def on_tick(self, session: Session):
        if not session.needs_loop():
            return

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
        session.set_need_loop(False)
        session.append_log(f"[{self.name}] All modules ready, sending to AI...")
        
        messages = []
        for ctx in session.get_context():
            if ctx.get("type") == "Text" or ctx.get("type") == "ProtectedText":
                role = "user" if ctx.get("type") == "Text" else "assistant"
                messages.append({
                    "role": role,
                    "content": ctx["data"]
                })
            elif ctx.get("type") == "ToolResult":
                messages.append({
                    "role": "tool",
                    "content": ctx["data"]
                })

        session.append_log(f"[{self.name}] Prepared {len(messages)} messages for AI")

        response = self._call_api(messages, session)
        
        if response:
            segments = _split_response_into_segments(response)
            if not segments:
                session.add_context("Text", response)
            else:
                for kind, chunk in segments:
                    if kind == "json":
                        session.add_context("Text", chunk, label="json")
                    else:
                        session.add_context("Text", chunk)
            n_seg = len(segments) if segments else 1
            session.append_log(f"[{self.name}] Received AI response ({n_seg} segment(s))")
            self.runtime.newloop(session)

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
