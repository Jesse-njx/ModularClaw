# ModularClaw

这是一个极简的 Agentic AI 平台。核心代码远少于 300 行，并配有一些示例模块来跑通整个基础系统。

这个系统主要用于教学演示。它并不是最适合生产环境工作的系统。

这份文档是由人手动编写的，重点是可读性和直觉理解。如果你想看更技术化、更详细的说明，请阅读 `/docs`。

# 开始使用

进行真实模型调用需要 API key，但即使没有也能运行（`sender` 会模拟空响应）。本项目本身是有意保持“粗糙”的，你可能需要根据自己的需求补充实现细节。`/modules/sender.py` 负责调用 AI 服务，`/config/sender.json` 用于填写 API key 和模型配置。

把这个仓库的源码复制到本地设备：
`git clone https://github.com/Jesse-njx/ModularClaw.git`
然后进入项目目录：
`cd ModularClaw`
也可以直接下载 ZIP：
https://github.com/Jesse-njx/ModularClaw/archive/refs/heads/main.zip

然后启动：
```bash
python run_cli.py
```
再打开 CLI 输出里显示的状态页地址（使用 `run_cli.py` 时一般为 `http://localhost:8080/session/<会话 id>`）。CLI 本身不会完整展示后端状态，所以建议通过网页查看 context/status/logs。

# 架构

你可以把这个系统想象成一个工作台，上面有一份共享的大文档。多个“工人”可以编辑文档、往桌上贴便签，并往日志里追加记录。

下面是系统中的常见术语：

- 工作台：Runtime。负责管理整个系统。
- 大文档：Session。保存一次“对话”中的所有信息。
- “很多工人”：Modules。你最常创建/修改的就是它们。
- 便签：Status。每个模块都可以写状态值，其他模块可以读取。
- 日志：Logs。模块会把消息追加到这里。

# 流程（Pipeline）

运行周期可以简单分成两部分。如果你刚接触 agentic 系统，可以先按下面理解：

Ticking：默认每 `0.1` 秒一次（可在 `/config/system.json` 配置），Runtime 会调用所有模块的 `on_tick()`。

Looping：会话启动时会开始一个新 loop；每次 `sender` 完成后也会触发新的 loop（`Runtime.newloop(...)`），并调用每个模块的 `on_loop()`。

整体逻辑是：模块之间协作、处理 context、完成非 LLM 的工作。各模块用 `"Ready to send"` 的 `"ready"` / `"pending"` 表示当前是否适合调用模型。**CLI** 负责与人相关的门控：在 context 中尚未出现 `UserText` 时保持 `"pending"`；在 `user_input` 工具执行后 `session.awaiting_user_input` 为真时也保持 `"pending"`，直到用户再次输入。**`file_system`**、**`executor`**、**`logger`** 在工具执行或 claimed 区域未清完时为 `"pending"`，完成后为 `"ready"`。**`sender`** 在其余模块均为 `"ready"` 且自身 `pending_confirmation` 已上闩时立即发送（每次 `newloop` 后上闩）。若模型需要等待用户，应发出 `user_input` 工具调用（见 `config/sender.json`）。

很多状态和 context 约定并不是由 Runtime 严格强制的。请尽量遵守约定，不要直接干扰其他模块已声明（claimed）的工作区域。

# 创建你的第一个模块

所有模块都从 `core.Module` 开始。

可以用下面这个快速模式：

1. 在 `modules/` 里新建文件（例如：`modules/my_module.py`）
2. 创建一个继承 `Module` 的类
3. 添加 `VERSION` 字符串
4. 实现 `on_tick()`（可选实现 `on_loop()` / `on_session_start()`）
5. 在 `config/` 下创建同版本配置文件
6. 确保 runtime 会加载你的模块（用 `run_cli.py` 时通常自动完成；见第 3 步）

## 第 1 步：创建模块文件

```python
from core import Module
from session import Session


class MyModule(Module):
    VERSION = "1.0.0"

    def on_session_start(self, session: Session):
        session.append_log(f"[{self.name}] Session started")

    def on_loop(self, session: Session):
        # 每次新 loop 开始时调用
        session.set_status(self.name, "Ready to send", "pending")

    def on_tick(self, session: Session):
        # 每个 tick 调用一次（默认每 0.1 秒）
        context = session.get_context()
        if context:
            session.set_status(self.name, "Ready to send", "ready")
```

## 第 2 步：添加模块配置

创建 `config/my_module.json`：

```json
{
  "version": "1.0.0"
}
```

`version` 必须和类里的 `VERSION` 一致，否则启动会失败。

## 第 3 步：注册模块

**若使用本仓库的入口（`python run_cli.py` 或 `python -m modules.cli`）：** 一般**不需要**手写注册代码。两者都会调用 `Runtime.auto_register_modules("modules")`（见 `run_cli.py` 与 `modules/cli.py`），会导入 `modules/` 下每个不以 `_` 开头的 `.py`，并注册其中唯一的 `Module` 子类。只要放好 `modules/my_module.py` 和 `config/my_module.json` 即可。

**若在你自己的程序里使用 `Runtime`：** 在构造 runtime 的地方注册——通常在 `runtime = Runtime()` 之后、`create_session` 之前，可以对每个模块调用 `register_module`，或在仍使用 `modules/` 包布局时调用 `auto_register_modules("modules")`：

```python
from core import Runtime
from modules.my_module import MyModule

runtime = Runtime()
runtime.register_module("my_module", MyModule())
```

重要命名规则：`register_module("my_module", ...)` 里的字符串必须与模块文件名主干一致（`my_module.py` → `"my_module"`）。自动注册时 runtime 名称同样是去掉 `.py` 的文件名。

## 第 4 步：运行并验证

- 启动 runtime（本仓库中使用 `python run_cli.py`）
- 打开终端里打印的状态页地址（一般为 `http://localhost:8080/session/<会话 id>`；若只有一个会话，访问 `/` 也会落到该会话）
- 检查你的模块状态和日志

如果模块注册正确，你会在仪表盘和日志中看到它在每个 loop/tick 的行为。


# 联系我

如果你现实中认识我，直接微信私信即可。如果不认识，可以发邮件到 jessedd777@hotmail.com。若项目关注度上来，我会建一个 Discord 服务器。
