# File system 模块（`modules/file_system.py`）

`FileSystem` 通过统一工具接口（`edit_file`）在可配置的根路径下读取、写入并管理文件与目录。

## 它处理什么

- 监听 context 中的 JSON 工具调用：
  - `{"type":"tool_call","name":"edit_file","arguments":{...}}`
- 支持的动作（完整参数见 `config/file_system.json` → `prompt`）：
  - `read` — 可选 `max_chars`、`start_line` / `end_line`
  - `write`、`append`
  - `replace` — 可选 `occurrence`（默认 1）、`replace_all`，以及成对使用的行范围 `start_line` / `end_line`
  - `list` — 列目录；可选 `recursive`、`include_hidden`、`files_only`、`dirs_only`
  - `glob` — 在基准路径下按模式匹配；可选 `recursive`（默认 true）
  - `search` — 文本搜索（可用时使用 `rg`）；可选 `max_results`
  - `rename` — 移动/重命名；可选 `overwrite`
  - `delete` — 非空目录可选 `recursive`
  - `mkdir` — 可选 `recursive`（默认 true）
  - `metadata` — 文件/目录元数据

## 安全行为（`config/file_system.json` 中的 `path_policy`）

- **`write_scope`**：`"workspace"`（默认）或 `"project"`。写入及多数破坏性操作必须落在对应根目录（代码中的 `_write_root`）之下：工作区根或项目根。
- **`workspace_root`** / **`project_root`**：可选覆盖；空字符串表示使用默认值（工作区为当前工作目录，项目根为 `modules/` 所在仓库根）。
- **相对路径**：读类操作相对于 `workspace_root` 解析；写操作相对于 `_write_root` 解析。
- **绝对路径**：若 **`allow_read_all_system`** 为 `true`，读操作可指向任意绝对路径；否则（且写操作始终）解析后的路径必须位于 `_write_root` 之下。
- **`read`** 要求目标为已存在文件（不能是目录）；**`replace`** 要求文件已存在。
- **`read`** 可通过 `max_chars` 截断输出。

## 会话集成

- 会话开始时，会从 `config/file_system.json` 注入模块提示词。
- 每次处理工具调用时，模块会：
  - 声明该 region（claim），
  - 执行动作，
  - 将工具调用替换为 `tool_result`，
  - 不设单独的 dispatch 标记；完成后将 `"Ready to send"` 置为 `"ready"`，以便下一 tick 在全员 ready 时由 `Sender` 发送，
  - 基于待处理工具调用更新 `"Ready to send"` 状态。

## 配置映射

- Runtime 模块名：`file_system`
- 模块文件：`modules/file_system.py`
- 配置文件：`config/file_system.json`
