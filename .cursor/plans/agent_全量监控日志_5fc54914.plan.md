---
name: Agent 全量监控日志
overview: 在保留现有流式对话体验的前提下，通过 LangGraph 多模式 stream 与/或 astream_events 捕获全量执行细节；落盘 JSON Lines，并汇总 token 消耗、工具调用次数等指标供监控分析。
todos:
  - id: research-stream-api
    content: 使用项目虚拟环境 D:\PythonProject\virtual_env\sandbox_demo_env 验证 agent.stream 多模式与 astream_events
    status: pending
  - id: implement-jsonl-logger
    content: 新增 JSON Lines 与截断/脱敏；记录原始流事件，并在每轮结束写入 usage 与工具调用汇总
    status: pending
  - id: aggregate-metrics
    content: 从 AIMessage.usage_metadata / response_metadata 与各轮 messages 或 astream_events 汇总 token 与工具次数
    status: pending
  - id: extend-stream-modes
    content: 将 stream_mode 改为含 updates/messages/values 的组合并解析 (mode, chunk)
    status: pending
  - id: revisit-ainvoke
    content: 评估流结束后 ainvoke 伪造消息对 checkpoint 与监控的影响，改为与 LangGraph 推荐用法一致
    status: pending
  - id: manual-verify
    content: 触发多工具与多轮 LLM 的对话，核对 summary 中 token 与工具次数与原始事件一致
    status: pending
isProject: false
---

# Agent 全量输出与工具调用监控方案

## 项目 Python 环境

本仓库依赖安装在虚拟环境：`**D:\PythonProject\virtual_env\sandbox_demo_env**`。

执行验证脚本或运行 `my_agent.py` 时请先激活该环境（Windows 示例：`D:\PythonProject\virtual_env\sandbox_demo_env\Scripts\activate`），或使用该环境下的解释器绝对路径调用 Python，以确保 `deepagents`、`langgraph` 等版本与 `requirements.txt` 一致。

## 现状与问题

`[my_agent.py](d:\PythonProject\sandbox_demo\my_agent.py)` 当前仅用 `stream_mode="messages"`，且只处理了 `AIMessageChunk` 的 `content` 与部分 `tool_call_chunks`（仅在有 `name` 时打印一行）。**工具入参、工具返回内容、节点级更新** 在 `messages` 单模式下往往不完整或需自行拼接。

另外，流结束后对 `[agent.ainvoke](d:\PythonProject\sandbox_demo\my_agent.py)` 传入「用户原文 + 拼接后的 assistant 纯文本」**并非**图执行产生的真实消息序列（缺少 `tool_calls` / `ToolMessage`），既不利于监控对齐，也可能干扰 checkpoint 语义。监控方案落地时建议**一并理清**：要么只依赖单次 `stream`/`astream` 完成一轮对话并写 checkpoint，要么改为不显式 `ainvoke` 伪造历史（需用官方推荐方式续写 thread）。

## 推荐架构（两条数据路径）

```mermaid
flowchart LR
  user_input[UserInput]
  graph[CompiledGraph]
  stream_multi[stream_multi_mode]
  events[astream_events_v2]
  console[Console_UI]
  jsonl[JSONL_Log]
  user_input --> graph
  graph --> stream_multi
  graph --> events
  stream_multi --> console
  stream_multi --> jsonl
  events --> jsonl
```



1. `**agent.stream(..., stream_mode=[...])**`：捕获图状态与消息流。
  - `**"updates"**`：每步之后各节点的 patch，便于看「哪一步写了什么」。  
  - `**"messages"**`：保留现有 token 级助手输出（控制台体验）。  
  - 可选 `**"values"**`：每步后的完整 state（通常含 `messages` 列表），**最利于拿到完整的 `AIMessage.tool_calls` 与 `ToolMessage`**，用于与 `updates` 互补。  
  - LangGraph 在 `stream_mode` 为列表时，流式项一般为 `(mode, payload)` 形式，需在循环里按 `mode` 分支解析并写入日志。
