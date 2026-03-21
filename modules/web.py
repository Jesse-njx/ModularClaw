import html
import json
import threading
from urllib.parse import urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from core import Module, Runtime
from config_loader import Config
from session import Session
from modules.sender import _context_blocked_from_upstream


class StatusHandler(BaseHTTPRequestHandler):
    module = None

    def do_GET(self):
        if self.module is None:
            self.send_error(500, "Module not initialized")
            return

        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/session/"):
            session = self._get_session(path, "/api/session/")
            if not session:
                self._send_json({"error": "Session not found"}, status_code=404)
                return
            self._send_json(self.module._session_payload(session))
            return

        if path.startswith("/session/"):
            session = self._get_session(path, "/session/")
        else:
            session = self._first_session()

        if not session:
            self._send_html("<!DOCTYPE html><html><body><h1>No active sessions</h1></body></html>")
            return

        html = self.module._generate_html(session)
        self._send_html(html)

    def _first_session(self):
        if not self.module.runtime.sessions:
            return None
        return list(self.module.runtime.sessions.values())[0]

    def _get_session(self, path: str, prefix: str):
        session_id = path[len(prefix):].strip("/")
        if not session_id:
            return None
        return self.module.runtime.get_session(session_id)

    def _send_html(self, content: str, status_code: int = 200):
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode("utf-8"))

    def _send_json(self, payload: dict, status_code: int = 200):
        body = json.dumps(payload)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass


