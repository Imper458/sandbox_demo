from pathlib import Path

from deepagents import create_deep_agent, CompiledSubAgent
from langgraph.checkpoint.memory import InMemorySaver
from my_agent import build_local_shell_backend, llm
from langchain.agents import create_agent

if __name__ == '__main__':

        workspace_dir = Path("./").absolute()
        checkpointer = InMemorySaver()
        backend = build_local_shell_backend(workspace_dir)

        agent3 = create_deep_agent(
                model=llm,
                system_prompt="你是第三个agent"
        )
        agent2 = create_deep_agent(
                backend=backend,
                model=llm,
                system_prompt="你是第二个agent",
                subagents=[
                        {
                                "name": "agent3",
                                "description": "处理第三层任务",
                                "runnable": agent3
                        }
                ]
        )

        agent1 = create_deep_agent(
                backend=backend,
                model=llm,
                system_prompt="你是第一个agent",
                subagents=[
                        {
                                "name": "agent2",
                                "description": "处理第二层任务",
                                "runnable": agent2
                        }
                ]
        )

        resp = agent1.invoke(
                {"messages":[{"role":"user","content":"你有几个agent"}]}
        )

        print(resp)