import time
from config_loader import Config


class Session:
    VERSION = "1.1.0"

    def __init__(self, id: str):
        self.id = id
        self.context = []
        self.status_list = {}
        self.logs = []
        self._claimed_regions = {}
        self._max_context = Config.get("system", "session", {}).get("max_context_items", 1000)
        self._max_logs = Config.get("system", "session", {}).get("max_logs", 5000)
        self.loop_count = 0
        self.tick_count = 0
        self._loop_limit = Config.get("system", "runtime", {}).get("max_loops", 5)
        # After a user_input tool call; CLI uses this to hold Ready to send until the user types.
        self.awaiting_user_input = False

    def add_context(self, content_type: str, data: str, module: str = None, claimed_since: int = None, info: dict = None, label: str = None):
        if len(self.context) >= self._max_context:
            self.context.pop(0)
        entry = {"type": content_type, "data": data}
        if label:
            entry["label"] = label
        if module:
            entry["module"] = module
            entry["claimedSince"] = claimed_since
            entry["info"] = info or {}
        self.context.append(entry)
        return len(self.context) - 1

    def get_context(self):
        return self.context

    def set_status(self, module: str, key: str, value):
        if module not in self.status_list:
            self.status_list[module] = {}
        self.status_list[module][key] = value

    def get_status(self, module: str, key: str = None):
        if module not in self.status_list:
            return None
        if key is None:
            return self.status_list[module]
        return self.status_list[module].get(key)

    def get_all_statuses(self):
        return self.status_list

    def append_log(self, message: str):
        if len(self.logs) >= self._max_logs:
            self.logs.pop(0)
        self.logs.append(f"[{int(time.time())}] {message}")

    def claim_region(self, region_index: int, module: str):
        self._claimed_regions[region_index] = module

    def release_region(self, region_index: int):
        if region_index in self._claimed_regions:
            del self._claimed_regions[region_index]

    def is_claimed(self, region_index: int) -> bool:
        return region_index in self._claimed_regions

    def get_claimant(self, region_index: int):
        return self._claimed_regions.get(region_index)

    def update_region(self, region_index: int, new_data: str, new_type: str = None):
        if 0 <= region_index < len(self.context):
            self.context[region_index]["data"] = new_data
            if new_type:
                self.context[region_index]["type"] = new_type

    def mark_claimed_region_finished(self, region_index: int, module: str):
        if region_index in self._claimed_regions and self._claimed_regions[region_index] == module:
            if "claimedSince" in self.context[region_index]:
                del self.context[region_index]["claimedSince"]
            if "module" in self.context[region_index]:
                del self.context[region_index]["module"]
            self.release_region(region_index)

    def to_dict(self):
        def convert(obj):
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert(i) for i in obj]
            elif hasattr(obj, "__dict__"):
                return str(obj)
            else:
                return obj

        return {
            "id": self.id,
            "version": self.VERSION,
            "context": self.context,
            "status_list": convert(self.status_list),
            "logs": self.logs,
            "claimed_regions": self._claimed_regions,
            "loop_count": self.loop_count,
            "tick_count": self.tick_count,
            "awaiting_user_input": self.awaiting_user_input,
        }
