# ModularClaw

英文概览：**[README.md](README.md)**。

仅关于**核心框架**的参考（`Runtime`、`Module`、`Session`、`Config` 的每个函数，不含模块目录），见 **[`docs/main/core/README.md`](main/core/README.md)**。

模块协作说明：

- **[`docs/main/sender.md`](main/sender.md)**（中文：[main_zh_cn/sender.md](main_zh_cn/sender.md)）— `Sender` 如何决定调用 API，以及其他模块如何阻塞或放行发送。
- **[`docs/main/executor.md`](main/executor.md)**（中文：[main_zh_cn/executor.md](main_zh_cn/executor.md)）— 面向入门：`Executor` 做什么，以及如何避免 `Sender` 过早发送。
- **[`docs/main/file-system.md`](main/file-system.md)**（中文：[main_zh_cn/file-system.md](main_zh_cn/file-system.md)）— `FileSystem` / `edit_file` 与路径策略。
- **[`docs/main/memory.md`](main/memory.md)**（中文：[main_zh_cn/memory.md](main_zh_cn/memory.md)）— `Memory`：`save_memory` / `search_memory` 与持久化存储。

ModularClaw 是一个模块化、会话驱动的智能体运行时。你可以把 CLI 输入、大模型调用、命令执行、日志和 Web 状态界面等模块接入同一条循环：每个 tick 各模块推进工作，直到系统准备好发起下一次 AI 请求。

## 这个工具做什么

从高层看，本项目是一个智能体编排框架：

- 通过 CLI 模块接收用户输入
- 在共享的 `Session` context 中保存对话与工具数据
- 让大模型模块（`Sender`）读取 context 并生成回复
- 让执行模块（`Executor`）根据结构化工具调用运行 shell 命令
- 让文件系统模块（`FileSystem`，注册名为 `file_system`）处理 `edit_file` 工具调用（在配置的路径策略下进行读写、搜索、列表及相关操作）
- 让记忆模块（`Memory`，注册名为 `memory`）处理 `save_memory` 与 `search_memory` 工具调用（默认写入 `workspace/Memory/` 下 JSON；详见 [main/memory.md](main/memory.md) / [main_zh_cn/memory.md](main_zh_cn/memory.md)）
- 将工具结果写回 context，供大模型继续推理
- 通过小型 Web 面板（`Web`）展示实时状态、context 预览与日志

简而言之：**ModularClaw 在每个 tick 上通过可插拔模块传递共享会话状态，协调多步「AI + 工具」工作流。**

## 架构

```
用户输入 (CLI)
    -> Session Context
    -> 每个 tick 运行模块 (Logger / Executor / FileSystem / Memory / Web / Sender)
    -> 全员就绪时 Sender 调用 AI
    -> AI 输出回到 Session Context
    -> 开始新循环
```

## 核心概念

### Runtime

`Runtime` 负责：

- 已注册的模块
- 活跃会话
- Tick / 循环流程（`tick()`、`newloop()`、`run()`）
- 代码与配置之间的版本校验

### Session

`Session` 是模块读写的共享状态：

- `context`：有序条目（`Text`、`ToolResult` 等）
- `status_list`：按模块划分的就绪/状态标志
- `logs`：有上限的日志历史
- region claim：保证就地更新 context 时的安全
- 计数器：`loop_count`、`tick_count`

### Module

每个模块继承 `Module`，可实现：

- `on_loop(session)`：新循环开始时调用
- `on_tick(session)`：每个运行时 tick 调用

## 内置模块

### `CLI`

- 采集终端输入
- 将用户文本追加到会话 context
- 设置 `user_message_ready` 与模块就绪状态

### `Sender`

- 等待其他模块全部上报 `Ready to send = ready`
- 从会话 context 构造发给 LLM 的消息
- 调用配置的 chat-completions 接口
- 将 AI 回复写回 context 并开启新循环

若未配置 API 密钥，则返回模拟响应。

### `Executor`

扫描 context 中的 JSON 工具调用：

```json
{
  "type": "tool_call",
  "name": "execute_command",
  "arguments": {"command": "ls -la"}
}
```

- 声明该 context 区域（claim）
- 异步执行命令
- 将条目改写为 `ToolResult` 载荷
- 释放 claim 并更新就绪状态

### `FileSystem`（`file_system`）

扫描 context 中的 JSON 工具调用：

```json
{
  "type": "tool_call",
  "name": "edit_file",
  "arguments": {"action": "write", "path": "notes.txt", "content": "Hello"}
}
```

