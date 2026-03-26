---
name: my_agent模块拆分
overview: 将 [`src/agents/my_agent.py`](e:/pycharm/pythonProject/sandbox_demo/src/agents/my_agent.py) 内部代码按模块分类拆分到若干新文件（例如提示词、工作流分别放独立 py 文件），并在主文件中只通过导入/重导出保持对外接口不变；移动后的代码内容保持原样（仅调整存放位置与导入）。
todos:
  - id: split-prompts
    content: 从 [`src/agents/my_agent.py`](src/agents/my_agent.py) 抽取提示词常量到 [`src/agents/my_agent_prompts.py`](src/agents/my_agent_prompts.py)（只移动不改字符串内容）
    status: completed
  - id: split-debug
    content: 把路由调试函数抽取到 [`src/agents/my_agent_debug.py`](src/agents/my_agent_debug.py)（只移动不改函数体）
    status: completed
  - id: split-agents
    content: 把 llm/router_llm、IntentRoute、DisableWriteFileMiddleware、build_*agent 等抽取到 [`src/agents/my_agent_agents.py`](src/agents/my_agent_agents.py)，并从 prompts/debug 做相对导入
    status: completed
  - id: split-workflow
    content: 把 WorkflowState、build_workflow_graph、route_user_input、stream_* 等抽取到 [`src/agents/my_agent_workflow.py`](src/agents/my_agent_workflow.py)，并从 agents/debug 做相对导入
    status: completed
  - id: rewrite-entry
    content: 将 [`src/agents/my_agent.py`](src/agents/my_agent.py) 改为聚合入口：只做从各模块的导入/重导出（确保外部导入符号不变）
    status: completed
isProject: false
---

# my_agent.py 按模块分类拆分（仅移动/导入重定向，不改代码逻辑）

## 目标

- 将 `[src/agents/my_agent.py](e:/pycharm/pythonProject/sandbox_demo/src/agents/my_agent.py)` 中的不同功能块拆分到独立模块文件：
  - 提示词（`ROUTER_INTENT_SYSTEM_PROMPT`、`API_DATA_FETCHER_SYSTEM` 等）放单独文件
  - 工作流（`WorkflowState`、`build_workflow_graph` 等）放单独文件
  - 其余：路由/专家 agent 构建、调试工具、交互 main 等可继续按同样方式拆分
- `src/agents/my_agent.py` 作为“聚合入口”文件：
  - 通过 `from .xxx import ...` 引入并 `__all`__/重导出，确保外部导入（例如 `[my_agent2.py](e:/pycharm/pythonProject/sandbox_demo/my_agent2.py)` 的 `from src.agents.my_agent import build_local_shell_backend, llm`）不受影响
- 代码内容要求：
  - 新模块文件中的代码段保持与原文件字面一致（仅移动位置、必要的顶层 import 调整为相对模块导入；不改函数/类体逻辑）

## 建议的模块划分（同目录下新增文件）

1. `[src/agents/my_agent_prompts.py](src/agents/my_agent_prompts.py)`
  - 放：`ROUTER_INTENT_SYSTEM_PROMPT`、`API_DATA_FETCHER_SYSTEM`、`FILE_MANAGER_SYSTEM`、`GET_SYSTEM_INFO_SYSTEM`、`GENERAL_PURPOSE_SYSTEM`
2. `[src/agents/my_agent_debug.py](src/agents/my_agent_debug.py)`
  - 放：`_router_debug_enabled`、`format_router_state_debug`、`dump_router_debug`
3. `[src/agents/my_agent_agents.py](src/agents/my_agent_agents.py)`
  - 放：`get_python_executable`、`llm`、`router_llm`、`IntentRoute`、`DisableWriteFileMiddleware`、`build_local_shell_backend`、`build_router_agent`、`_specialist_tool_run_limit`、`build_specialist_agents`
  - 其中 `build_specialist_agents` 需要从 `my_agent_prompts.py` 导入 system_prompt 常量
  - `GET_SYSTEM_INFO_SYSTEM` 等仍保留原样字符串内容
4. `[src/agents/my_agent_workflow.py](src/agents/my_agent_workflow.py)`
  - 放：`WorkflowState`、`build_workflow_graph`、`_thread_config`、`_extract_intent_route`、`_last_ai_text`、`route_user_input`、`stream_specialist`、`stream_agent_interaction_multi`
  - 需要从：
    - `my_agent_agents.py` 导入 `_thread_config` 所依赖项/`IntentRoute`/`dump_router_debug` 等
    - `my_agent_debug.py` 导入 `dump_router_debug`（若选择将 debug 也拆出）
5. `[src/agents/my_agent.py](src/agents/my_agent.py)`
  - 只保留聚合与重导出：
    - `from .my_agent_agents import llm, build_local_shell_backend, IntentRoute, build_router_agent, build_specialist_agents, ...`
    - `from .my_agent_workflow import build_workflow_graph, stream_agent_interaction_multi, route_user_input, ...`
    - `main` / `__main`__ 逻辑可以保留在入口文件或单独放到 `my_agent_cli.py`（两者均可），但建议先仅在入口文件里重排以最小化变动

## 执行流（实现步骤）

1. 复制粘贴：把原 `[src/agents/my_agent.py](src/agents/my_agent.py)` 中“提示词块/工作流块/专家构建块/调试块”的代码段原样复制到对应新文件。
2. 调整相对导入：在每个新模块文件的顶部添加最少量 import，把从其他模块来的符号通过相对路径引入（例如 `from .my_agent_prompts import ROUTER_INTENT_SYSTEM_PROMPT`）。
3. 聚合入口：把 `[src/agents/my_agent.py](src/agents/my_agent.py)` 替换为 import 重导出，确保原先被外部使用的符号仍存在且可导入。
4. 校验导入点：
  - 检查 `[my_agent2.py](e:/pycharm/pythonProject/sandbox_demo/my_agent2.py)` 的导入是否仍能解析
  - 其它脚本若引用 `src.agents.my_agent`，同样保持不变
5. （可选）在新模块之间设置 `__all_`_ 以减少导出混乱，但不影响运行

## 验收标准

- `python -m src.agents.my_agent` 依旧能启动（若你用这一方式）
- `my_agent2.py` 仍能从 `src.agents.my_agent` 导入 `build_local_shell_backend` 和 `llm`
- 任何函数/类的行为保持不变（因为代码体逻辑未修改，只移动与导入重定向）

