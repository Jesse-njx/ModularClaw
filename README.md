# ModularClaw

[中文说明（zh-CN）](https://github.com/Jesse-njx/ModularClaw/blob/main/README.zh-CN.md)

This is a minimalistic agentic AI platform. The core code is well under 300 lines, along with some sample modules that run the whole baseline system. 

This system is made for educational purposes. It is definitely not the best system out there for actual work.

This document is typed manually by a human. It's optimized for readability and intuition. If you want a more technical/detailed description of what everything does, read `/docs`.

# To begin

An API key is needed for real model calls, but the system can still run without one (the sender will simulate an empty response). It is intentionally crude and may require your own implementation details. `/modules/sender.py` is responsible for contacting AI services, and `/config/sender.json` is where you input your API key and model settings.

To copy this repo's source code onto your local device, run:
`git clone https://github.com/Jesse-njx/ModularClaw.git`
Then enter the project folder with:
`cd ModularClaw`
Or download it as a ZIP file from:
https://github.com/Jesse-njx/ModularClaw/archive/refs/heads/main.zip

Then start it with:
```bash
python run_cli.py
```
Then open the status page shown in the CLI output (usually `http://localhost:8080`). The CLI itself does not stream full backend state, so use the web page to inspect context/status/logs.

# Architecture

Imagine this system as a workbench with a large shared document on it. Multiple workers can edit that document, stick notes onto the table, and append entries to a log.

Here is a list of common terms used in this system.

- Workbench: Runtime. This manages the whole system.
- Word Document: Session. This contains all information in one "conversation".
- "A lot of people": Modules. This is what you will most likely create/edit.
- Sticky Notes: Status. Each module can set status values, and other modules can read them.
- Logs: Logs. Modules append messages to this list.

# Pipeline

There are two parts to the runtime cycle. If you are new to agentic systems, use this short description:

Ticking: Every `0.1` seconds by default (configurable in `/config/system.json`), the runtime calls `on_tick()` on all modules.

Looping: A new loop starts at session start and after each sender completion (`Runtime.newloop(...)`), which runs each module's `on_loop()`.

At a high level, modules interact, process context, and do non-LLM work. When modules are done, they mark `"Ready to send"` as `"ready"` (for example, `session.set_status(self.name, "Ready to send", "ready")`). At least one module must also trigger `session.set_need_loop(True)`. Then `sender` sees the trigger, verifies other modules are ready, sends `context` to the model, and appends the model result back into `context` (with automatic JSON segment detection).

Many status and context conventions are not strictly enforced by the runtime. Follow conventions consistently and avoid directly interfering with another module's claimed work.

# Building your first module

All modules start with `core.Module`.

Use this quick pattern:

1. Create a file in `modules/` (example: `modules/my_module.py`)
2. Create a class that inherits from `Module`
3. Give it a `VERSION` string
4. Implement `on_tick()` (and optionally `on_loop()` / `on_session_start()`)
5. Add a matching config file in `config/` with the same version
6. Register it in your runtime

## Step 1: Create your module file

```python
from core import Module
from session import Session


class MyModule(Module):
    VERSION = "1.0.0"

    def on_session_start(self, session: Session):
        session.append_log(f"[{self.name}] Session started")

    def on_loop(self, session: Session):
        # Called whenever a new loop starts
        session.set_status(self.name, "Ready to send", "pending")

    def on_tick(self, session: Session):
        # Called every tick (default: every 0.1s)
        context = session.get_context()
        if context:
            session.set_status(self.name, "Ready to send", "ready")
```

## Step 2: Add module config

Create `config/my_module.json`:

```json
{
  "version": "1.0.0"
}
```

The `version` must match your class `VERSION`, or startup will fail.

## Step 3: Register your module

```python
from core import Runtime
from modules.my_module import MyModule

runtime = Runtime()
runtime.register_module("my_module", MyModule())
```

Important naming rule: the string in `register_module("my_module", ...)` must exactly match the module file name (`my_module.py`).

## Step 4: Run and verify

- Start the runtime (`python run_cli.py` in this repo)
- Open `http://localhost:8080`
- Check your module status and logs

If your module is registered correctly, you should see it in the dashboard and logs on each loop/tick.


# Contact me

If you know me in person, just DM me on Wechat. If not, you can email jessedd777@hotmail.com. A discord server will be set up if this draws attention.