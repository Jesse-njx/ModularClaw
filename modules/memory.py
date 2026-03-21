import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from config_loader import Config
from core import Module
from session import Session


class Memory(Module):
    """Tool-driven persistent snippets under workspace/Memory/memories.json."""

    VERSION = "1.1.0"

    def __init__(self):
        super().__init__()
        mem_config = Config.get("memory") or {}
        self._tool_prompt = (mem_config.get("prompt") or "").strip()
        policy = mem_config.get("path_policy", {})
        self.workspace_root = Path(
            os.path.abspath(policy.get("workspace_root") or os.getcwd())
        )
        storage = mem_config.get("storage", {})
        rel_dir = storage.get("relative_dir", "Memory")
        mem_file = storage.get("memories_file", "memories.json")
        self.memory_dir = self.workspace_root / rel_dir
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memories_file = self.memory_dir / mem_file
        self.memories: List[Dict[str, Any]] = self._load_memories()

    def on_session_start(self, session: Session):
        if self._tool_prompt:
            session.add_context("SystemText", self._tool_prompt)

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
            if data.get("type") != "tool_call":
                continue
            name = data.get("name")
            if name not in ("save_memory", "search_memory"):
                continue
            if session.is_claimed(i):
                continue
            if name == "save_memory":
                self._handle_save_memory(session, i, data)
            else:
                self._handle_search_memory(session, i, data)

        self._check_if_all_clear(session)

    def _load_memories(self) -> List[Dict[str, Any]]:
        if not self.memories_file.exists():
            return []
        try:
            with open(self.memories_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, list) else []
        except (json.JSONDecodeError, OSError):
            return []

    def _save_memories(self) -> None:
        with open(self.memories_file, "w", encoding="utf-8") as f:
            json.dump(self.memories, f, indent=2, ensure_ascii=False)

    def _next_id(self) -> int:
        if not self.memories:
            return 1
        return max(m.get("id", 0) for m in self.memories) + 1

    def _handle_save_memory(self, session: Session, index: int, tool_call: dict):
        session.claim_region(index, self.name)
        args = tool_call.get("arguments") or {}
        content = args.get("content")
        if not content:
            payload = {
                "type": "tool_result",
                "tool": "save_memory",
                "ok": False,
                "message": "Missing required argument: content",
            }
            session.update_region(index, json.dumps(payload), "ToolResult")
            session.mark_claimed_region_finished(index, self.name)
            session.append_log(f"[{self.name}] save_memory -> missing content")
            return

        entry = {
            "id": self._next_id(),
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "tags": args.get("tags") or [],
            "importance": args.get("importance", 3),
            "metadata": args.get("metadata") or {},
        }
        self.memories.append(entry)
        try:
            self._save_memories()
        except OSError as e:
            self.memories.pop()
            payload = {
                "type": "tool_result",
                "tool": "save_memory",
                "ok": False,
                "message": f"Failed to save: {e}",
            }
            session.update_region(index, json.dumps(payload), "ToolResult")
            session.mark_claimed_region_finished(index, self.name)
            session.append_log(f"[{self.name}] save_memory -> {e}")
            return

        payload = {
            "type": "tool_result",
            "tool": "save_memory",
            "ok": True,
            "message": "Memory saved successfully",
            "memory_id": entry["id"],
            "timestamp": entry["timestamp"],
        }
        session.update_region(index, json.dumps(payload), "ToolResult")
        session.mark_claimed_region_finished(index, self.name)
        session.append_log(f"[{self.name}] save_memory -> id {entry['id']}")

    def _handle_search_memory(self, session: Session, index: int, tool_call: dict):
        session.claim_region(index, self.name)
        args = tool_call.get("arguments") or {}
        query = (args.get("query") or "").strip()
        tags = args.get("tags") or []
        try:
            max_results = int(args.get("max_results", 10))
        except (TypeError, ValueError):
            max_results = 10
        max_results = max(1, min(max_results, 500))

        if not query and not tags:
            results = sorted(
                self.memories,
                key=lambda x: x.get("timestamp") or "",
                reverse=True,
            )[:max_results]
            payload = {
                "type": "tool_result",
                "tool": "search_memory",
                "ok": True,
                "message": f"Returning {len(results)} most recent memories",
                "results": results,
                "total_memories": len(self.memories),
            }
            session.update_region(index, json.dumps(payload), "ToolResult")
            session.mark_claimed_region_finished(index, self.name)
            session.append_log(f"[{self.name}] search_memory -> recent {len(results)}")
            return

        filtered: List[Dict[str, Any]] = []
        for memory in self.memories:
            if tags:
                memory_tags = set(memory.get("tags") or [])
                if not set(tags).issubset(memory_tags):
                    continue
            if query:
                content = (memory.get("content") or "").lower()
                if query.lower() in content:
                    filtered.append(memory)
                elif not tags:
                    continue
            else:
                filtered.append(memory)

        filtered.sort(
            key=lambda x: (-int(x.get("importance", 3)), x.get("timestamp") or "")
        )
        results = filtered[:max_results]
        payload = {
            "type": "tool_result",
            "tool": "search_memory",
            "ok": True,
            "message": f"Found {len(results)} matching memories",
            "results": results,
            "total_memories": len(self.memories),
            "total_matching": len(filtered),
        }
        session.update_region(index, json.dumps(payload), "ToolResult")
        session.mark_claimed_region_finished(index, self.name)
        session.append_log(f"[{self.name}] search_memory -> {len(results)} matches")

    def _check_if_all_clear(self, session: Session):
        context = session.get_context()
        pending = False
        for ctx in context:
            if ctx.get("type") != "Text" or ctx.get("label") != "json":
                continue
            try:
                data = json.loads(ctx["data"])
                if (
                    isinstance(data, dict)
                    and data.get("type") == "tool_call"
                    and data.get("name") in ("save_memory", "search_memory")
                ):
                    pending = True
                    break
            except (json.JSONDecodeError, TypeError):
                pass
        if pending:
            session.set_status(self.name, "Ready to send", "pending")
        else:
            session.set_status(self.name, "Ready to send", "ready")
