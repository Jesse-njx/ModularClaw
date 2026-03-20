# Session（`session.py`）

**`Session`** 是单次运行的状态容器：包含**上下文**（有序条目）、按模块键控的**状态**、**日志**、针对 context 索引的**已声明区域**，以及用于 **loop/tick** 的计数与标志。它从 **`Config.get("system", "session", {})`** 和 **`Config.get("system", "runtime", {})`** 读取限制参数。

类常量：

- **`VERSION`** — 在 **`to_dict()`** 中导出，便于序列化或调试。

## 构造函数

### `__init__(self, id: str)`

- **`self.id`** — 此会话的外部标识符。
- **`self.context`** — 上下文条目列表（见 **`add_context`** / **`update_region`**）。
- **`self.status_list`** — 嵌套字典：模块名 → 键 → 值。
- **`self.logs`** — 带时间戳字符串列表（见 **`append_log`**）。
- **`self._claimed_regions`** — context 索引 → 模块名的映射（见 claim 相关辅助方法）。
- 来自 **`system.session`** 配置：**`max_context_items`**（默认 `1000`）、**`max_logs`**（默认 `5000`）。
- **`self.loop_count`**、**`self.tick_count`** — 初始为 `0`；通常由 **`Runtime`** 更新。
- **`self._loop_limit`** — 来自 **`system.runtime.max_loops`**（默认 `5`）；供希望在 session 侧使用 loop 上限概念的调用方使用（runtime 同时也有自己的 `_max_loops`）。
- **`self.NeedLoop`** — 布尔标记；可通过 **`set_need_loop`** / **`needs_loop`** 让外部驱动决定是否开启下一轮循环。

---

## 上下文（Context）

### `add_context(self, content_type: str, data: str, module: str = None, claimed_since: int = None, info: dict = None, label: str = None) -> int`

- 若 **`len(self.context) >= self._max_context`**，先通过 **`pop(0)`** 删除**最旧**条目（FIFO 淘汰）。
- 追加字典：**`type`**、**`data`**，并按需包含 **`label`**。
- 若提供 **`module`**，还会写入 **`module`**、**`claimedSince`**、**`info`**（默认空字典），用于将该条目与声明模块及元数据绑定。
- 返回新增条目的 **`self.context`** 索引（`len - 1`）。

### `get_context(self) -> list`

- 返回 **`self.context`**（是活对象；修改返回值会直接修改会话状态）。

### `update_region(self, region_index: int, new_data: str, new_type: str = None)`

- 若 **`0 <= region_index < len(self.context)`**，设置 **`context[region_index]["data"] = new_data`**。
- 若 **`new_type`** 为真值，同时设置 **`context[region_index]["type"] = new_type`**。

---

## 状态（Status）

### `set_status(self, module: str, key: str, value)`

- 确保 **`self.status_list[module]`** 存在，然后写入 **`status_list[module][key] = value`**。

### `get_status(self, module: str, key: str = None)`

- 若 **`module`** 不在 **`status_list`** 中，返回 **`None`**。
- 若 **`key`** 为 **`None`**，返回该模块完整状态字典。
- 否则返回 **`status_list[module].get(key)`**（键缺失时隐式 `None`）。

### `get_all_statuses(self) -> dict`

- 返回 **`self.status_list`**（活的嵌套字典对象）。

---

## 日志（Logs）

### `append_log(self, message: str)`

- 若 **`len(self.logs) >= self._max_logs`**，先通过 **`pop(0)`** 丢弃最旧日志。
- 追加 **`f"[{int(time.time())}] {message}"`**（前缀为 Unix 秒级整数时间戳）。

---

## 区域声明（Region claims）

区域是 **`self.context`** 中的**索引**。**`_claimed_regions`** 维护“索引 → 所有者模块名”映射。

### `claim_region(self, region_index: int, module: str)`

- 设置 **`self._claimed_regions[region_index] = module`**。

### `release_region(self, region_index: int)`

- 若 **`region_index`** 存在，则执行 **`del self._claimed_regions[region_index]`**。

### `is_claimed(self, region_index: int) -> bool`

- 返回 **`region_index`** 是否存在于 **`_claimed_regions`**。

### `get_claimant(self, region_index: int)`

- 返回 **`self._claimed_regions.get(region_index)`**（模块名或 `None`）。

### `mark_claimed_region_finished(self, region_index: int, module: str)`

- 当 **`region_index`** 已被声明，且声明者等于 **`module`** 时：
  - 若存在，则从 **`self.context[region_index]`** 中移除 **`claimedSince`** 与 **`module`** 键（条目本身保留，仅清这两个键）。
  - 调用 **`release_region(region_index)`**。

---

## 循环控制标记

### `set_need_loop(self, value: bool = True)`

- 设置 **`self.NeedLoop = bool(value)`**。

### `needs_loop(self) -> bool`

- 返回 **`self.NeedLoop`**。

runtime **不会**自动读取此标记；通常由驱动层或模块逻辑决定是否据此调用 **`Runtime.newloop`**。

---

## 序列化

### `to_dict(self) -> dict`

- 定义内部函数 **`convert(obj)`**：
  - 字典 → 递归处理值。
  - 列表 → 递归处理元素。
  - 具备 **`__dict__`** 的对象 → **`str(obj)`**（字符串兜底，而非深层结构）。
  - 标量 → 原样返回。
- 返回字典字段包括：**`id`**、**`version`**、**`context`**、**`status_list`**（经 **`convert`**）、**`logs`**、**`claimed_regions`**、**`loop_count`**、**`tick_count`**、**`need_loop`**（`NeedLoop` 序列化为 **`need_loop`**）。

可用于快照、API 输出或调试；对于 **`status_list`** 中的任意对象，不保证能在 **`convert`** 规则之外完整往返还原。
