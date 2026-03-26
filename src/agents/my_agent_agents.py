"""
Router / specialist agent building primitives.

NOTE: Code strings and behavior must stay identical to the original
`src/agents/my_agent.py` implementation; this module only re-homes code.
"""

import os
import sys
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# 先于 LangChain / deepagents 导入，确保 .env（含 LANGSMITH_*）已加载到 os.environ
from env_utils import OPENAI_API_KEY, OPENAI_BASE_URL

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain.agents import create_agent
from langchain.agents.middleware import ToolCallLimitMiddleware
from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI
from langgraph.types import Checkpointer

from src.agents.my_agent_prompts import (
    API_DATA_FETCHER_SYSTEM,
    FILE_MANAGER_SYSTEM,
    GENERAL_PURPOSE_SYSTEM,
    GET_SYSTEM_INFO_SYSTEM,
    ROUTER_INTENT_SYSTEM_PROMPT,
)


def get_repo_root() -> Path:
    """
    Project root directory.
    When running `python src/agents/my_agent.py`, CWD may become `src/agents/`,
    so any skills / teaching_skills relative paths would break unless we anchor
    execution to the repo root.
    """
    return Path(__file__).resolve().parents[2]


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


def build_local_shell_backend(workspace_dir: Path) -> LocalShellBackend:
    repo_root = get_repo_root()
    return LocalShellBackend(
        root_dir=str(repo_root),
        virtual_mode=True,
        env={
            "PATH": f"{os.path.dirname(get_python_executable())};{os.environ.get('PATH', '')}",
            "PYTHONPATH": str(repo_root),
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


# def _specialist_tool_run_limit() -> int:
#     """
#     单轮 deep agent 的工具调用上限（含 execute 与 task）。
#     文档类任务通常需要多步调用，默认值不能过低。
#     """
#     raw = os.environ.get("AGENT_TOOL_RUN_LIMIT", "25").strip()
#     try:
#         n = int(raw)
#     except ValueError:
#         return 25
#     return max(1, min(n, 500))


def build_specialist_agents(backend: LocalShellBackend, checkpointer: Checkpointer) -> dict[str, Any]:
    """五个独立的 create_deep_agent，各自 skills（general-purpose 无 skills）。"""
    guarded_middlewares = [
        DisableWriteFileMiddleware(),
        ToolCallLimitMiddleware(run_limit=4, exit_behavior="continue"),
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