- 支持 `read`、`write`、`append`、`replace`、`list`、`glob`、`search`、`rename`、`delete`、`mkdir`、`metadata` 等动作（详见 [main/file-system.md](main/file-system.md) / [main_zh_cn/file-system.md](main_zh_cn/file-system.md)）
- 按 `config/file_system.json` 中的 `path_policy` 约束路径（`write_scope`、根目录、`allow_read_all_system`）
- 返回结构化 `tool_result`，包含 `ok`、`message`、`path`（必要时含 `content`）

### `Logger`

- 跟踪是否仍有未完成的已声明工作
- 据此更新 `Ready to send` 状态

### `Memory`

- 扫描 context 中的 JSON 工具调用 `save_memory` / `search_memory`（与 `Executor` 相同的 `Text` + `label="json"` 约定）
- 将条目持久化到 `config/memory.json` 所配置的路径（`path_policy.workspace_root` 与 `storage.*`；默认相对目录 `Memory`、文件 `memories.json`）
- 在相关工具调用尚未处理完时将 `Ready to send` 设为 `pending`，清空后为 `ready`

详见 [main_zh_cn/memory.md](main_zh_cn/memory.md)（英文：[main/memory.md](main/memory.md)）。

### `Web`

- 提供极简 HTTP 状态页（`/` 与 `/session/<id>`）
- 展示模块状态、context 预览与最近日志

## 配置

所有配置位于 `config/`，按模块名加载并做版本校验。

### `config/system.json`

- `runtime.tick_interval`：运行循环中的默认休眠间隔
- `runtime.max_sessions`：最大并发会话数（目前多为说明用途）
- `runtime.ticks_per_loop`：用于循环 tick 日志的阈值
- `runtime.max_loops`：会话循环次数上限
- `session.max_context_items`：context 条目上限
- `session.max_logs`：日志条数上限

### 各模块配置

- `config/cli.json`
- `config/sender.json`
- `config/executor.json`
- `config/logger.json`
- `config/file_system.json`
- `config/memory.json`
- `config/web.json`

每个文件中的 `version` 须与对应模块代码里的 `VERSION` 常量一致。

## 快速开始

运行演示 CLI 运行时（推荐：自动发现 `modules/` 下全部模块，并打印带会话 id 的状态页地址）：

```bash
python run_cli.py
```

等价入口（无 Ctrl+C 保存会话等钩子，启动提示较简）：

```bash
python -m modules.cli
```

然后：

1. 在终端输入提示
2. 让模块在后台按 tick 处理
3. 在浏览器打开状态页（例如 `run_cli.py` 会给出 `http://localhost:8080/session/<id>`；单会话时也可使用 `http://localhost:8080`）
4. 输入 `exit` / `quit` / `q` 退出

## 编程方式使用

```python
from core import Runtime
from modules import Sender, Executor, Logger, CLI, Web
from modules.memory import Memory

runtime = Runtime()
runtime.register_module("logger", Logger())
runtime.register_module("sender", Sender())
runtime.register_module("executor", Executor())
runtime.register_module("memory", Memory())
runtime.register_module("cli", CLI())
runtime.register_module("web", Web())

session = runtime.create_session("session-001")
runtime.newloop(session)
```

## 扩展自定义模块

```python
from core import Module
from session import Session

class MyModule(Module):
    VERSION = "1.0.0"

    def on_loop(self, session: Session):
        pass

    def on_tick(self, session: Session):
        session.set_status(self.name, "Ready to send", "ready")
```

注册方式：

```python
runtime.register_module("mymodule", MyModule())
```

添加 `config/mymodule.json`（文件名主干与运行时注册名一致），并写入匹配的 `version`。

## API 参考

### Runtime

- `create_session(session_id=None)`
- `get_session(session_id)`
- `register_module(name, module)`
- `newloop(session)`
- `tick()`
- `run(interval=0.1)`
- `stop()`
- `broadcast(message, session_id=None)`
- `register_callback(event, callback)`
- `emit(event, *args, **kwargs)`

### Session

- `add_context(content_type, data, module=None, claimed_since=None, info=None, label=None)` — 可选 `label`（如 `"json"`）为条目打标，供 Executor 等模块识别。
- `get_context()`
- `set_status(module, key, value)`
- `get_status(module, key=None)`
- `get_all_statuses()`
- `append_log(message)`
- `claim_region(region_index, module)`
- `release_region(region_index)`
- `is_claimed(region_index)`
- `get_claimant(region_index)`
- `update_region(region_index, new_data, new_type=None)`
- `mark_claimed_region_finished(region_index, module)`
