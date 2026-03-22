---
name: Agent 全量次数统计
overview: 在 [`my_agent.py`](d:\PythonProject\sandbox_demo\my_agent.py) 的流式循环中按回合累加各类计数（工具、代理、图步骤、异常等），**并汇总每回合与会话的 token 消耗**（prompt/completion/total）；回合结束与程序退出时输出可读摘要；可选恢复 JSONL 与离线聚合脚本，与现有 [`logs/agent_events_*.jsonl`](d:\PythonProject\sandbox_demo\logs\agent_events_20260321.jsonl) 结构对齐。
todos:
  - id: add-agent-stats-module
    content: 新增 agent_stats.py：TurnAccumulator / SessionAccumulator、feed 逻辑（ToolMessage 为主计数）与 usage_metadata 归并（多模型步求和）
    status: completed
  - id: wire-my-agent-stream
    content: 在 my_agent.py 的 stream_agent_interaction_corrected 中接入累加、回合结束与会话结束打印
    status: completed
  - id: optional-jsonl-turn-summary
    content: 可选：回合结束追加 agent_turn_summary 行（与现有 JSONL 字段对齐）
    status: completed
  - id: optional-offline-script
    content: 可选：scripts/summarize_agent_logs.py 离线聚合 agent_events jsonl
    status: completed
isProject: false
---

