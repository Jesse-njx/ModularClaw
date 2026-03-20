# Runtime（`core.py`）

`Runtime` 类是编排器：它持有所有已注册模块与活跃会话，确保系统配置版本一致，通过 **tick** 与 **loop** 推进运行，并提供一个轻量级内部**发布/订阅**事件通道。

类常量：

- **`VERSION`** — Runtime 实现的语义化版本字符串。初始化时会与系统配置（`Config.load("system")`）中的 `version` 对比；不一致则抛出 `VersionMismatchError`。

## 构造函数

### `__init__(self)`

- 初始化空字典：`modules`、`sessions`、`_callbacks`、`_session_ticks`。在当前 `core.py` 中，`_session_ticks` 构造后未被读取（仅被赋值 `{}`）。
- 从 **`Config.get("system", "runtime", {})`** 读取：
  - **`ticks_per_loop`** — 默认 `10`。会话中 tick 达到该值时，runtime 会追加一条“本轮已达 tick 上限”的日志（它不会自动调用 `newloop`，调用方可自行触发）。
  - **`max_loops`** — 默认 `5`。在 `newloop` 与 `tick` 中用于限制一个会话最多推进多少轮；超过后 runtime 不再推进该会话的 loop / tick 工作。
- 将 **`running`** 设为 `False`。
- 调用 **`_verify_system_version()`**，若系统配置版本与 `Runtime.VERSION` 不一致则启动即失败。

---

## 版本与注册

### `_verify_system_version(self)`（私有）

- 加载 **`Config.load("system")`** 并读取可选字段 **`version`**。
- 若 **`version`** 已设置且不等于 **`Runtime.VERSION`**，则抛出 **`VersionMismatchError`**，错误信息会包含两个版本号。
- 若 **`version`** 缺失，则不做检查。

### `register_module(self, name: str, module)`

- 通过 **`_validate_module_name`** 执行严格命名校验：注册键必须与模块文件 stem 完全一致。  
  - 例如：从 `modules/file_system.py` 导入的类必须注册为 `"file_system"`。
- 若 runtime 键重复（同名模块被注册两次），抛出 `NameConflictError`。
- 调用 **`_verify_module_version(name, module)`**，确保模块配置版本与模块代码版本一致（当两者都存在时）。
- 赋值 **`module._runtime = self`** 与 **`module._name = name`**（供基类 `Module` 的属性访问）。
- 存储 **`self.modules[name] = module`**。

### `_verify_module_version(self, name: str, module)`（私有）

- 若 **`module`** 没有 **`VERSION`** 属性，立即返回（不做配置版本检查）。
- 否则加载 **`Config.load(name)`**。
- 若配置缺失，抛出 `NameConflictError`（严格规则要求存在 `config/{name}.json`）。
- 读取可选字段 **`version`**。
- 若配置 **`version`** 存在且不等于 **`module.VERSION`**，抛出 **`VersionMismatchError`**，信息中会包含模块名与两个版本号。

### `auto_register_modules(self, package_name: str = "modules")`

- 扫描包目录下的模块文件（例如 `modules/*.py`）。
- 对每个文件导入后，选择唯一满足以下条件的类：
  - 是 `Module` 的子类，
  - 且定义在该文件对应模块命名空间内（非重导出）。
- 以**文件 stem**作为注册名实例化并注册每个发现到的模块。
- 遇到非法发现状态会抛出 `NameConflictError`（例如单文件多个 `Module` 子类、重复名称或未发现可注册运行时模块）。

这样可避免在 `run_cli.py` 之类入口中手动 import 并逐个注册模块。

---

## 会话

### `create_session(self, session_id: str = None) -> Session`

- 若 **`session_id`** 为 `None`，生成 **`str(int(time.time() * 1000))`**（毫秒时间戳字符串）。
- 构造 **`Session(session_id)`**，并保存到 **`self.sessions[session_id]`**。
- 对**每个**已注册模块，若该钩子存在，则调用 **`module.on_session_start(session)`**（见 [Module 基类](module-base-class.md)）。
- 返回新建的 **`Session`**。

### `get_session(self, session_id: str) -> Session | None`

- 返回 **`self.sessions.get(session_id)`** —— 找到则返回会话，否则返回 `None`。

### `broadcast(self, message: str, session_id: str = None)`

- 若设置了 **`session_id`**：查找该会话，存在则执行 **`session.append_log(f"[BROADCAST] {message}")`**。
- 若 **`session_id`** 为 `None`：给**所有**会话日志都追加同样带前缀的消息。

---

## 循环与 tick

### `newloop(self, session: Session)`

- 递增 **`session.loop_count`**，并将 **`session.tick_count`** 重置为 `0`。
- 追加日志，标记会话 **`session.id`** 进入第 **`session.loop_count` / `self._max_loops`** 轮。
- 若 **`session.loop_count > self._max_loops`**，记录“达到最大循环数”并 **return**，不再调用 `on_loop`。
- 否则，对每个已注册且定义了 **`on_loop`** 的模块调用 **`module.on_loop(session)`**。

### `tick(self)`

- 遍历 **`list(self.sessions.values())`**（快照副本，迭代时更安全地应对集合变化）。
- 跳过 **`session.loop_count > self._max_loops`** 的会话（按当前设计，不再进行后续 tick 处理）。
- 将 **`session.tick_count`** 加 1。
- 对每个已注册且定义了 **`on_tick`** 的模块调用 **`module.on_tick(session)`**。
- 若 **`session.tick_count >= self._ticks_per_loop`**，追加“本轮达到 tick 上限”的日志（仍不会自动开启新 loop）。

### `run(self, interval: float = 0.1)`

- 设 **`self.running = True`**。
- 进入 **`while self.running:`** 循环：调用 **`self.tick()`**，再 **`time.sleep(interval)`**。
- 这是阻塞式主循环，适合长运行进程；可通过 **`stop()`** 停止。

### `stop(self)`

- 设 **`self.running = False`**，使 **`run()`** 在下一轮迭代退出。

---

## 内部事件（回调）

### `register_callback(self, event: str, callback: Callable)`

- 确保 **`self._callbacks[event]`** 为列表，然后执行 **`append(callback)`**。
- 同一事件可注册多个回调；执行顺序为注册顺序。

### `emit(self, event: str, *args, **kwargs)`

- 若 **`event`** 存在于 **`self._callbacks`**，按顺序调用每个已注册回调：**`callback(*args, **kwargs)`**。
- 未知事件会被忽略（不抛错）。

---

## 关系小结

- **Modules** 会在 **`create_session`**、**`newloop`**、**`tick`** 中通过可选钩子被调用。
- **Sessions** 存储每次运行的状态；runtime 主要在 **`newloop`** / **`tick`** 中更新日志和 loop/tick 计数。
- **Config** 提供 **`system.runtime`** 参数，以及 **`_verify_system_version`** / **`_verify_module_version`** 使用的版本字段。
