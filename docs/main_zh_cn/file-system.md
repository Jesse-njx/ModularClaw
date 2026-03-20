# File system 模块（`modules/file_system.py`）

`FileSystem` 提供了一个统一工具接口（`edit_file`），可在项目工作区内读取并修改文件。

## 它处理什么

- 监听 context 中的 JSON 工具调用：
  - `{"type":"tool_call","name":"edit_file","arguments":{...}}`
- 支持动作：
  - `read`（可选 `max_chars`）
  - `write`
  - `append`
  - `replace`（仅替换首个匹配）

## 安全行为

- 路径按项目根目录进行解析。
- 任何逃逸工作区的路径都会被拒绝。
- `read` 和 `replace` 要求目标文件已存在。
- `read` 会拒绝目录，并可通过 `max_chars` 截断输出。

## 会话集成

- 会话开始时，会从 `config/file_system.json` 注入模块提示词。
- 每次处理工具调用时，模块会：
  - 声明该 region（claim），
  - 执行动作，
  - 将工具调用替换为 `tool_result`，
  - 标记需要新循环（`set_need_loop(True)`），
  - 基于待处理工具调用更新 `"Ready to send"` 状态。

## 配置映射

- Runtime 模块名：`file_system`
- 模块文件：`modules/file_system.py`
- 配置文件：`config/file_system.json`
