"""
Router debug helpers.

NOTE: Code strings must stay byte-for-byte identical to the original file.
This module only moves functions; it must not change behavior.
"""

import os
import sys
from typing import Any

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from src.agents.my_agent_agents import IntentRoute


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

