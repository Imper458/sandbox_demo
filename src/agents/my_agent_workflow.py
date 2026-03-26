"""
LangGraph workflow orchestration for one user input.

NOTE: Code strings and behavior must stay identical to the original
`src/agents/my_agent.py` implementation; this module only re-homes code.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Checkpointer

from src.agents.my_agent_agents import (
    IntentRoute,
    build_local_shell_backend,
    build_router_agent,
    build_specialist_agents,
    get_repo_root,
)
from src.agents.my_agent_debug import _router_debug_enabled, dump_router_debug


class WorkflowState(TypedDict, total=False):
    """LangGraph 工作流状态：单轮用户输入从 START 经意图节点与条件边到 END。"""

    user_input: str
    base_thread_id: str
    intent: IntentRoute | None
    assistant_text: str


def build_workflow_graph(
    router: Any,
    specialists: dict[str, Any],
    checkpointer: Checkpointer,
):
    """
    START → intent（结构化 IntentRoute）→ 条件边 → direct 或各专家单次 invoke → END。
    专家节点内使用 invoke，非 token 流式（见 stream_agent_interaction_multi 说明）。
    """

    def intent_node(state: WorkflowState) -> dict[str, Any]:
        uid = state.get("base_thread_id") or "default_thread"
        uin = state.get("user_input") or ""
        router_cfg = _thread_config(uid, "_router")
        router_state = router.invoke(
            {"messages": [HumanMessage(content=uin)]},
            config=router_cfg,
        )
        dump_router_debug(router_state)
        route = _extract_intent_route(router_state)
        return {"intent": route}

    def route_after_intent(state: WorkflowState) -> str:
        intent = state.get("intent")
        if intent is None:
            return "error"
        return intent.target

    def direct_node(state: WorkflowState) -> dict[str, Any]:
        route = state.get("intent")
        if route is None:
            return {"assistant_text": "路由未能解析结构化意图，请重试。"}
        print("[router] direct 路径命中（主agent直接回答）", file=sys.stderr, flush=True)
        reply = (route.direct_reply or "").strip()
        if not reply:
            return {"assistant_text": "路由判定为 direct 但未提供 direct_reply。"}
        return {"assistant_text": reply}

    def error_node(state: WorkflowState) -> dict[str, Any]:
        return {"assistant_text": "路由未能解析结构化意图，请重试。"}

    def make_specialist_node(target_key: str):
        def specialist_node(state: WorkflowState) -> dict[str, Any]:
            route = state.get("intent")
            user_in = state.get("user_input") or ""
            uid = state.get("base_thread_id") or "default_thread"
            if route is None:
                return {"assistant_text": "路由未能解析结构化意图，请重试。"}
            agent = specialists.get(target_key)
            if agent is None:
                return {"assistant_text": f"未知 target: {target_key}"}
            print(f"{target_key}-agent成功接收信息", file=sys.stderr, flush=True)
            sub_cfg = _thread_config(uid, f"_{target_key}")
            task_text = (route.delegated_task or user_in).strip() or user_in
            try:
                sub_state = agent.invoke(
                    {"messages": [HumanMessage(content=task_text)]},
                    config=sub_cfg,
                )
            except Exception as e:
                return {"assistant_text": f"子 Agent 执行出错: {e}"}
            msgs = sub_state.get("messages", [])
            text = _last_ai_text(msgs)
            return {"assistant_text": text if text else "(子代理未返回文本内容)"}

        return specialist_node

    workflow = StateGraph(WorkflowState)
    workflow.add_node("intent", intent_node)
    workflow.add_node("direct", direct_node)
    workflow.add_node("error", error_node)
    for key in ("api-data-fetcher", "file-manager", "get-system-info", "general-purpose"):
        workflow.add_node(key, make_specialist_node(key))

    workflow.add_edge(START, "intent")
    workflow.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "direct": "direct",
            "api-data-fetcher": "api-data-fetcher",
            "file-manager": "file-manager",
            "get-system-info": "get-system-info",
            "general-purpose": "general-purpose",
            "error": "error",
        },
    )
    for name in ("direct", "error", "api-data-fetcher", "file-manager", "get-system-info", "general-purpose"):
        workflow.add_edge(name, END)

    return workflow.compile(checkpointer=checkpointer)


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
    workflow: Any,
    user_input: str,
    base_thread_id: str,
) -> tuple[str, IntentRoute | None]:
    """
    对工作流图执行一次 invoke（意图节点 → 条件边 → direct 或专家节点）。
    返回 (助手文本, 意图结果以便调试)。
    """
    result = workflow.invoke(
        {
            "user_input": user_input,
            "base_thread_id": base_thread_id,
            "intent": None,
            "assistant_text": "",
        },
        config={"configurable": {"thread_id": f"{base_thread_id}_workflow"}},
    )
    return (result.get("assistant_text") or "").strip(), result.get("intent")


async def stream_specialist(
    agent: Any,
    task_text: str,
    config: dict[str, Any],
) -> AsyncIterator[str]:
    """对选中的 deep agent 做 stream(messages)。工作流图内专家节点用 invoke 聚合，不走本函数；需按 token 流式时可单独调用。"""
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
    workflow: Any,
    base_thread_id: str,
) -> AsyncIterator[str]:
    """
    多轮对话：每轮对工作流图 invoke 一次（START→意图→条件边→END）。
    专家节点内为 invoke 聚合结果，非按 token 流式输出（与原先 stream_specialist 不同）。
    """
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

        try:
            result = workflow.invoke(
                {
                    "user_input": user_input,
                    "base_thread_id": base_thread_id,
                    "intent": None,
                    "assistant_text": "",
                },
                config={"configurable": {"thread_id": f"{base_thread_id}_workflow"}},
            )
        except Exception as e:
            yield f"\n❌ 工作流执行出错: {e}\n"
            continue

        text = (result.get("assistant_text") or "").strip()
        if text:
            yield text
        yield "\n"


async def main():
    workspace_dir = get_repo_root()
    checkpointer = InMemorySaver()
    backend = build_local_shell_backend(workspace_dir)

    router = build_router_agent(checkpointer)
    specialists = build_specialist_agents(backend, checkpointer)
    workflow = build_workflow_graph(router, specialists, checkpointer)

    print("Agent 创建成功（LangGraph 工作流 + 路由 create_agent + 5x create_deep_agent）")
    if _router_debug_enabled():
        print(
            "主路由调试已开启：每轮会在 stderr 打印完整路由 state（设 AGENT_DEBUG_ROUTER=0 可关闭）。",
            file=sys.stderr,
        )

    thread_id = "demo_thread_01"
    async for response in stream_agent_interaction_multi(workflow, thread_id):
        print(response, end="", flush=True)

