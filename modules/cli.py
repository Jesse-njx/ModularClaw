import sys
import threading
import time
from core import Module
from config_loader import Config
from session import Session


class CLI(Module):
    VERSION = "1.0.0"

    def __init__(self):
        super().__init__()
        cli_config = Config.get("cli", "settings", {})
        self.prompt = cli_config.get("prompt", ">>> ")
        self.echo = cli_config.get("echo", True)
        self._input_thread = None
        self._running = False
        self._current_session = None

    def set_session(self, session: Session):
        self._current_session = session

    def on_loop(self, session: Session):
        self._current_session = session

    def _has_user_text(self, session: Session) -> bool:
        return any(item.get("type") == "UserText" for item in session.get_context())

    def on_tick(self, session: Session):
        if session.awaiting_user_input:
            session.set_status(self.name, "Ready to send", "pending")
            return
        if not self._has_user_text(session):
            session.set_status(self.name, "Ready to send", "pending")
            return
        context = session.get_context()
        has_claimed = any(session.is_claimed(i) for i in range(len(context)))

        if not has_claimed:
            session.set_status(self.name, "Ready to send", "ready")

    def start_input_loop(self, session: Session = None):
        if session is None:
            session = self._current_session
        if session is None:
            return

        self._running = True
        self._current_session = session

        while self._running:
            try:
                if self.prompt:
                    user_input = input(self.prompt)
                else:
                    user_input = input()

                if user_input.strip().lower() in ("exit", "quit", "q"):
                    self._running = False
                    session.append_log(f"[CLI] Exiting CLI mode")
                    break

                if user_input.strip():
                    self._process_input(user_input, session)

            except EOFError:
                self._running = False
                break
            except KeyboardInterrupt:
                self._running = False
                session.append_log(f"[CLI] Interrupted by user")
                break
            except Exception as e:
                session.append_log(f"[CLI] Error: {e}")

    def _process_input(self, user_input: str, session: Session):
        session.awaiting_user_input = False
        session.add_context("UserText", user_input)
        session.set_status(self.name, "user_message_ready", True)
        session.set_status(self.name, "Ready to send", "ready")
        session.append_log(f"[CLI] Added user message to session")

        if self.echo:
            print(f"You: {user_input}")

    def stop(self):
        self._running = False


def create_cli_runtime():
    from core import Runtime

    runtime = Runtime()
    runtime.auto_register_modules("modules")

    return runtime


def main():
    runtime = create_cli_runtime()

    session = runtime.create_session()
    cli_module = runtime.modules.get("cli")
    cli_module.set_session(session)
    tick_interval = Config.get("system", "runtime", {}).get("tick_interval", 0.1)

    session.append_log("[SYSTEM] CLI session started")
    runtime.newloop(session)

    def tick_worker():
        while cli_module._running:
            runtime.tick()
            time.sleep(tick_interval)

    print("ModularClaw CLI Demo")
    print("Type your messages below. Commands: exit/quit/q to exit.\n")

    cli_module._running = True
    tick_thread = threading.Thread(target=tick_worker, daemon=True)
    tick_thread.start()
    cli_module.start_input_loop(session)
    tick_thread.join(timeout=1.0)

    print("\nSession ended. Logs:")
    for log in session.logs[-20:]:
        print(log)


if __name__ == "__main__":
    main()
