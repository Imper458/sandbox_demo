import asyncio
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel, Field

# 先于 LangChain / deepagents 导入，确保 .env（含 LANGSMITH_*）已加载到 os.environ
from env_utils import OPENAI_API_KEY, OPENAI_BASE_URL

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Checkpointer


def get_python_executable() -> str:
    """获取当前Python解释器的完整路径"""
    python_exe = sys.executable
    print(f"当前Python解释器: {python_exe}")
    return python_exe


# 执行类任务用略高温度；路由用低温保证分类稳定
llm = ChatOpenAI(
    model_name="gpt-4o-mini",
    temperature=1.1,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)

router_llm = ChatOpenAI(
    model_name="gpt-4o-mini",
    temperature=0,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
)


class IntentRoute(BaseModel):
    """主路由结构化输出：由 create_agent(..., response_format=IntentRoute) 解析。"""

    target: Literal[
        "direct",
        "api-data-fetcher",
        "file-manager",
        "get-system-info",
        "general-purpose",
    ] = Field(
        description=(
            "direct=由本路由直接回答；其余为对应子 deep agent。"
            "general-purpose 仅作兜底（三位专家都不适用时的复杂多步任务）。"
        )
    )
    delegated_task: str = Field(
        default="",
        description="委派给子代理时的完整任务说明（含路径、约束）；target=direct 时可为空。",
    )
    direct_reply: str | None = Field(
        default=None,
        description="仅当 target=direct 时必填：给用户的完整中文回答。",
    )


class DisableWriteFileMiddleware(AgentMiddleware):
    """全局禁用 write_file 工具，避免误写伪二进制文件（如伪 PDF）。"""

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | AIMessage:
        if request.tools:
            filtered_tools = []
            for t in request.tools:
                if isinstance(t, dict):
                    tool_name = t.get("name")
                else:
                    tool_name = getattr(t, "name", None)
                if tool_name == "write_file":
                    continue
                filtered_tools.append(t)
            request = request.override(tools=filtered_tools)
        return handler(request)


def _router_debug_enabled() -> bool:
    """设为 0/false/off 可关闭主路由调试块（环境变量 AGENT_DEBUG_ROUTER）。"""
    v = os.environ.get("AGENT_DEBUG_ROUTER", "1").strip().lower()
    return v in ("1", "true", "yes", "on")


def format_router_state_debug(router_state: dict[str, Any]) -> str:
    """主 agent（create_agent 路由图）一次 invoke 后的完整可观测状态，便于排查路由与结构化输出。"""
    lines: list[str] = [
        "",
        "========== [主agent / 路由 调试] ==========",
        f"state keys: {sorted(router_state.keys())}",
    ]
    msgs = router_state.get("messages") or []
    lines.append(f"messages 条数: {len(msgs)}")
    for i, m in enumerate(msgs):
        tname = type(m).__name__
        content = getattr(m, "content", None)
        lines.append(f"  [{i}] {tname}.content = {content!r}")
        if tname == "AIMessage":
            tc = getattr(m, "tool_calls", None)
            if tc:
                lines.append(f"       tool_calls = {tc!r}")
            refus = getattr(m, "invalid_tool_calls", None)
            if refus:
                lines.append(f"       invalid_tool_calls = {refus!r}")
        extra = getattr(m, "additional_kwargs", None)
        if extra:
            lines.append(f"       additional_kwargs = {extra!r}")

    sr = router_state.get("structured_response")
    lines.append(f"structured_response (raw): {sr!r}")
    if sr is not None:
        if isinstance(sr, IntentRoute):
            lines.append(f"structured_response (IntentRoute.model_dump): {sr.model_dump()}")
        elif isinstance(sr, BaseModel):
            lines.append(f"structured_response (BaseModel.model_dump): {sr.model_dump()}")
        elif isinstance(sr, dict):
            lines.append(f"structured_response (已是 dict): {sr}")

    for k in sorted(router_state.keys()):
        if k in ("messages", "structured_response"):
            continue
        lines.append(f"其它字段 {k!r}: {router_state[k]!r}")

    lines.append("========== [/主agent / 路由 调试] ==========")
    lines.append("")
    return "\n".join(lines)


