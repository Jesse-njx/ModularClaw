# 配置加载器（`config_loader.py`）

配置从项目 **`config/`** 目录下的 JSON 文件读取（路径相对于 **`config_loader.py`** 所在包解析）。**`Runtime`** 与 **`Session`** 通过 **`Config.get`** 读取值，这些文件中的版本字段也参与启动期校验。

---

## `Config`

类级缓存与路径：

- **`_configs`** — 逻辑配置名 → 已解析 JSON **`dict`** 的映射，由 **`load`** 填充。
- **`_config_dir`** — 指向 **`.../ModularClaw/config`** 的绝对路径。

### `load(cls, name: str) -> dict`

- **`name`** — 逻辑键名（如 **`"system"`**、**`"executor"`**）。文件匹配是**严格且精确**的：`Config.load("executor")` 会查找 `config/executor.json`（大小写与拼写都一致）。
- 若 **`name`** 已在 **`_configs`** 中，直接返回缓存字典（不读磁盘）。
- 否则按精确 stem 构造 **`config/{name}.json`**。若文件**存在**，读取 JSON，保存到 **`_configs[name]`** 并返回。
- 若文件**不存在**，返回 **`{}`**（在当前代码中不会缓存 miss，后续调用仍会访问文件系统）。

在加载前，配置加载器会先索引所有配置文件 stem，并检查仅大小写不同的冲突。若存在如 `Sender.json` 与 `sender.json` 这样的组合，会抛出 `NameConflictError`。

### `get(cls, name: str, key: str = None, default=None)`

- 先加载 **`config = cls.load(name)`**（可能来自缓存，也可能来自磁盘）。
- 若 **`key`** 为 **`None`**，返回该 **`name`** 的完整配置字典。
- 若提供 **`key`**，返回 **`config.get(key, default)`** —— 当键缺失或文件缺失（空字典）时使用 **`default`**。

核心中的常见调用：

- **`Config.get("system", "runtime", {})`** — runtime 的 tick/loop 设置。
- **`Config.get("system", "session", {})`** — session 的容量限制。
- **`Config.load("system")`** — 读取完整 system 配置，做 **`version`** 校验。

---

## `VersionMismatchError`

### `class VersionMismatchError(Exception)`

- **`Exception`** 的空子类，用作版本不一致的**专用异常类型**。
- 当 **`system`** 配置中的 **`version`** 与 **`Runtime.VERSION`** 不一致时，由 **`Runtime._verify_system_version`** 抛出。
- 当模块配置 **`version`** 与 **`module.VERSION`** 不一致（且两者都定义）时，由 **`Runtime._verify_module_version`** 抛出。

调用方可以专门捕获 **`VersionMismatchError`**，把配置版本错误与其他失败类型区分处理。

---

## `NameConflictError`

### `class NameConflictError(Exception)`

- 当配置命名违反严格唯一性规则时抛出（例如两个文件 lowercase stem 相同但大小写不同）。
- runtime 的模块注册路径中也会使用该异常表示模块/配置命名冲突。

---

## 文件命名约定

| `load(name)` 参数 | 期望文件 |
|------------------------|----------------|
| `"system"` | `config/system.json` |
| `"executor"` | `config/executor.json` |
| `"file_system"` | `config/file_system.json` |

当前没有大小写转换。模块注册名、模块文件名 stem、配置文件名 stem 必须严格一致。
