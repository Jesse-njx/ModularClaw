import html
import json
import threading
from urllib.parse import urlparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from core import Module, Runtime
from config_loader import Config
from session import Session


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
    VERSION = "1.0.0"

    def __init__(self, port: int = None, host: str = None):
        super().__init__()
        web_config = Config.get("web", "server", {})
        self.port = port or web_config.get("port", 8080)
        self.host = host or web_config.get("host", "localhost")
        self._server = None
        self._thread = None

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

    def _session_payload(self, session: Session) -> dict:
        return {
            "id": session.id,
            "loop_count": session.loop_count,
            "tick_count": session.tick_count,
            "statuses": session.get_all_statuses(),
            "context": session.get_context(),
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
        }}
        .item {{
            padding: 8px;
            margin: 6px 0;
            background: #f9fafb;
            border-left: 3px solid #2f80ed;
            border-radius: 4px;
            word-break: break-word;
        }}
        .subtle {{ color: #637083; font-size: 12px; }}
        .log-entry {{ font-family: monospace; font-size: 12px; padding: 4px 0; border-bottom: 1px solid #eef1f4; }}
        .empty {{ color: #666; }}
        .status-group {{ margin-bottom: 10px; }}
        .status-title {{ font-weight: 700; margin: 6px 0; }}
        .status-line {{ margin-left: 12px; padding: 2px 0; }}
    </style>
</head>
<body>
    <h1>Session Status: {session_id}</h1>
    <p class="meta">Loop: <span id="loopCount">0</span> | Tick: <span id="tickCount">0</span></p>

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
        const tabButtons = Array.from(document.querySelectorAll(".tab-btn"));
        const tabs = ["context", "status", "logs"];

        let activeTab = "context";
        let latestPayload = null;
        let latestTick = -1;

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
                if (data.length > 400) data = data.slice(0, 400) + "...";
                data = escapeHtml(data);
                const module = ctx.module ? escapeHtml(ctx.module) : "";
                const claimedSince = ctx.claimedSince !== undefined ? escapeHtml(ctx.claimedSince) : "";
                const claimNote = module ? `<div class="subtle">claimed by ${{module}} since ${{claimedSince}}</div>` : "";
                return `<div class="item"><strong>[${{ctxType}}]</strong>${{labelBadge}} ${{data}}${{claimNote}}</div>`;
            }}).join("");
        }}

        function renderStatus(payload) {{
            const statuses = payload.statuses || {{}};
            const modules = Object.keys(statuses);
            if (!modules.length) return '<div class="empty">No status updates yet</div>';
            return modules.map((moduleName) => {{
                const moduleStatuses = statuses[moduleName] || {{}};
                const lines = Object.entries(moduleStatuses).map(([key, value]) => {{
                    return `<div class="status-line"><strong>${{escapeHtml(key)}}:</strong> ${{escapeHtml(value)}}</div>`;
                }}).join("");
                return `<div class="status-group"><div class="status-title">Module: ${{escapeHtml(moduleName)}}</div>${{lines || '<div class="subtle">No details</div>'}}</div>`;
            }}).join("");
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

            if (activeTab === "context") {{
                contentEl.innerHTML = renderContext(latestPayload);
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
                if (payload.tick_count !== latestTick) {{
                    latestTick = payload.tick_count;
                    render();
                }} else if (!contentEl.innerHTML) {{
                    render();
                }}
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