def dump_router_debug(router_state: dict[str, Any]) -> None:
    """将主路由完整状态打印到 stderr，避免与助手正文 stdout 混在一起。"""
    if _router_debug_enabled():
        print(format_router_state_debug(router_state), file=sys.stderr, flush=True)


ROUTER_INTENT_SYSTEM_PROMPT = """你是意图路由模块。你不加载任何技能（SKILL），只根据用户输入判断交给哪个子代理，或由你直接回答。

## 可选 target
- **direct**：闲聊、常识、与下列专家无关的一般问题；或无法明确归类时由你直接回答。
- **api-data-fetcher**：网络/API 公开数据、天气、股票、新闻、汇率等（需运行 teaching_skills/api-data-fetcher 脚本）。
- **file-manager**：Windows 下列目录、列文件、文件内搜索（dir / findstr），不用 ls/grep。
- **get-system-info**：本机操作系统、Python 环境、磁盘、网络；以及 docx/pdf/pptx/xlsx 文档处理（按对应技能）。
- **general-purpose**：仅当需要长链路隔离且上述三位专家都不适用时的兜底任务。

## 结构化输出规则（必须遵守）
- 只通过结构化字段输出，不要假设有 task 工具。
- **target=direct**：必须在 direct_reply 中给出完整、简洁的中文回答；delegated_task 可为空；direct_reply 不可为空。
- **target 为专家**：delegated_task 必须写清用户原意与必要上下文（路径、文件名等）；direct_reply 必须为 null。
- 不要强行委派；不确定时优先 direct。"""


# --- 各子 deep agent 的 system_prompt（与原 subagents 一致，去掉「task 委派」表述）---

API_DATA_FETCHER_SYSTEM = (
    "你是 API 数据查询技能代理。必须严格遵循当前已加载技能（SKILL.md）中的步骤与命令示例。\n"
    "在项目根目录下使用 execute 运行脚本；使用 teaching_skills/api-data-fetcher/ 下的相对路径。\n"
    "完成后用简洁中文向用户汇总结果。"
)

FILE_MANAGER_SYSTEM = (
    "你是 Windows 文件管理技能代理。必须严格遵循 SKILL.md：仅用 dir、findstr、cd 等 Windows 命令，"
    "通过 execute 执行。完成后用简洁中文汇报命令输出要点。"
)

GET_SYSTEM_INFO_SYSTEM = (
    "你是系统信息与文档处理技能代理。必须严格遵循已加载技能目录中的 SKILL.md。\n"
    "当任务是系统信息时：在项目根目录执行 "
    "python teaching_skills/get-system-info/get_system_info.py（或 SKILL 中给出的路径）。\n"
    "当任务涉及 docx/pdf/pptx/xlsx 时：优先使用对应技能目录中的标准流程与脚本。\n"
    "你有一个监管子代理 qa-regulator。每次你认为任务完成后，必须调用 task(name='qa-regulator', ...)\n"
    "让其核验“是否真的满足用户目标、关键命令是否失败、产物是否可打开/可用”。仅当监管通过，才能对用户宣告成功。\n"
    "如果监管判定失败，继续修复并再次调用监管复核。\n"
    "统一使用 execute 运行；完成后用简洁中文总结输出。"
)

GENERAL_PURPOSE_SYSTEM = (
    "你是兜底子代理。仅当路由明确判定你为合适目标时使用；使用可用工具（含 execute）完成任务，"
    "最后返回简洁摘要。若任务明显属于 api-data-fetcher、file-manager、get-system-info 之一，"
    "在回复中说明更合适的目标名称。"
)


def build_local_shell_backend(workspace_dir: Path) -> LocalShellBackend:
    return LocalShellBackend(
        root_dir=".",
        virtual_mode=True,
        env={
            "PATH": f"{os.path.dirname(get_python_executable())};{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(workspace_dir),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
        },
    )


def build_router_agent(checkpointer: Checkpointer):
    """主意图路由：无 skills、无 subagents，仅结构化输出 IntentRoute。"""
    return create_agent(
        router_llm,
        tools=[],
        system_prompt=ROUTER_INTENT_SYSTEM_PROMPT,
        response_format=IntentRoute,
        checkpointer=checkpointer,
    )


