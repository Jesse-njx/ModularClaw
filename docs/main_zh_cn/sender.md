# Sender 模块（`modules/sender.py`）

`Sender` 是负责调用聊天 API 的模块（若未设置 API key，则返回一段模拟字符串）。它**不是**单纯按固定计时器发送：只有当会话请求新一轮循环，且所有*其他*已注册模块都报告“可发送”时，它才会尝试发送。

本页解释该门控机制如何工作，以及**你自己的模块如何与 `Sender` 协作或阻塞它**。

---

## 注册名很重要

`Sender` 使用 runtime 模块键（`self.runtime.modules`）读取状态。在当前 runtime 中，名称规则是严格的：这个键应与模块文件 stem 完全一致（例如 `file_system`）。

判断是否可发送时，`Sender` 会遍历 **`self.runtime.modules`**，并对除自身外的每个模块读取：

```text
session.get_status(<该模块名称>, "Ready to send")
```

因此你的模块写状态时应使用注册后的 **`self.name`**，不要硬编码猜测值（除非你有意保持同名镜像）。

---

## 两个开关：`NeedLoop` 与 `Ready to send`

### 1. `session.set_need_loop(True)`

在 `on_tick` 中，如果 `session.needs_loop()` 为 false，`Sender` 会**直接返回**：

```124:125:modules/sender.py
        if not session.needs_loop():
            return
```

所以如果没有任何逻辑把 `NeedLoop` 设为 true，`Sender` 连“就绪检查”都不会做。典型模式是：当用户输入或后续工作需要触发 AI 调用时，某处调用 `session.set_need_loop(True)`（例如 CLI 模块在加入用户消息后会这样做）。

发送成功后，`Sender` 会清除此标记：

```139:141:modules/sender.py
        # Consume the trigger before sending so future sends require fresh work.
        self.pending_confirmation = False
        session.set_need_loop(False)
```

**如何干预：** 如果你想在准备好之前阻止发送，就保持 `NeedLoop` 为 false。若要允许进入就绪检查，在前置工作完成后设为 true。

### 2. `Ready to send` 状态（按模块分别维护）

对 runtime 中其他每个模块，`Sender` 要求：

```text
session.get_status(module_name, "Ready to send") == "ready"
```

只要任一模块给出其他值（例如 `"pending"`）或状态缺失，`all_ready` 就为 false，不会发起 API 调用。

**如何干预：**

- **阻塞发送：** `session.set_status(self.name, "Ready to send", "pending")`（或任何不是 `"ready"` 的值）。
- **放行你的模块：** `session.set_status(self.name, "Ready to send", "ready")`。

你可以在同一模块命名空间下使用额外状态键（`set_status(self.name, "something_else", ...)`）实现自己的逻辑；除非你同时影响 `"Ready to send"`，否则不会影响该门控。

---

## `pending_confirmation`（内部闩锁）

每次 `on_loop` 时，`Sender` 都会设置 `self.pending_confirmation = True`，并广播 / 发出事件：

```118:121:modules/sender.py
    def on_loop(self, session: Session):
        self.runtime.broadcast(f"[{self.name}] Waiting for confirmation to send", session.id)
        self.pending_confirmation = True
        self.runtime.emit("sender_waiting", session.id)
```

真正发送仅在 **`all_ready and self.pending_confirmation`** 时执行。`_send_to_ai` 启动后会把 `pending_confirmation = False`，因此在下一次 `on_loop` 再次“上闩”前，不会触发下一次发送。

**实际效果：** 即便全部状态都是 `"ready"`，也需要一次**新循环**（来自 `runtime.newloop(session)`）来重新上闩。其他模块通常通过调用 `set_need_loop(True)` 并让 runtime 开新循环来驱动这件事；而最近一次成功发送本身也会调用 `newloop`。

**外部如何干预：** 可通过 `runtime.register_callback("sender_waiting", your_fn)` 订阅事件。回调会收到 `session_id`，可用于 UI 协调、日志记录或在下一 tick 前调整状态。若不修改/继承 `Sender`，无法直接从外部设置 `pending_confirmation`——应通过 **`NeedLoop`** 和 **`Ready to send`** 来控制。

---

## `Sender` 从 context 读取什么

构造 API 请求时，`_send_to_ai` 会遍历 `session.get_context()`，只映射以下类型：

| Context `type`        | 消息中的角色                           |
|-----------------------|----------------------------------------|
| `Text`                | `user`                                 |
| `ProtectedText`       | `assistant`                            |
| `ToolResult`          | `tool`（`content = data`）             |

其他类型都会被跳过，不会进入 HTTP `messages` 列表。因此，**如何干预输入内容：** 在发送前新增或改写这些类型的 context 项；若希望自定义类型也被发送，则需要扩展 `Sender`。

---

## `Sender` 会写回什么

模型回复字符串会被切分为普通文本段和有效 JSON 段（`_split_response_into_segments`）。JSON 段会以 `Text` 类型存储，并标记 **`label="json"`**——这正是 executor 用来识别工具调用的标记。普通文本段则是未标记的 `Text`。

**如何干预：** 若其他模块希望在执行前消费模型输出，和 `Sender` 同 tick 的先后顺序并不能解决问题——因为输出是 `Sender` 产生的。应在发送后的后续 tick 处理，或使用 `newloop`/回调，或在你的模块 `on_tick` 中对新出现的 context 做后处理。

---

## 配置入口

构造函数会从 `Config.get("sender", "api", {})` 读取默认配置：API URL、模型、超时、temperature、max tokens、key。缺少 key 时会走模拟文本而不是 HTTP 请求。

---

## 给模块作者的速查表

| 目标                          | 常见操作 |
|-------------------------------|----------|
| 允许 `Sender` 进入发送判断     | `session.set_need_loop(True)` |
| 暂停整条流水线                | `set_status(self.name, "Ready to send", "pending")` |
| 释放你的模块门控             | `set_status(self.name, "Ready to send", "ready")` |
| 监听“已进入待发送”时机        | `runtime.register_callback("sender_waiting", ...)` |
| 控制模型看到的内容            | 在 context 中新增/更新 `Text` / `ProtectedText` / `ToolResult` |

executor 与 logger 模块是通过 `Ready to send` 和 claimed regions 实现“干预”的两个具体例子；executor 侧的入门讲解见 [`executor.md`](executor.md)。
