from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from ...utils import logger
from ..state import Todo
from .tool_descriptions import READ_TODOS_DESCRIPTION, WRITE_TODOS_DESCRIPTION


def _todo_tool_response(
    runtime: ToolRuntime,
    feedback: str,
    *,
    is_error: bool = False,
    todos: list[Todo] | None = None,
) -> Command:
    update: dict = {
        "messages": [
            ToolMessage(
                feedback,
                tool_call_id=runtime.tool_call_id,
                status="error" if is_error else "success",
            )
        ],
    }
    if todos is not None:
        update["todos"] = todos
    return Command(update=update)


class WriteTodosInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    todos: list[Todo] = Field(description="List of Todo items with content and status")
    runtime: ToolRuntime


class ReadTodosInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    runtime: ToolRuntime


@tool(description=WRITE_TODOS_DESCRIPTION, parse_docstring=True, args_schema=WriteTodosInput)
def write_todos(todos: list[Todo], runtime: ToolRuntime) -> Command:
    """Create or update the agent's TODO list for task planning and tracking.

    Args:
        todos: List of Todo items with content and status

    Returns:
        Command to update agent state with new TODO list
    """
    try:
        return _todo_tool_response(
            runtime,
            f"Updated todo list to {todos}",
            todos=todos,
        )
    except Exception as exc:
        logger.error("Error writing todos: %s", exc)
        return _todo_tool_response(runtime, f"Error: {exc}", is_error=True)


@tool(description=READ_TODOS_DESCRIPTION, parse_docstring=True, args_schema=ReadTodosInput)
def read_todos(runtime: ToolRuntime) -> Command:
    """Read the current TODO list from the agent state.

    Args:
        runtime: Tool runtime containing the current TODO list

    Returns:
        Command with formatted TODO list in the tool message
    """
    try:
        todos = runtime.state.get("todos", [])
        if not todos:
            return _todo_tool_response(runtime, "No todos currently in the list.")

        result = "Current TODO List:\n"
        for i, todo in enumerate(todos, 1):
            status_emoji = {"pending": "⏳", "in_progress": "🔄", "completed": "✅"}
            emoji = status_emoji.get(todo["status"], "❓")
            result += f"{i}. {emoji} {todo['content']} ({todo['status']})\n"

        return _todo_tool_response(runtime, result.strip())
    except Exception as exc:
        logger.error("Error reading todos: %s", exc)
        return _todo_tool_response(runtime, f"Error: {exc}", is_error=True)
