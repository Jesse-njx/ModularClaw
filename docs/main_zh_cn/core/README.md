# 核心框架

本目录记录 ModularClaw 的**核心层**：运行时编排、可插拔模块契约、按会话隔离的状态，以及配置加载。这里有意**不**描述 `modules/` 下的具体模块，只覆盖它们接入的 API。

## 组件如何配合

| 组件 | 角色 |
|--------|------|
| [`Runtime`](runtime.md) | 管理已注册模块与会话；驱动 tick、loop、可选主循环，以及内部事件。 |
| [`Module`](module-base-class.md) | 运行时调用对象的基类；子类按需重写生命周期钩子。 |
| [`Session`](session.md) | 一次逻辑运行的可变状态（context、日志、状态、region claim、loop 标记）。 |
| [`Config` / `VersionMismatchError` / `NameConflictError`](config-loader.md) | Runtime 与 Session 使用的 JSON 配置发现、版本校验与命名冲突守卫。 |

高层上：Runtime 负责注册模块、创建会话，并反复调用 `on_tick`（以及通过 `newloop` 可选调用 `on_loop`）。Session 承载模块读写的数据。配置通过 `Config` 从 `config/*.json` 读取。

当前命名规则是严格的：模块文件名 stem、runtime 注册名、配置文件名 stem 必须完全一致（例如 `modules/file_system.py` -> `file_system` -> `config/file_system.json`）。

## 文档索引

- [Runtime (`core.py`)](runtime.md) — `Runtime` 类
- [Module 基类 (`core.py`)](module-base-class.md) — `Module` 类
- [Session (`session.py`)](session.md) — `Session` 类
- [Config 加载器 (`config_loader.py`)](config-loader.md) — `Config`、`VersionMismatchError`、`NameConflictError`

## 源文件（参考）

| 文档 | Python 模块 |
|----------|----------------|
| Runtime、Module | `core.py` |
| Session | `session.py` |
| Config | `config_loader.py` |
