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


_MODULAR_TOOL_NAMES = frozenset(
    {"edit_file", "execute_command", "user_input", "save_memory", "search_memory"}
)


def _is_modular_tool_call_payload(data) -> bool:
    return (
        isinstance(data, dict)
        and data.get("type") == "tool_call"
        and data.get("name") in _MODULAR_TOOL_NAMES
    )


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
            data = json.loads(candidate)
        except json.JSONDecodeError:
            plain.append(text[j])
            i = j + 1
            continue
        if _is_modular_tool_call_payload(data):
            out.append(("json", candidate))
        else:
            plain.append(candidate)
        i = end

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
        if _is_modular_tool_call_payload(data):
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


# API response fragments: each becomes its own context row (web “text box”). Omitted from upstream via config blacklist.
CONTEXT_TYPE_API_PART = "SenderApiPart"


def _context_blocked_from_upstream(
    content_type: str | None,
    label: str | None,
    rules: list,
) -> bool:
    """
    Blacklist match: each rule has "type" (required). Optional "label":
    - omitted or null or "*": drop every context row of that type (any label)
    - string (including ""): drop only when ctx label equals that string (None/ missing ctx label treated as "")
    """
    if not rules:
        return False
    t = content_type or ""
    lbl = "" if label is None else str(label)
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        rt = rule.get("type")
        if rt != t:
            continue
        rl = rule.get("label", None)
        if rl is None or rl == "*":
            return True
        if str(rl) == lbl:
            return True
    return False


