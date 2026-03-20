# Module 基类（`core.py`）

`Module` 是 **`Runtime`** 期望的抽象形状：一个带有可选生命周期钩子、并在注册后注入 **`_runtime`** / **`_name`** 的小对象。

类常量：

- **`VERSION`** — 可选但推荐设置。若存在，**`Runtime.register_module`** 会将其与该模块 JSON 配置中的 **`version`**（按模块名加载）比较；不一致时抛出 **`VersionMismatchError`**。

## 构造函数

### `__init__(self)`

- 设置 **`self._runtime = None`** 与 **`self._name = None`**。Runtime 会在 **`register_module`** 中覆盖它们。

---

## 属性

### `runtime` → `Runtime`

- 返回 **`self._runtime`**。注册后为所属 runtime 实例；注册前为 `None`。

### `name` → `str | None`

- 返回 **`self._name`**。注册后为传给 **`register_module`** 的字符串键；注册前为 `None`。

---

## 生命周期钩子

三个钩子默认都是**空操作**（`pass`）。子类可按需选择性重写。

### `on_loop(self, session: Session)`

- 由 **`Runtime.newloop`** 调用：仅当 **`hasattr(module, 'on_loop')`** 为真。基类 **`Module`** 已定义 **`on_loop`**，因此常规 **`Module`** 子类会在每次新 loop 被调用（若未重写则执行默认空操作）。异常场景（如注册非 `Module` 对象）可能缺少此属性。
- 用于处理会话**新一轮开始时**的工作。调用钩子前，runtime 已先递增 **`session.loop_count`** 并重置 **`session.tick_count`**。

### `on_tick(self, session: Session)`

- 由 **`Runtime.tick`** 按同样的 **`hasattr(module, 'on_tick')`** 规则调用。基类已定义 **`on_tick`**，因此常规子类会在每个 tick 被调用，除非实例以非常规方式移除了该属性（少见）。
- 用于每个全局 tick 中的轻量周期性工作。

### `on_session_start(self, session: Session)`

- 由 **`Runtime.create_session`** 在新会话创建时对**所有**注册模块调用。
- 用于每会话初始化（订阅、初始状态设置等）。

---

## 契约摘要

| 职责 | 负责方 |
|----------------|--------|
| 注册模块 | `Runtime.register_module` 的调用者 |
| 设置 `_runtime` / `_name` | `Runtime.register_module`（不要依赖手工赋值） |
| 实现钩子 | `Module` 子类 |
| 版本对齐 | 当两者都设置时：`Module.VERSION` 与配置 `version` 一致 |

本文不列举 `modules/` 下具体模块，只描述它们所继承的基类类型。
