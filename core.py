import time
import inspect
import importlib
import pkgutil
from typing import Dict, List, Callable, Any
from session import Session
from config_loader import Config, VersionMismatchError, NameConflictError


class Runtime:
    VERSION = "1.1.0"

    def __init__(self):
        self.modules = {}
        self.sessions = {}
        self.running = False
        self._callbacks = {}
        self._session_ticks = {}
        self._ticks_per_loop = Config.get("system", "runtime", {}).get("ticks_per_loop", 10)
        self._max_loops = Config.get("system", "runtime", {}).get("max_loops", 5)
        self._verify_system_version()

    def _verify_system_version(self):
        system_config = Config.load("system")
        system_version = system_config.get("version")
        if system_version and system_version != self.VERSION:
            raise VersionMismatchError(
                f"System version mismatch: code={self.VERSION}, config={system_version}"
            )

    def register_module(self, name: str, module):
        self._validate_module_name(name, module)
        self._verify_module_version(name, module)
        module._runtime = self
        module._name = name
        if name in self.modules:
            raise NameConflictError(f"Module name conflict: '{name}' is already registered.")
        self.modules[name] = module

    def auto_register_modules(self, package_name: str = "modules"):
        package = importlib.import_module(package_name)
        if not hasattr(package, "__path__"):
            raise ValueError(f"'{package_name}' is not a package directory.")

        discovered = []
        for module_info in pkgutil.iter_modules(package.__path__):
            file_stem = module_info.name
            if file_stem.startswith("_"):
                continue
            full_module_name = f"{package_name}.{file_stem}"
            imported = importlib.import_module(full_module_name)

            module_class = self._pick_runtime_module_class(imported, full_module_name)
            if module_class is None:
                continue

            discovered.append((file_stem, module_class))

        if not discovered:
            raise NameConflictError(f"No runtime modules found in package '{package_name}'.")

        for file_stem, module_class in sorted(discovered, key=lambda item: item[0]):
            instance = module_class()
            self.register_module(file_stem, instance)

    def _verify_module_version(self, name: str, module):
        if not hasattr(module, 'VERSION'):
            return

        config = Config.load(name)
        if not config:
            raise NameConflictError(
                f"Missing config for module '{name}'. Expected 'config/{name}.json'."
            )
        config_version = config.get("version")

        if config_version and config_version != module.VERSION:
            raise VersionMismatchError(
                f"Module '{name}' version mismatch: code={module.VERSION}, config={config_version}"
            )

    def _validate_module_name(self, name: str, module):
        module_obj = inspect.getmodule(module)
        if module_obj is None or not getattr(module_obj, "__name__", ""):
            raise NameConflictError("Unable to resolve module source for registration.")

        source_name = module_obj.__name__.split(".")[-1]
        if name != source_name:
            raise NameConflictError(
                f"Strict naming violation: register name '{name}' must exactly match module file name '{source_name}'."
            )

    def _pick_runtime_module_class(self, imported_module, full_module_name: str):
        candidates = []
        for _, obj in inspect.getmembers(imported_module, inspect.isclass):
            if not issubclass(obj, Module) or obj is Module:
                continue
            if obj.__module__ != full_module_name:
                continue
            candidates.append(obj)

        if not candidates:
            return None

        if len(candidates) > 1:
            raise NameConflictError(
                f"Module file '{full_module_name}' has multiple Module subclasses. Keep one per file."
            )

        return candidates[0]

    def create_session(self, session_id: str = None) -> Session:
        if session_id is None:
            session_id = str(int(time.time() * 1000))
        session = Session(session_id)
        self.sessions[session_id] = session

        for module in self.modules.values():
            module.on_session_start(session)

        return session

    def get_session(self, session_id: str) -> Session:
        return self.sessions.get(session_id)

    def broadcast(self, message: str, session_id: str = None):
        if session_id:
            session = self.get_session(session_id)
            if session:
                session.append_log(f"[BROADCAST] {message}")
        else:
            for session in self.sessions.values():
                session.append_log(f"[BROADCAST] {message}")

    def newloop(self, session: Session):
        session.loop_count += 1
        session.tick_count = 0
        session.append_log(f"[NEWLOOP] Starting loop {session.loop_count}/{self._max_loops} for session {session.id}")
        
        if session.loop_count > self._max_loops:
            session.append_log(f"[SYSTEM] Max loops ({self._max_loops}) reached, stopping session {session.id}")
            return
        
        for module in self.modules.values():
            if hasattr(module, 'on_loop'):
                module.on_loop(session)

    def register_callback(self, event: str, callback: Callable):
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)

    def emit(self, event: str, *args, **kwargs):
        if event in self._callbacks:
            for callback in self._callbacks[event]:
                callback(*args, **kwargs)

    def tick(self):
        for session in list(self.sessions.values()):
            if session.loop_count > self._max_loops:
                continue
            
            session.tick_count += 1
            
            for module in self.modules.values():
                if hasattr(module, 'on_tick'):
                    module.on_tick(session)
            
            if session.tick_count >= self._ticks_per_loop:
                session.append_log(f"[SYSTEM] Tick limit ({self._ticks_per_loop}) reached for loop {session.loop_count}")

    def run(self, interval: float = 0.1):
        self.running = True
        while self.running:
            self.tick()
            time.sleep(interval)

    def stop(self):
        self.running = False


class Module:
    VERSION = "1.1.0"

    def __init__(self):
        self._runtime = None
        self._name = None

    @property
    def runtime(self) -> Runtime:
        return self._runtime

    @property
    def name(self) -> str:
        return self._name

    def on_loop(self, session: Session):
        pass

    def on_tick(self, session: Session):
        pass

    def on_session_start(self, session: Session):
        pass
