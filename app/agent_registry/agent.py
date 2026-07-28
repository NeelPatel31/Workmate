from langchain.agents import create_agent

from .checkpointers import checkpointer
from .llms import model
from .middlewares import SkillsMiddleware, summarization_middleware
from .prompts import (
    FILESYSTEM_ENVIRONMENT_INSTRUCTION,
    MAIN_AGENT_INSTRUCTION,
    SEPERATOR,
    SUBAGENT_USAGE_INSTRUCTIONS,
    TODO_INSTRUCTION,
)
from .state import DeepAgentState
from .subagents import visual_designer_sub_agent
from .tools import (
    _create_task_tool,
    bash_tool,
    create_file,
    display_widget,
    insert,
    present_files,
    read_todos,
    str_replace,
    view_file,
    write_todos,
)

sub_agent_tools = [
    bash_tool,
    view_file,
    str_replace,
    create_file,
    insert,
    present_files,
]

built_in_tools = [
    write_todos,
    read_todos,
    bash_tool,
    view_file,
    str_replace,
    create_file,
    insert,
    present_files,
    display_widget,
]

task_tool = _create_task_tool(
    sub_agent_tools, [visual_designer_sub_agent], model, DeepAgentState
)

all_tools = built_in_tools + [task_tool]

INSTRUCTION = (
    MAIN_AGENT_INSTRUCTION
    + SEPERATOR
    + FILESYSTEM_ENVIRONMENT_INSTRUCTION
    + SEPERATOR
    + TODO_INSTRUCTION
    + SEPERATOR
    + SUBAGENT_USAGE_INSTRUCTIONS
)

workmate_agent = create_agent(
    model=model,
    tools=all_tools,
    system_prompt=INSTRUCTION,
    state_schema=DeepAgentState,
    checkpointer=checkpointer,
    middleware=[
        summarization_middleware,
        SkillsMiddleware(),
    ],
)