def build_specialist_agents(backend: LocalShellBackend, checkpointer: Checkpointer) -> dict[str, Any]:
    """五个独立的 create_deep_agent，各自 skills（general-purpose 无 skills）。"""
    guarded_middlewares = [
        DisableWriteFileMiddleware(),
        ToolCallLimitMiddleware(run_limit=3, exit_behavior="error"),
    ]
    api_agent = create_deep_agent(
        backend=backend,
        model=llm,
        checkpointer=checkpointer,
        system_prompt=API_DATA_FETCHER_SYSTEM,
        middleware=guarded_middlewares,
        skills=["/teaching_skills/api-data-fetcher/"],
    )
    file_agent = create_deep_agent(
        backend=backend,
        model=llm,
        checkpointer=checkpointer,
        system_prompt=FILE_MANAGER_SYSTEM,
        middleware=guarded_middlewares,
        skills=["/teaching_skills/file-manager/"],
    )
    qa_regulator_subagent = {
        "name": "qa-regulator",
        "description": (
            "用于验收 get-system-info 代理的执行结果。检查是否真正解决用户问题，"
            "是否存在关键命令失败、虚假成功、文件不可打开或格式错误。"
        ),
        "system_prompt": (
            "你是结果监管代理。你的职责是严格验收，不要替主代理粉饰结果。\n"
            "请根据主代理提供的任务目标、执行过程与产物进行核验，重点检查：\n"
            "1) 是否满足用户原始需求；\n"
            "2) 执行日志里是否有失败命令（非0退出码）；\n"
            "3) 若产物是文件，是否存在且基本有效（例如 PDF 需是有效 PDF 而非文本冒充）；\n"
            "4) 是否出现“部分失败却宣告成功”。\n"
            "输出必须简洁且结论明确：PASS 或 FAIL，并给出可执行修复建议。"
        ),
    }

    sysdoc_agent = create_deep_agent(
        backend=backend,
        model=llm,
        checkpointer=checkpointer,
        system_prompt=GET_SYSTEM_INFO_SYSTEM,
        middleware=guarded_middlewares,
        subagents=[qa_regulator_subagent],
        skills=[
            "/teaching_skills/get-system-info/",
            "/teaching_skills/docx/",
            "/teaching_skills/pdf/",
            "/teaching_skills/pptx/",
            "/teaching_skills/xlsx/",
        ],
    )
    general_agent = create_deep_agent(
        backend=backend,
        model=llm,
        checkpointer=checkpointer,
        system_prompt=GENERAL_PURPOSE_SYSTEM,
        middleware=guarded_middlewares,
    )
    return {
        "api-data-fetcher": api_agent,
        "file-manager": file_agent,
        "get-system-info": sysdoc_agent,
        "general-purpose": general_agent,
    }


def _thread_config(base_thread_id: str, suffix: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"{base_thread_id}{suffix}"}}


def _extract_intent_route(router_state: dict[str, Any]) -> IntentRoute | None:
    sr = router_state.get("structured_response")
    if sr is None:
        return None
    if isinstance(sr, IntentRoute):
        return sr
    if isinstance(sr, dict):
        return IntentRoute.model_validate(sr)
    return None


def _last_ai_text(messages: list[Any]) -> str:
    for m in reversed(messages or []):
        if isinstance(m, AIMessage):
            c = m.content
            if c is not None and str(c).strip():
                return str(c)
    return ""


def route_user_input(
    router: Any,
    specialists: dict[str, Any],
    user_input: str,
    base_thread_id: str,
) -> tuple[str, IntentRoute | None]:
    """
    路由一次用户输入：先 router.invoke，再按 target 委派或返回 direct_reply。
    返回 (助手文本, 路由结果以便调试)。
    """
    router_cfg = _thread_config(base_thread_id, "_router")
    router_state = router.invoke(
        {"messages": [HumanMessage(content=user_input)]},
        config=router_cfg,
    )
    dump_router_debug(router_state)
    route = _extract_intent_route(router_state)
    if route is None:
        return "路由未能解析结构化意图，请重试。", None

    if route.target == "direct":
        print("[router] direct 路径命中（主agent直接回答）", file=sys.stderr, flush=True)
        reply = (route.direct_reply or "").strip()
        if not reply:
            return "路由判定为 direct 但未提供 direct_reply。", route
        return reply, route

    agent = specialists.get(route.target)
    if agent is None:
        return f"未知 target: {route.target}", route
    print(f"{route.target}-agent成功接收信息", file=sys.stderr, flush=True)

    sub_cfg = _thread_config(base_thread_id, f"_{route.target}")
    task_text = (route.delegated_task or user_input).strip() or user_input
    sub_state = agent.invoke(
        {"messages": [HumanMessage(content=task_text)]},
        config=sub_cfg,
    )
    msgs = sub_state.get("messages", [])
    text = _last_ai_text(msgs)
    return (text if text else "(子代理未返回文本内容)"), route