def _flatten_message_content_field(val) -> str:
    """Normalize message.content when the API returns a string or a list of parts (OpenAI-style)."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        chunks: list[str] = []
        for item in val:
            if isinstance(item, dict):
                if item.get("type") == "text" and item.get("text") is not None:
                    chunks.append(str(item["text"]))
                elif "text" in item:
                    chunks.append(str(item["text"]))
            elif isinstance(item, str):
                chunks.append(item)
        return "\n".join(chunks)
    return str(val)


def _merge_consecutive_same_role(messages: list[dict]) -> list[dict]:
    """Merge adjacent messages with the same role into one (DeepSeek and others reject successive same-role messages).
    Tool messages are never merged: each must keep its own tool_call_id."""
    out: list[dict] = []
    merge_roles = frozenset({"system", "user", "assistant"})
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        if role not in merge_roles:
            out.append(dict(m))
            continue
        raw = m.get("content")
        piece = _flatten_message_content_field(raw) if raw is not None else ""
        if out and out[-1].get("role") == role:
            prev = out[-1].get("content", "")
            if not isinstance(prev, str):
                prev = _flatten_message_content_field(prev)
            sep = "\n\n" if prev and piece else ""
            out[-1]["content"] = f"{prev}{sep}{piece}"
        else:
            out.append({"role": role, "content": piece})
    return out


def _finalize_upstream_messages(messages: list[dict], api_url: str) -> list[dict]:
    """
    After same-role merge, adapt for strict chat APIs. DeepSeek requires tool_call_id on tool
    messages and expects them to follow assistant tool_calls; ModularClaw uses JSON-in-text tools,
    so tool results are sent as user messages with a clear prefix, then user messages are merged.
    """
    merged = _merge_consecutive_same_role(messages)
    if not api_url or "api.deepseek.com" not in api_url:
        return merged
    converted: list[dict] = []
    for m in merged:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool":
            tid = m.get("tool_call_id") or ""
            body = _flatten_message_content_field(m.get("content"))
            lead = f"[Tool result tool_call_id={tid}]\n" if tid else "[Tool result]\n"
            converted.append({"role": "user", "content": lead + body})
        else:
            converted.append(dict(m))
    return _merge_consecutive_same_role(converted)


def _json_if_needed(val) -> str | None:
    if val is None:
        return None
    if isinstance(val, (str, int, float, bool)):
        return str(val)
    try:
        return json.dumps(val, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(val)


def _assistant_message_to_corpus(message: dict | None) -> str:
    """
    Build one string for ModularClaw tool JSON scanning from all assistant message fields
    providers commonly use (content, reasoning, tool_calls, etc.).
    """
    if not isinstance(message, dict):
        return ""
    blocks: list[str] = []
    content = _flatten_message_content_field(message.get("content"))
    if content.strip():
        blocks.append(content)

    for key in (
        "reasoning",
        "reasoning_content",
        "thinking",
        "analysis",
        "refusal",
    ):
        if key not in message:
            continue
        raw = message.get(key)
        if raw is None or raw == "":
            continue
        if isinstance(raw, str):
            piece = raw.strip()
        else:
            piece = (_json_if_needed(raw) or "").strip()
        if piece:
            blocks.append(f"[{key}]\n{piece}")

    if message.get("tool_calls"):
        tc = _json_if_needed(message.get("tool_calls"))
        if tc:
            blocks.append(f"[tool_calls]\n{tc}")

    used = {
        "content",
        "reasoning",
        "reasoning_content",
        "thinking",
        "analysis",
        "refusal",
        "tool_calls",
        "role",
        "name",
        "function_call",
    }
    for key in sorted(message.keys()):
        if key in used:
            continue
        val = message[key]
        if val is None or val == "":
            continue
        enc = _json_if_needed(val)
        if enc and enc.strip():
            blocks.append(f"[{key}]\n{enc}")

    return "\n\n".join(blocks)


def _segments_contain_modular_tool(segments: list[tuple[str, str]]) -> bool:
    for kind, chunk in segments:
        if kind != "json":
            continue
        try:
            data = json.loads(chunk)
        except (json.JSONDecodeError, TypeError):
            continue
        if _is_modular_tool_call_payload(data):
            return True
    return False


def _tool_scan_segments(message: dict | None, corpus: str) -> list[tuple[str, str]]:
    """Prefer segmenting main `content` only; fall back to full corpus if tools live outside content."""
    msg = message if isinstance(message, dict) else {}
    content_only = _flatten_message_content_field(msg.get("content"))
    segments = _segment_model_response(content_only)
    if _segments_contain_modular_tool(segments):
        return segments
    return _segment_model_response(corpus or "")


def _append_api_parts_from_envelope(session: Session, envelope: dict) -> None:
    """Split HTTP JSON into labeled SenderApiPart rows (metadata, usage, reasoning, etc.)."""
    if not isinstance(envelope, dict):
        return

    meta_keys = (
        "id",
        "model",
        "created",
        "object",
        "service_tier",
        "system_fingerprint",
    )
    meta = {k: envelope[k] for k in meta_keys if k in envelope and envelope[k] is not None}
    if meta:
        session.add_context(
            CONTEXT_TYPE_API_PART,
            json.dumps(meta, ensure_ascii=False, indent=2),
            label="response_meta",
        )

    usage = envelope.get("usage")
    if usage is not None:
        u = _json_if_needed(usage)
        if u and str(u).strip():
            session.add_context(CONTEXT_TYPE_API_PART, u, label="usage")

    choices = envelope.get("choices") or []
    for ci, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        cprefix = f"choice[{ci}]" if len(choices) > 1 else "choice"
        for key in ("finish_reason", "index", "logprobs"):
            if key not in choice:
                continue
            val = choice[key]
            if val is None:
                continue
            enc = _json_if_needed(val)
            if enc and str(enc).strip():
                session.add_context(CONTEXT_TYPE_API_PART, enc, label=f"{cprefix}.{key}")

        msg = choice.get("message")
        if not isinstance(msg, dict):
            continue

        if ci == 0:
            skip = {"content", "role"}
            priority = (
                "reasoning",
                "reasoning_content",
                "thinking",
                "analysis",
                "refusal",
                "tool_calls",
                "function_call",
                "name",
            )
            for key in priority:
                if key not in msg:
                    continue
                val = msg[key]
                if val is None or val == "":
                    continue
                if isinstance(val, str):
                    text = val.strip()
                elif isinstance(val, (dict, list)):
                    text = json.dumps(val, ensure_ascii=False, indent=2)
                else:
                    text = (_json_if_needed(val) or "").strip()
                if text:
                    session.add_context(CONTEXT_TYPE_API_PART, text, label=f"assistant.{key}")

            for key in sorted(msg.keys()):
                if key in skip or key in priority:
                    continue
                val = msg[key]
                if val is None or val == "":
                    continue
                if isinstance(val, str):
                    text = val.strip()
                elif isinstance(val, (dict, list)):
                    text = json.dumps(val, ensure_ascii=False, indent=2)
                else:
                    text = (_json_if_needed(val) or "").strip()
                if text:
                    session.add_context(CONTEXT_TYPE_API_PART, text, label=f"assistant.{key}")
        else:
            session.add_context(
                CONTEXT_TYPE_API_PART,
                json.dumps(choice, ensure_ascii=False, indent=2),
                label=f"{cprefix}_full",
            )


def _merge_cumulative_usage(prev_raw: str | None, delta: dict) -> dict:
    """Sum OpenAI-style usage dicts across requests for session status (prompt/completion/total + detail ints)."""
    acc: dict = {}
    if prev_raw:
        try:
            parsed = json.loads(prev_raw) if isinstance(prev_raw, str) else prev_raw
            if isinstance(parsed, dict):
                acc = {k: v for k, v in parsed.items()}
        except (json.JSONDecodeError, TypeError):
            acc = {}
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        v = delta.get(k)
        if isinstance(v, (int, float)):
            acc[k] = int(acc.get(k, 0) or 0) + int(v)
    for detail_key in ("completion_tokens_details", "prompt_tokens_details"):
        d = delta.get(detail_key)
        if not isinstance(d, dict):
            continue
        cur = acc.get(detail_key)
        merged_detail: dict = dict(cur) if isinstance(cur, dict) else {}
        for sk, sv in d.items():
            if isinstance(sv, (int, float)):
                merged_detail[sk] = int(merged_detail.get(sk, 0) or 0) + int(sv)
        if merged_detail:
            acc[detail_key] = merged_detail
    return acc


def _apply_completion_envelope_to_sender_status(session: Session, sender_name: str, envelope: dict) -> None:
    """
    Parse chat/completion JSON (e.g. GLM/OpenAI shape) into session.status_list[sender_name].
    Mirrors the raw body in last_completion plus flat fields for dashboards.
    last_usage accumulates prompt/completion/total (and numeric detail fields) across API calls in the session.
    """
    if not isinstance(envelope, dict):
        return

    session.set_status(sender_name, "last_completion", json.dumps(envelope, ensure_ascii=False))
    session.set_status(sender_name, "last_id", str(envelope.get("id") or ""))
    rid = envelope.get("request_id")
    session.set_status(sender_name, "last_request_id", "" if rid is None else str(rid))
    session.set_status(sender_name, "last_model", str(envelope.get("model") or ""))
    created = envelope.get("created")
    session.set_status(sender_name, "last_created", "" if created is None else str(created))
    session.set_status(sender_name, "last_object", str(envelope.get("object") or ""))

    usage = envelope.get("usage")
    if isinstance(usage, dict):
        prev = session.get_status(sender_name, "last_usage")
        merged = _merge_cumulative_usage(prev if isinstance(prev, str) else None, usage)
        session.set_status(sender_name, "last_usage", json.dumps(merged, ensure_ascii=False))
    elif isinstance(usage, list):
        session.set_status(sender_name, "last_usage", json.dumps(usage, ensure_ascii=False))
    elif usage is not None:
        session.set_status(sender_name, "last_usage", str(usage))

    choices = envelope.get("choices") or []
    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    session.set_status(sender_name, "last_finish_reason", str(first.get("finish_reason") or ""))
    idx = first.get("index")
    session.set_status(sender_name, "last_choice_index", "" if idx is None else str(idx))

    raw_msg = first.get("message")
    msg = raw_msg if isinstance(raw_msg, dict) else {}
    session.set_status(sender_name, "last_message_role", str(msg.get("role") or ""))

    content = _flatten_message_content_field(msg.get("content"))
    session.set_status(sender_name, "last_assistant_content", content)

    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if reasoning is None:
        reasoning_text = ""
    elif isinstance(reasoning, str):
        reasoning_text = reasoning
    else:
        reasoning_text = _json_if_needed(reasoning) or ""
    session.set_status(sender_name, "last_reasoning_content", reasoning_text)

    session.set_status(sender_name, "last_error", "")


def _active_sender_api_profile(sender_cfg: dict) -> dict:
    """Pick the API profile from apis[] using selected_model (matches id, else model string), with legacy api {} fallback."""
    if not isinstance(sender_cfg, dict):
        return {}
    raw_apis = sender_cfg.get("apis")
    profiles: list[dict] = [p for p in raw_apis if isinstance(p, dict)] if isinstance(raw_apis, list) else []
    legacy = sender_cfg.get("api")
    if not profiles and isinstance(legacy, dict) and legacy:
        profiles = [legacy]
    if not profiles:
        return {}
    selected = sender_cfg.get("selected_model")
    if isinstance(selected, str) and selected.strip():
        key = selected.strip()
        for p in profiles:
            if p.get("id") == key:
                return p
        for p in profiles:
            if p.get("model") == key:
                return p
    return profiles[0]


class Sender(Module):
    VERSION = "1.1.0"

    def __init__(self, api_key: str = None, api_url: str = None, model: str = None):
        super().__init__()
        api_config = _active_sender_api_profile(Config.load("sender"))
        
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

        raw_bl = Config.get("sender", "context_upstream_blacklist", None)
        if isinstance(raw_bl, list):
            self._context_upstream_blacklist = raw_bl
        else:
            self._context_upstream_blacklist = [
                {"type": CONTEXT_TYPE_API_PART},
                {"type": "SenderApiEnvelope"},
            ]

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
            lbl = ctx.get("label")
            if _context_blocked_from_upstream(t, lbl, self._context_upstream_blacklist):
                continue
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
                raw_data = ctx.get("data", "")
                if not isinstance(raw_data, str):
                    raw_data = _json_if_needed(raw_data) or ""
                tid = None
                tool_name = "tool"
                try:
                    tr = json.loads(raw_data)
                    if isinstance(tr, dict):
                        tid = tr.get("tool_call_id")
                        tool_name = tr.get("tool") or tool_name
                except (json.JSONDecodeError, TypeError):
                    pass
                if not tid:
                    tid = f"modularclaw_{tool_name}_{len(messages)}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(tid),
                    "content": raw_data,
                })

        messages = _finalize_upstream_messages(messages, self.api_url or "")
        session.append_log(f"[{self.name}] Prepared {len(messages)} messages for AI")

        corpus, envelope = self._call_api(messages, session)

        advance = (
            envelope is not None
            or (corpus and str(corpus).strip())
            or (isinstance(corpus, str) and corpus.startswith("Simulated"))
            or (isinstance(corpus, str) and corpus.startswith("Error:"))
        )
        if advance:
            msg: dict = {}
            if isinstance(envelope, dict):
                first = (envelope.get("choices") or [{}])[0]
                raw_m = first.get("message") if isinstance(first, dict) else None
                msg = raw_m if isinstance(raw_m, dict) else {}
            segments = _tool_scan_segments(msg, corpus or "")
            ctx_before = len(session.get_context())
            added_plain = False
            if not segments:
                content_fallback = _flatten_message_content_field(msg.get("content"))
                plain = (content_fallback or (corpus or "")).strip()
                if plain:
                    session.add_context("Text", plain)
                    added_plain = True
            else:
                for kind, chunk in segments:
                    if kind == "json":
                        session.add_context("Text", chunk, label="json")
                    else:
                        session.add_context("Text", chunk)
            self._resolve_user_input_tool_calls(session, ctx_before)
            if envelope is not None:
                _apply_completion_envelope_to_sender_status(session, self.name, envelope)
                _append_api_parts_from_envelope(session, envelope)
            n_seg = len(segments) if segments else (1 if added_plain else 0)
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

    def _call_api(self, messages: list, session: Session) -> tuple[str, dict | None]:
        """
        Returns (corpus_for_tool_scanning, full_response_json_or_none).
        On HTTP success, envelope is the parsed JSON body; corpus aggregates assistant message fields.
        """
        if not self.api_key:
            session.append_log(f"[{self.name}] No API key configured, simulating AI response")
            return (
                "Simulated AI response - configure API key to enable real AI calls",
                None,
            )

        try:
            import requests

            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            try:
                max_out = int(self.max_tokens)
            except (TypeError, ValueError):
                max_out = 2048
            max_out = max(1, max_out)
            if self.api_url and "api.deepseek.com" in self.api_url:
                max_out = min(max_out, 8192)
            data = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "max_tokens": max_out,
            }
            response = requests.post(self.api_url, headers=headers, json=data, timeout=self.timeout)
            session.append_log(f"[{self.name}] Response status: {response.status_code}")
            if response.status_code != 200:
                session.append_log(f"[{self.name}] Response body: {response.text}")
            response.raise_for_status()
            result = response.json()
            choices = result.get("choices") or []
            first = choices[0] if choices else {}
            message = (first.get("message") if isinstance(first, dict) else None) or {}
            corpus = _assistant_message_to_corpus(message if isinstance(message, dict) else {})
            if message.get("function_call"):
                fc = _json_if_needed(message.get("function_call"))
                if fc:
                    corpus = (corpus + "\n\n" if corpus else "") + f"[function_call]\n{fc}"
            return corpus, result
        except Exception as e:
            err_msg = str(e)
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    body = (resp.text or "").strip()
                except Exception:
                    body = ""
                if body and body not in err_msg:
                    err_msg = f"{err_msg} — {body[:2000]}"
            session.append_log(f"[{self.name}] API call failed: {err_msg}")
            session.set_status(self.name, "last_error", err_msg)
            return f"Error: {err_msg}", None
