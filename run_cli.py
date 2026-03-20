#!/usr/bin/env python3
import sys
import os
import threading
import time
import json
import signal

mc_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, mc_dir)

os.chdir(mc_dir)

from core import Runtime


def run_ticks(runtime, session, tick_interval=0.1):
    runtime.newloop(session)
    while True:
        runtime.tick()
        time.sleep(tick_interval)


def main():
    runtime = Runtime()
    runtime.auto_register_modules("modules")

    session = runtime.create_session()
    cli_module = runtime.modules.get("cli")
    if cli_module is None:
        raise RuntimeError("Required module 'cli' was not discovered in modules/.")
    cli_module.set_session(session)

    session.append_log("[SYSTEM] CLI session started")

    def save_session(signum, frame):
        session_logs_dir = os.path.join(mc_dir, "session_logs")
        os.makedirs(session_logs_dir, exist_ok=True)
        save_path = os.path.join(session_logs_dir, f"session_{session.id}.json")
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, indent=2)
        print(f"\n[SYSTEM] Session saved to {save_path}")
        sys.exit(0)

    signal.signal(signal.SIGINT, save_session)

    tick_thread = threading.Thread(target=run_ticks, args=(runtime, session), daemon=True)
    tick_thread.start()

    print("=" * 50)
    print("  ModularClaw CLI Demo")
    print("=" * 50)
    print(f"Status page: http://localhost:8080/session/{session.id}")
    print("Type your messages below.")
    print("Commands: exit/quit/q to exit")
    print("=" * 50)
    print()

    cli_module.start_input_loop(session)

    print("\n" + "=" * 50)
    print("Session Logs:")
    print("=" * 50)
    for log in session.logs:
        print(log)

    print("\n" + "=" * 50)
    print("Session Context:")
    print("=" * 50)
    for i, ctx in enumerate(session.get_context()):
        print(f"{i}: [{ctx.get('type')}] {ctx.get('data')[:100]}")


if __name__ == "__main__":
    main()