async def stream_specialist(
    agent: Any,
    task_text: str,
    config: dict[str, Any],
) -> AsyncIterator[str]:
    """对选中的 deep agent 做 stream(messages)，与原先流式体验一致。"""
    inputs = {"messages": [HumanMessage(content=task_text)]}
    stream = agent.stream(inputs, config=config, stream_mode="messages", subgraphs=False)
    try:
        for chunk in stream:
            if isinstance(chunk, tuple) and len(chunk) == 2:
                token, _metadata = chunk
                if hasattr(token, "content") and token.content is not None:
                    content_str = str(token.content)
                    if content_str:
                        yield content_str
                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tool_chunk in token.tool_call_chunks:
                        if tool_chunk and hasattr(tool_chunk, "get") and tool_chunk.get("name"):
                            yield f"\n[调用工具: {tool_chunk['name']}]\n"
            else:
                print(f"\n[调试] 意外的 chunk 结构: {type(chunk)}", file=sys.stderr)
    except Exception as e:
        yield f"\n❌ 子 Agent 执行出错: {e}\n"
        import traceback

        traceback.print_exc()


async def stream_agent_interaction_multi(
    router: Any,
    specialists: dict[str, Any],
    base_thread_id: str,
) -> AsyncIterator[str]:
    """多轮对话：每轮先路由 invoke，再 direct 打印或子 agent 流式输出。"""
    while True:
        try:
            user_input = input("\n\n[用户] >>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n\n对话结束。")
            break

        if user_input.lower() in ("quit", "exit", "退出", "q"):
            print("再见！")
            break
        if not user_input:
            continue

        print("\n[助手] ", end="", flush=True)

        router_cfg = _thread_config(base_thread_id, "_router")
        try:
            router_state = router.invoke(
                {"messages": [HumanMessage(content=user_input)]},
                config=router_cfg,
            )
            dump_router_debug(router_state)
        except Exception as e:
            yield f"\n❌ 路由执行出错: {e}\n"
            continue

        route = _extract_intent_route(router_state)
        if route is None:
            yield "路由未能解析结构化意图，请重试。\n"
            continue

        if route.target == "direct":
            print("[router] direct 路径命中（主agent直接回答）", file=sys.stderr, flush=True)
            reply = (route.direct_reply or "").strip()
            if not reply:
                yield "路由判定为 direct 但未提供 direct_reply。\n"
            else:
                yield reply
            continue

        agent = specialists.get(route.target)
        if agent is None:
            yield f"未知 target: {route.target}\n"
            continue
        print(f"{route.target}-agent成功接收信息", file=sys.stderr, flush=True)

        sub_cfg = _thread_config(base_thread_id, f"_{route.target}")
        task_text = (route.delegated_task or user_input).strip() or user_input

        async for piece in stream_specialist(agent, task_text, sub_cfg):
            yield piece


async def main():
    workspace_dir = Path("./").absolute()
    checkpointer = InMemorySaver()
    backend = build_local_shell_backend(workspace_dir)

    router = build_router_agent(checkpointer)
    specialists = build_specialist_agents(backend, checkpointer)

    print("Agent 创建成功（路由 create_agent + 5x create_deep_agent）")
    if _router_debug_enabled():
        print(
            "主路由调试已开启：每轮会在 stderr 打印完整路由 state（设 AGENT_DEBUG_ROUTER=0 可关闭）。",
            file=sys.stderr,
        )

    thread_id = "demo_thread_01"
    async for response in stream_agent_interaction_multi(router, specialists, thread_id):
        print(response, end="", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
