"""
Entry point (aggregator) for the agent system.

This file intentionally contains no business logic: it only re-exports
symbols from the module-separated implementation.
"""

import sys
from pathlib import Path

# Support running as a script (not as a module):
# ensure project root is on sys.path so `import src...` works.
_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

import asyncio

from src.agents.my_agent_agents import (
    IntentRoute,
    DisableWriteFileMiddleware,
    build_local_shell_backend,
    build_router_agent,
    build_specialist_agents,
    get_python_executable,
    llm,
    router_llm,
)
from src.agents.my_agent_debug import _router_debug_enabled, dump_router_debug, format_router_state_debug
from src.agents.my_agent_prompts import (
    API_DATA_FETCHER_SYSTEM,
    FILE_MANAGER_SYSTEM,
    GENERAL_PURPOSE_SYSTEM,
    GET_SYSTEM_INFO_SYSTEM,
    ROUTER_INTENT_SYSTEM_PROMPT,
)
from src.agents.my_agent_workflow import (
    WorkflowState,
    build_workflow_graph,
    main,
    route_user_input,
    stream_agent_interaction_multi,
    stream_specialist,
)


if __name__ == "__main__":
    asyncio.run(main())