2. `**agent.astream_events(version="v2", ...)**`（若运行时代码中 `agent` 支持，LangGraph 编译图通常支持）：按事件类型记录 **工具开始/结束、模型流式、链边界**，适合「监控/审计」粒度。
  - 过滤 `event["event"]`（如 `on_tool_start` / `on_tool_end` / `on_chat_model_stream` 等，以实际 v2 文档为准），将 `event["data"]` 中可序列化字段写入日志。  
  - 注意：社区有工具异常时 `ToolMessage` 在事件里不可见等边界情况，日志中应记录 `on_tool_error` 或异常字符串（若存在）。

**并行策略**：同一轮用户输入可 **先/后** 各跑一次会破坏状态；正确做法是 **单次执行** 里要么只用多模式 `stream`（推荐），要么只用 `astream_events` 驱动控制台（需自己拼助手正文），或查阅 LangGraph 是否支持在单次执行中订阅 events（部分版本需 `stream_mode="debug"` 或 callbacks）。若 Deep Agents 封装限制较多，**优先实现多模式 `stream` + `"values"` 快照**，一般已覆盖「工具调用 + 返回」分析需求。

## 日志形态（便于监控分析）

- **格式**：**JSON Lines**（一行一个 JSON 对象），字段建议包括：`ts`（ISO8601）、`thread_id`、`turn_id`（每轮用户输入递增）、`mode`（`updates` / `messages` / `values` / `event`）、`payload`（原始或裁剪后的可 JSON 序列化结构）。  
- **敏感信息**：对 `api_key`、`.env` 路径等做脱敏或省略。  
- **大字段**：`ToolMessage.content` 过长时可截断并增加 `truncated: true`。  
- **输出位置**：例如 `logs/agent_YYYYMMDD.jsonl`（路径可配置），控制台保留人类可读摘要 + 可选 `DEBUG` 开关打印完整 payload。

实现上可用标准库 `[logging](https://docs.python.org/3/library/logging.html)` 配 `FileHandler` + 自定义 Formatter，或直接用 `open(..., "a")` 写 JSONL，避免引入过重依赖。

## 代码改动范围（预估）


| 区域                                                         | 改动要点                                                                              |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------- |
| `[my_agent.py](d:\PythonProject\sandbox_demo\my_agent.py)` | 将 `stream_mode` 扩展为列表；解析 `(mode, chunk)`；将各 mode 写入日志；控制台仍可从 `messages` 流式打印助手文本。 |
| 新建 `logging` 辅助模块（可选）                                      | `log_event(record: dict)`，统一序列化与截断。                                               |
| 依赖                                                         | 通常无需新增依赖；若需更严的 JSON schema 可后续加 `pydantic`（项目已有）。                                 |


## 验证方式

- 发起一轮会触发 **读文件 / 执行 shell** 的对话，检查 JSONL 中是否出现：用户消息、带 `tool_calls` 的 AI 消息、`ToolMessage`、最终回复。  
- 对比仅 `messages` 与增加 `values`/`updates` 后日志字段是否完整。

## 可选增强（非必须）

- **LangSmith**：若可接受外发追踪，用环境变量开启 tracing，与本地 JSONL 互补。  
- **结构化分析**：用 `jq` 或小型 Python 脚本按 `thread_id` / `tool` 名称聚合 JSONL。

## 风险说明

- `stream_mode=["messages", ...]` 在部分 LangGraph 版本中存在 **tool_calls 元数据在 chunk 中不完整** 的 [已知问题](https://github.com/langchain-ai/langgraph/issues)；若分析强依赖 chunk 级 `tool_calls`，以 `**values` 每步后的完整 `messages`** 为准更稳。  
- `deepagents` 若对 `stream` 做了包装，需在本地安装版本下做一次小实验，确认多模式与 `astream_events` 是否可用；不可用则退回 `**stream_mode="values"` 单模式** + 从 state 打印助手回复。

