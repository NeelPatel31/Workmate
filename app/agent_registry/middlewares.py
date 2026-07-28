from collections.abc import Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    SummarizationMiddleware,
    ToolCallRequest,
)
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.types import Command

from ..utils import logger
from .llms import model
from .prompts import SEPERATOR, SKILL_USAGE_INSTRUCTIONS
from .skills_helper import get_skills_xml

summarization_middleware = SummarizationMiddleware(
    model=model,
    trigger=[("tokens", 72000), ("messages", 100)],
    keep=("messages", 20),
)


class SkillsMiddleware(AgentMiddleware):
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        currently_available_skills = get_skills_xml()
        if not currently_available_skills:
            return await handler(request)

        skill_addendum = SEPERATOR + SKILL_USAGE_INSTRUCTIONS.format(
            loaded_skills=currently_available_skills
        )
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skill_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        return await handler(request.override(system_message=new_system_message))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        try:
            result = await handler(request)
            writer = get_stream_writer()
            message = result
            if isinstance(result, Command):
                messages = (result.update or {}).get("messages") or []
                message = messages[-1]

            logger.info("\n" + message.pretty_repr())
            kwargs = message.to_json()["kwargs"]
            writer({"kind": "tool.result", "name": request.tool_call["name"], **kwargs})
            return result
        except Exception as exc:
            logger.error("Tool failed: %s", exc)
            raise