class Web(Module):
    VERSION = "1.1.0"

    def __init__(self, port: int = None, host: str = None):
        super().__init__()
        web_config = Config.get("web", "server", {})
        self.port = port or web_config.get("port", 8080)
        self.host = host or web_config.get("host", "localhost")
        self._server = None
        self._thread = None

        raw_bl = Config.get("web", "context_display_blacklist", None)
        if isinstance(raw_bl, list):
            self._context_display_blacklist = raw_bl
        else:
            self._context_display_blacklist = [
                {"type": "SenderApiEnvelope"},
                {"type": "SenderApiPart"},
            ]

    def _context_for_display(self, session: Session) -> list:
        """Omit blacklisted context rows from dashboard/API (same semantics as sender upstream blacklist)."""
        out: list = []
        for row in session.get_context():
            if not isinstance(row, dict):
                continue
            if _context_blocked_from_upstream(
                row.get("type"), row.get("label"), self._context_display_blacklist
            ):
                continue
            out.append(row)
        return out

    def on_loop(self, session: Session):
        if self._server is None:
            self._start_server()

    def on_tick(self, session: Session):
        # Web is passive for processing, so it should not block Sender readiness checks.
        session.set_status(self.name, "Ready to send", "ready")

    def _start_server(self):
        StatusHandler.module = self
        self._server = HTTPServer((self.host, self.port), StatusHandler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        print(f"[Web] Status page available at http://{self.host}:{self.port}")

    # Keys written by modules/sender._apply_completion_envelope_to_sender_status
    _SENDER_USAGE_KEYS = (
        "last_model",
        "last_usage",
        "last_finish_reason",
        "last_id",
        "last_request_id",
        "last_created",
        "last_object",
        "last_choice_index",
        "last_message_role",
        "last_error",
    )

    def _sender_status_view(self, session: Session) -> dict:
        """Parse sender module status (from sender.py) for API / UI."""
        raw = session.get_status("sender")
        if not isinstance(raw, dict):
            raw = {}
        out = {k: raw.get(k) or "" for k in self._SENDER_USAGE_KEYS}
        out["tokens"] = None
        usage_raw = raw.get("last_usage")
        if usage_raw:
            try:
                u = json.loads(usage_raw) if isinstance(usage_raw, str) else usage_raw
            except (json.JSONDecodeError, TypeError):
                u = None
            if isinstance(u, dict):
                details = u.get("completion_tokens_details") or {}
                reasoning = details.get("reasoning_tokens") if isinstance(details, dict) else None
                pd = u.get("prompt_tokens_details") or {}
                cached = pd.get("cached_tokens") if isinstance(pd, dict) else None
                out["tokens"] = {
                    "prompt": u.get("prompt_tokens"),
                    "completion": u.get("completion_tokens"),
                    "total": u.get("total_tokens"),
                    "reasoning": reasoning,
                    "cached": cached,
                }
        return out

    def _session_payload(self, session: Session) -> dict:
        return {
            "id": session.id,
            "loop_count": session.loop_count,
            "tick_count": session.tick_count,
            "statuses": session.get_all_statuses(),
            "sender": self._sender_status_view(session),
            "context": self._context_for_display(session),
            "logs": session.logs[-100:],
            "total_logs": len(session.logs),
        }

    def _generate_html(self, session: Session) -> str:
        session_id = html.escape(session.id)
        return f"""<!DOCTYPE html>
<html>
<head>
    <title>Session Status - {session_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background: #f5f5f5; color: #222; }}
        h1 {{ margin: 0 0 8px 0; }}
        .meta {{ margin: 0 0 16px 0; color: #555; }}
        .tabs {{ display: flex; gap: 8px; margin: 10px 0 16px 0; }}
        .tab-btn {{
            border: 1px solid #ccd3dd;
            background: #fff;
            color: #333;
            border-radius: 8px;
            padding: 8px 14px;
            cursor: pointer;
            font-weight: 600;
        }}
        .tab-btn.active {{ background: #2f80ed; color: #fff; border-color: #2f80ed; }}
        .panel {{
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            padding: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
            max-height: 70vh;
            overflow-y: auto;
        }}
        .item {{
            padding: 8px;
            margin: 6px 0;
            background: #f9fafb;
            border-left: 3px solid #2f80ed;
            border-radius: 4px;
            word-break: break-word;
        }}
        .context-data {{
            margin-top: 4px;
            white-space: pre-wrap;
        }}
        .subtle {{ color: #637083; font-size: 12px; }}
        .log-entry {{ font-family: monospace; font-size: 12px; padding: 4px 0; border-bottom: 1px solid #eef1f4; }}
        .empty {{ color: #666; }}
        .status-group {{ margin-bottom: 10px; }}
        .status-title {{ font-weight: 700; margin: 6px 0; }}
        .status-line {{ margin-left: 12px; padding: 2px 0; }}
        .sender-strip {{
            margin: 0 0 12px 0;
            padding: 10px 12px;
            background: #fff;
            border: 1px solid #e5e7eb;
            border-radius: 10px;
            font-size: 14px;
            color: #333;
        }}
        .sender-strip strong {{ color: #111; }}
        .sender-card {{
            margin-bottom: 16px;
            padding: 12px;
            background: #f0f6ff;
            border: 1px solid #cfe0fc;
            border-radius: 10px;
        }}
        .sender-card h3 {{ margin: 0 0 8px 0; font-size: 15px; color: #1e3a5f; }}
        .token-row {{ font-family: monospace; font-size: 13px; margin-top: 6px; color: #333; }}
    </style>
</head>
<body>
    <h1>Session Status: {session_id}</h1>
    <p class="meta">Loop: <span id="loopCount">0</span> | Tick: <span id="tickCount">0</span></p>
    <div class="sender-strip" id="senderStrip" style="display:none;"></div>

    <div class="tabs">
        <button class="tab-btn active" data-tab="context">Context</button>
        <button class="tab-btn" data-tab="status">Status</button>
        <button class="tab-btn" data-tab="logs">Logs</button>
    </div>

    <div class="panel" id="tabContent"></div>

    <script>
        const sessionId = {json.dumps(session.id)};
        const contentEl = document.getElementById("tabContent");
        const loopEl = document.getElementById("loopCount");
        const tickEl = document.getElementById("tickCount");
        const senderStripEl = document.getElementById("senderStrip");
        const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
        const tabs = ["context", "status", "logs"];

        let activeTab = "context";
        let latestPayload = null;
        let lastContextCount = 0;

        function escapeHtml(value) {{
            return String(value)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }}

        function setActiveTab(tab) {{
            if (!tabs.includes(tab)) return;
            activeTab = tab;
            tabButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
            render();
        }}

        function renderContext(payload) {{
            const context = payload.context || [];
            if (!context.length) return '<div class="empty">No context yet</div>';
            return context.map((ctx) => {{
                const ctxType = escapeHtml(ctx.type || "unknown");
                const ctxLabel = ctx.label ? escapeHtml(ctx.label) : "";
                const labelBadge = ctxLabel ? ` <span class="subtle">(${{ctxLabel}})</span>` : "";
                let data = String(ctx.data || "");
                data = escapeHtml(data);
                const module = ctx.module ? escapeHtml(ctx.module) : "";
                const claimedSince = ctx.claimedSince !== undefined ? escapeHtml(ctx.claimedSince) : "";
                const claimNote = module ? `<div class="subtle">claimed by ${{module}} since ${{claimedSince}}</div>` : "";
                return `<div class="item"><strong>[${{ctxType}}]</strong>${{labelBadge}}<div class="context-data">${{data}}</div>${{claimNote}}</div>`;
            }}).join("");
        }}

        function formatTokens(tok) {{
            if (!tok || typeof tok !== "object") return "";
            const parts = [];
            if (tok.prompt != null) parts.push(`prompt ${{tok.prompt}}`);
            if (tok.completion != null) parts.push(`completion ${{tok.completion}}`);
            if (tok.total != null) parts.push(`total ${{tok.total}}`);
            if (tok.reasoning != null) parts.push(`reasoning ${{tok.reasoning}}`);
            if (tok.cached != null) parts.push(`cached ${{tok.cached}}`);
            return parts.length ? parts.join(" · ") : "";
        }}

        function renderSenderStrip(sender) {{
            if (!senderStripEl) return;
            const model = (sender && sender.last_model) ? String(sender.last_model) : "";
            const tokLine = formatTokens(sender && sender.tokens);
            const err = sender && sender.last_error ? String(sender.last_error) : "";
            if (!model && !tokLine && !err) {{
                senderStripEl.style.display = "none";
                senderStripEl.innerHTML = "";
                return;
            }}
            senderStripEl.style.display = "block";
            let html = "";
            if (model) html += `<strong>Model</strong> ${{escapeHtml(model)}}`;
            if (tokLine) html += (html ? " &nbsp;|&nbsp; " : "") + `<strong>Tokens</strong> ${{escapeHtml(tokLine)}}`;
            if (err) html += (html ? "<br>" : "") + `<span style="color:#b42318;"><strong>Error</strong> ${{escapeHtml(err)}}</span>`;
            senderStripEl.innerHTML = html;
        }}

        function renderSenderCard(sender) {{
            const s = sender || {{}};
            const hasData = !!(s.last_model || s.last_id || s.last_error
                || (s.tokens && (s.tokens.total != null || s.tokens.prompt != null || s.tokens.completion != null)));
            if (!hasData) return '<div class="subtle">No sender status yet (waiting for first API response).</div>';
            const model = s.last_model ? escapeHtml(s.last_model) : "—";
            const tokLine = formatTokens(s.tokens);
            const fr = s.last_finish_reason ? escapeHtml(s.last_finish_reason) : "—";
            const lid = s.last_id ? escapeHtml(s.last_id) : "—";
            const rid = s.last_request_id ? escapeHtml(s.last_request_id) : "";
            const created = s.last_created ? escapeHtml(s.last_created) : "";
            const obj = s.last_object ? escapeHtml(s.last_object) : "";
            const idx = s.last_choice_index ? escapeHtml(s.last_choice_index) : "";
            const role = s.last_message_role ? escapeHtml(s.last_message_role) : "";
            const err = s.last_error ? escapeHtml(s.last_error) : "";
            let html = `<div class="sender-card"><h3>Sender (from session status)</h3>`;
            html += `<div class="status-line"><strong>last_model:</strong> ${{model}}</div>`;
            html += `<div class="token-row">${{tokLine ? escapeHtml(tokLine) : "<span class=\\"subtle\\">No usage recorded yet</span>"}}</div>`;
            html += `<div class="status-line"><strong>last_finish_reason:</strong> ${{fr}}</div>`;
            html += `<div class="status-line"><strong>last_id:</strong> ${{lid}}</div>`;
            if (rid) html += `<div class="status-line"><strong>last_request_id:</strong> ${{rid}}</div>`;
            if (created) html += `<div class="status-line"><strong>last_created:</strong> ${{created}}</div>`;
            if (obj) html += `<div class="status-line"><strong>last_object:</strong> ${{obj}}</div>`;
            if (idx !== "") html += `<div class="status-line"><strong>last_choice_index:</strong> ${{idx}}</div>`;
            if (role) html += `<div class="status-line"><strong>last_message_role:</strong> ${{role}}</div>`;
            if (err) html += `<div class="status-line" style="color:#b42318;"><strong>last_error:</strong> ${{err}}</div>`;
            html += `</div>`;
            return html;
        }}

        function renderStatus(payload) {{
            const senderHtml = renderSenderCard(payload.sender || null);
            const statuses = payload.statuses || {{}};
            const modules = Object.keys(statuses).filter((m) => m !== "sender");
            const rest = modules.map((moduleName) => {{
                const moduleStatuses = statuses[moduleName] || {{}};
                const lines = Object.entries(moduleStatuses).map(([key, value]) => {{
                    return `<div class="status-line"><strong>${{escapeHtml(key)}}:</strong> ${{escapeHtml(value)}}</div>`;
                }}).join("");
                return `<div class="status-group"><div class="status-title">Module: ${{escapeHtml(moduleName)}}</div>${{lines || '<div class="subtle">No details</div>'}}</div>`;
            }}).join("");
            return senderHtml + rest;
        }}

        function renderLogs(payload) {{
            const logs = (payload.logs || []).slice().reverse();
            if (!logs.length) return '<div class="empty">No logs yet</div>';
            return logs.map((log) => `<div class="log-entry">${{escapeHtml(log)}}</div>`).join("");
        }}

        function render() {{
            if (!latestPayload) {{
                contentEl.innerHTML = '<div class="empty">Loading...</div>';
                return;
            }}
            loopEl.textContent = String(latestPayload.loop_count ?? 0);
            tickEl.textContent = String(latestPayload.tick_count ?? 0);
            renderSenderStrip(latestPayload.sender || null);

            if (activeTab === "context") {{
                const contextCount = (latestPayload.context || []).length;
                const hasNewSections = contextCount > lastContextCount;
                contentEl.innerHTML = renderContext(latestPayload);
                if (hasNewSections) {{
                    contentEl.scrollTop = contentEl.scrollHeight;
                }}
                lastContextCount = contextCount;
            }} else if (activeTab === "status") {{
                contentEl.innerHTML = renderStatus(latestPayload);
            }} else {{
                const totalLogs = latestPayload.total_logs ?? 0;
                contentEl.innerHTML = `<div class="subtle">Logs (${{totalLogs}} total, showing last 100)</div>` + renderLogs(latestPayload);
            }}
        }}

        async function poll() {{
            try {{
                const response = await fetch(`/api/session/${{encodeURIComponent(sessionId)}}`, {{ cache: "no-store" }});
                if (!response.ok) throw new Error("Failed to load session");
                const payload = await response.json();
                latestPayload = payload;
                render();
            }} catch (err) {{
                contentEl.innerHTML = `<div class="empty">Unable to load session data: ${{escapeHtml(err.message)}}</div>`;
            }}
        }}

        tabButtons.forEach((btn) => {{
            btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
        }});

        render();
        poll();
        setInterval(poll, 500);
    </script>
</body>
</html>"""
