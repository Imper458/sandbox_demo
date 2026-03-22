import asyncio
import os
import sys
from pathlib import Path
from typing import Any, AsyncIterator

# 先于 LangChain / deepagents 导入，确保 .env（含 LANGSMITH_*）已加载到 os.environ
from env_utils import ALIBABA_API_KEY, ALIBABA_BASE_URL, OPENAI_API_KEY, OPENAI_BASE_URL

from deepagents import create_deep_agent
from deepagents.backends import LocalShellBackend
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Checkpointer


def get_python_executable():
    """获取当前Python解释器的完整路径"""
    python_exe = sys.executable
    print(f"当前Python解释器: {python_exe}")
    return python_exe


# 大模型
llm = ChatOpenAI(
    model_name="gpt-4o-mini",
    temperature=1.1,
    api_key=OPENAI_API_KEY,
    base_url=OPENAI_BASE_URL,
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


def build_teaching_subagents() -> list[dict[str, Any]]:
    """三个子代理：各只挂载一个技能目录（虚拟路径，相对项目根）。"""
    return [
        {
            "name": "api-data-fetcher",
            "description": (
                "当用户需要获取网络数据、查询API信息、获取公开数据（如天气、股票、新闻、汇率等）时使用。"
                "通过公开API获取数据，可接收多种参数进行灵活查询。"
            ),
            "system_prompt": (
                "你是 API 数据查询技能代理。必须严格遵循当前已加载技能（SKILL.md）中的步骤与命令示例。\n"
                "在项目根目录下使用 execute 运行脚本；使用 teaching_skills/api-data-fetcher/ 下的相对路径。\n"
                "完成后用简洁中文向用户汇总结果。"
            ),
            "skills": ["/teaching_skills/api-data-fetcher/"],
        },
        {
            "name": "file-manager",
            "description": (
                "当用户需要列出文件、搜索文件内容、查看目录结构等 Windows 文件系统操作时使用。"
                "使用 Windows 命令（如 dir、findstr），不要用 Linux 式的 ls/grep。"
            ),
            "system_prompt": (
                "你是 Windows 文件管理技能代理。必须严格遵循 SKILL.md：仅用 dir、findstr、cd 等 Windows 命令，"
                "通过 execute 执行。完成后用简洁中文汇报命令输出要点。"
            ),
            "skills": ["/teaching_skills/file-manager/"],
        },
        {
            "name": "get-system-info",
            "description": (
                "当用户询问本机系统信息（操作系统、Python 环境、磁盘、网络配置等）时使用。"
                "按 SKILL.md 运行同目录下的 get_system_info.py 获取结构化信息。"
            ),
            "system_prompt": (
                "你是系统信息技能代理。必须严格遵循 SKILL.md：在项目根目录执行 "
                "python teaching_skills/get-system-info/get_system_info.py（或 SKILL 中给出的路径）。\n"
                "用 execute 运行；完成后用简洁中文总结输出。"
            ),
            "skills": ["/teaching_skills/get-system-info/"],
        },
    ]


def build_general_purpose_subagent() -> dict[str, Any]:
    """覆盖默认 general-purpose：不传 skills，收窄使用场景，避免与主代理「自答」冲突。"""
    return {
        "name": "general-purpose",
        "description": (
            "仅当主代理明确要求将复杂、多步任务隔离执行，且 api-data-fetcher、file-manager、"
            "get-system-info 三者均不适用时使用。一般闲聊、常识、与上述三领域无关的问题应由主代理直接回答，"
            "不要优先委派到本代理。"
        ),
        "system_prompt": (
            "你是兜底子代理。只在主代理通过 task 明确委派的任务内工作；使用可用工具完成任务，"
            "最后返回简洁摘要。若任务明显属于三位专家之一，在回复中说明更合适的目标代理名称。"
        ),
    }


MAIN_ROUTER_SYSTEM_PROMPT = """你是主协调代理：你不加载任何技能包（SKILL），只负责理解用户意图并决定是否委派。

## 子代理（仅能通过内置 task 工具调用）
当且仅当用户需求明确属于下列领域时，使用 task(name=..., task=...)，并在 task 中写清用户诉求与必要上下文：
- **api-data-fetcher**：网络/API 公开数据、天气、股票、新闻、汇率等。
- **file-manager**：Windows 下目录与文件列表、文件内搜索、路径相关操作（dir / findstr 等）。
- **get-system-info**：本机操作系统、Python 环境、磁盘、网络等系统信息（按技能脚本获取）。

## 何时不要调用 task
- 闲聊、常识、与上述三类无关的一般问题：请你自己直接回答。
- 无法明确归入三类专家时：请你自己直接回答，不要强行委派。
- **general-purpose** 仅作兜底：不要优先使用；只有在你判断需要长链路隔离且三位专家都不适用时再考虑。

## 风格
回答简洁；委派时 task 字段应包含用户原意与约束。"""


def create_agent_graph(backend: LocalShellBackend, checkpointer: Checkpointer):
    subagents = [
        *build_teaching_subagents(),
        build_general_purpose_subagent(),
    ]
    return create_deep_agent(
        backend=backend,
        model=llm,
        checkpointer=checkpointer,
        subagents=subagents,
        system_prompt=MAIN_ROUTER_SYSTEM_PROMPT,
    )


async def main():
    workspace_dir = Path("./").absolute()
    checkpointer = InMemorySaver()
    backend = build_local_shell_backend(workspace_dir)
    agent = create_agent_graph(backend, checkpointer)

    print("Agent 创建成功")

    thread_id = "demo_thread_01"
    async for response in stream_agent_interaction_corrected(agent, thread_id):
        print(response, end="", flush=True)


async def stream_agent_interaction_corrected(agent, thread_id: str) -> AsyncIterator[str]:
    """
    使用官方推荐的 `agent.stream()` 方法进行流式交互。
    chunk 的结构通常是 (AIMessageChunk, metadata_dict)
    """
    config = {"configurable": {"thread_id": thread_id}}

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

        inputs = {"messages": [{"role": "user", "content": user_input}]}
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
                            if tool_chunk and hasattr(tool_chunk, "get"):
                                if tool_chunk.get("name"):
                                    tool_name = tool_chunk["name"]
                                    yield f"\n[调用工具: {tool_name}]\n"
                else:
                    print(f"\n[调试] 意外的 chunk 结构: {type(chunk)}", file=sys.stderr)
                    continue

        except Exception as e:
            yield f"\n❌ Agent 执行出错: {e}\n"
            import traceback

            traceback.print_exc()
            continue


if __name__ == "__main__":
    asyncio.run(main())
