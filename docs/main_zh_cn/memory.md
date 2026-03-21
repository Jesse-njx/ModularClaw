# Memory 模块（`modules/memory.py`）

`Memory` 通过工具调用让模型把短文本片段持久化保存。数据以 JSON 形式存放在工作区下（默认：`workspace/Memory/memories.json`，可配置）。

## 它处理什么

- 监听 context 中 **`type` 为 `Text`** 且 **`label` 为 `"json"`** 的 JSON 工具调用（与 `Executor` / `FileSystem` 对模型输出工具的约定一致）：

```json
{"type": "tool_call", "name": "save_memory", "arguments": {"content": "...", "tags": [], "importance": 3, "metadata": {}}}
```

```json
{"type": "tool_call", "name": "search_memory", "arguments": {"query": "子串", "tags": [], "max_results": 10}}
```

- **`save_memory`**：必填 **`content`**（字符串）。可选 **`tags`**（列表）、**`importance`**（默认 `3`）、**`metadata`**（对象）。写入一条带自增 **`id`** 与 ISO **`timestamp`** 的记录并保存文件。
- **`search_memory`**：若 **`query`** 与 **`tags`** 都为空或省略，则返回最近若干条（至多 **`max_results`**，夹在 `1`–`500`）。否则按标签子集与/或在 **`content`** 中的不区分大小写子串过滤，按 **`importance`**（降序）再按 **`timestamp`** 排序。

## 会话集成

- 在 **`on_session_start`** 时，若 **`config/memory.json`** 里 **`prompt`** 非空，会作为 **`SystemText`** 追加进 context，供模型看到工具说明。
- 每个 tick：定位对应工具调用 → **`claim_region`** → 将 JSON 替换为 **`ToolResult`**（含 `ok`、`message` 及结果字段）→ **`mark_claimed_region_finished`**。
- **`Ready to send`**：context 中仍有未处理的 `save_memory` / `search_memory` 时为 **`pending`**；全部处理完为 **`ready`**——因此 **`Sender`** 会等同其他工具模块一样等待记忆读写结束。

## 配置映射

- Runtime 模块名：**`memory`**
- 模块文件：**`modules/memory.py`**
- 配置文件：**`config/memory.json`**

### 常用配置项

- **`prompt`**：会话开始时注入的系统说明（工具格式与规则）。
- **`path_policy.workspace_root`**：存储根目录；空字符串表示使用进程当前工作目录。
- **`storage.relative_dir`** / **`storage.memories_file`**：相对根目录的子目录与 JSON 文件名（默认：`Memory`、`memories.json`）。
- **`supported_tools`**：给人或工具清单用的说明；实际执行的工具名固定为 `save_memory` 与 `search_memory`。
