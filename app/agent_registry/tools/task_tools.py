from typing import NotRequired

from langchain.agents import create_agent
from langchain.tools import BaseTool, ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import TypedDict

from ..prompts import TASK_DESCRIPTION_PREFIX
from ..state import DeepAgentState


class SubAgent(TypedDict):
    """Configuration for a specialized sub-agent."""

    name: str
    description: str
    system_prompt: str
    tools: NotRequired[list[str]]


def _create_task_tool(tools, subagents: list[SubAgent], model, state_schema):
    """Create a task delegation tool that enables context isolation through sub-agents."""
    agents = {}

    # Build tool name mapping for selective tool assignment
    tools_by_name = {}
    for tool_ in tools:
        if not isinstance(tool_, BaseTool):
            tool_ = tool(tool_)
        tools_by_name[tool_.name] = tool_

    # Create specialized sub-agents based on configurations
    for _agent in subagents:
        if "tools" in _agent:
            # Use specific tools if specified
            _tools = [tools_by_name[t] for t in _agent["tools"]]
        else:
            # Default to all tools
            _tools = tools
        agents[_agent["name"]] = create_agent(
            model,
            system_prompt=_agent["system_prompt"],
            tools=_tools,
            state_schema=state_schema,
        )

    # Generate description of available sub-agents for the tool description
    other_agents_string = [
        f"- {_agent['name']}: {_agent['description']}" for _agent in subagents
    ]

    @tool(description=TASK_DESCRIPTION_PREFIX.format(other_agents=other_agents_string))
    async def task(
        description: str,
        subagent_type: str,
        runtime: ToolRuntime,
    ):
        """Delegate a task to a specialized sub-agent with isolated context."""
        if subagent_type not in agents:
            msg = (
                f"Error: invoked agent of type {subagent_type}, the only allowed types are "
                f"{[f'`{k}`' for k in agents]}"
            )
            return Command(
                update={"messages": [ToolMessage(msg, tool_call_id=runtime.tool_call_id)]}
            )

        sub_agent = agents[subagent_type]
        state = dict(runtime.state)
        state["messages"] = [{"role": "user", "content": description}]
        result = await sub_agent.ainvoke(state)

        return Command(
            update={
                "messages": [
                    ToolMessage(
                        result["messages"][-1].content,
                        tool_call_id=runtime.tool_call_id,
                    )
                ],
            }
        )

    return task
