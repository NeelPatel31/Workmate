from langchain.tools import ToolRuntime, tool
from langchain_core.messages import ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command
from pydantic import BaseModel, ConfigDict, Field

from ...container_handlers import CONTAINER_MANAGER
from ...container_handlers.constants import COMMAND_TIMEOUT_SEC
from ...container_handlers.errors import ContainerFileSystemError
from ...utils import logger
from .executor import (
    create_container_file,
    insert_in_container_file,
    present_container_files,
    str_replace_in_container_file,
    view_container_path_content,
)
from .tool_descriptions import (
    BASH_TOOL_DESCRIPTION,
    CREATE_FILE_DESCRIPTION,
    INSERT_DESCRIPTION,
    PRESENT_FILES_DESCRIPTION,
    STR_REPLACE_DESCRIPTION,
    VIEW_FILE_DESCRIPTION,
)


def _session_id(runtime: ToolRuntime) -> str:
    return runtime.config.get("configurable", {})["thread_id"]


def _file_tool_response(
    runtime: ToolRuntime,
    feedback: str,
    *,
    is_error: bool = False,
) -> Command:
    return Command(
        update={
            "messages": [
                ToolMessage(
                    feedback,
                    tool_call_id=runtime.tool_call_id,
                    status="error" if is_error else "success",
                )
            ],
        }
    )


class BashToolInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    description: str = Field(..., description="How this tool call will be useful.")
    command: str = Field(..., description="The bash command to execute.")
    runtime: ToolRuntime
    restart: bool = Field(description="Whether to restart the command.", default=False)
    timeout: int | None = Field(description="The timeout for the command.", default=None)


@tool(description=BASH_TOOL_DESCRIPTION, parse_docstring=True, args_schema=BashToolInput)
async def bash_tool(
    description: str,
    command: str,
    runtime: ToolRuntime,
    restart: bool = False,
    timeout: int | None = None,
):
    try:
        session_id = _session_id(runtime)
        new_timeout = COMMAND_TIMEOUT_SEC if not timeout else min(timeout, COMMAND_TIMEOUT_SEC)
        feedback, is_error = await CONTAINER_MANAGER.bash_tool_helper(
            session_id, command, new_timeout, restart
        )
        logger.info(f"Feedback: {feedback}")
        logger.info(f"Is error: {is_error}")
    except Exception as e:
        logger.error(f"Error executing command: {e}")
        feedback = f"Error: {e}"
        is_error = True

    return _file_tool_response(runtime, feedback, is_error=is_error)


class ViewFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: str = Field(..., description="The path of the file to view.")
    description: str = Field(..., description="How this tool call will be useful.")
    runtime: ToolRuntime
    view_range: list[int] | None = Field(description="The range of lines to view.", default=None)
    max_chars: int | None = Field(description="The maximum number of characters to view.", default=None)


@tool(description=VIEW_FILE_DESCRIPTION, parse_docstring=True, args_schema=ViewFileInput)
async def view_file(
    path: str,
    description: str,
    runtime: ToolRuntime,
    view_range: list[int] | None = None,
    max_chars: int | None = None,
) -> Command:
    try:
        msg = await view_container_path_content(
            _session_id(runtime),
            path,
            view_range=view_range,
            max_chars=max_chars,
        )
    except ContainerFileSystemError as exc:
        return _file_tool_response(runtime, f"Error: {exc}", is_error=True)
    except Exception as e:
        logger.error(f"Error viewing file: {e}")
        return _file_tool_response(runtime, f"Error: {e}", is_error=True)

    return _file_tool_response(runtime, msg)


class StrReplaceInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: str = Field(..., description="The path of the file to replace the string in.")
    old_string: str = Field(..., description="The old string to replace.")
    new_string: str = Field(..., description="The new string to replace the old string with.")
    description: str = Field(..., description="How this tool call will be useful.")
    runtime: ToolRuntime


@tool(description=STR_REPLACE_DESCRIPTION, parse_docstring=True, args_schema=StrReplaceInput)
async def str_replace(
    path: str,
    old_string: str,
    new_string: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    try:
        msg = await str_replace_in_container_file(
            _session_id(runtime),
            path,
            old_string,
            new_string,
        )
    except ContainerFileSystemError as exc:
        return _file_tool_response(runtime, f"Error: {exc}", is_error=True)
    except Exception as e:
        logger.error(f"Error replacing string in file: {e}")
        return _file_tool_response(runtime, f"Error: {e}", is_error=True)

    return _file_tool_response(runtime, msg)


class CreateFileInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: str = Field(..., description="The path of the file to create.")
    file_text: str = Field(..., description="The text to write to the file.")
    description: str = Field(..., description="How this tool call will be useful.")
    runtime: ToolRuntime


@tool(description=CREATE_FILE_DESCRIPTION, parse_docstring=True, args_schema=CreateFileInput)
async def create_file(
    path: str,
    file_text: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    try:
        msg = await create_container_file(_session_id(runtime), path, file_text)
    except ContainerFileSystemError as exc:
        return _file_tool_response(runtime, f"Error: {exc}", is_error=True)
    except Exception as e:
        logger.error(f"Error creating file: {e}")
        return _file_tool_response(runtime, f"Error: {e}", is_error=True)

    return _file_tool_response(runtime, msg)


class InsertInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    path: str = Field(..., description="The path of the file to insert the text into.")
    insert_line: int = Field(..., description="The line to insert the text at.")
    insert_text: str = Field(..., description="The text to insert.")
    description: str = Field(..., description="How this tool call will be useful.")
    runtime: ToolRuntime


@tool(description=INSERT_DESCRIPTION, parse_docstring=True, args_schema=InsertInput)
async def insert(
    path: str,
    insert_line: int,
    insert_text: str,
    description: str,
    runtime: ToolRuntime,
) -> Command:
    try:
        msg = await insert_in_container_file(
            _session_id(runtime),
            path,
            insert_line,
            insert_text,
        )
    except ContainerFileSystemError as exc:
        return _file_tool_response(runtime, f"Error: {exc}", is_error=True)
    except Exception as e:
        logger.error(f"Error inserting text into file: {e}")
        return _file_tool_response(runtime, f"Error: {e}", is_error=True)

    return _file_tool_response(runtime, msg)


class PresentFilesInput(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    paths: list[str] = Field(..., description="The paths of the files to present.")
    description: str = Field(..., description="How this tool call will be useful.")
    runtime: ToolRuntime


@tool(description=PRESENT_FILES_DESCRIPTION, parse_docstring=True, args_schema=PresentFilesInput)
async def present_files(
    paths: list[str],
    description: str,
    runtime: ToolRuntime,
) -> Command:
    try:
        msg, records = await present_container_files(_session_id(runtime), paths)
        get_stream_writer()({"kind": "files.presented", "files": records})
    except ContainerFileSystemError as exc:
        return _file_tool_response(runtime, f"Error: {exc}", is_error=True)
    except Exception as e:
        logger.error(f"Error presenting file: {e}")
        return _file_tool_response(runtime, f"Error: {e}", is_error=True)

    return Command(
        update={
            "messages": [
                ToolMessage(
                    msg,
                    tool_call_id=runtime.tool_call_id,
                    status="success",
                )
            ],
            "presented_files": records,
        }
    )
