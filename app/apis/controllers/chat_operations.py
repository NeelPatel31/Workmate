import asyncio
import contextlib
import json
import uuid
from collections.abc import AsyncGenerator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from app.agent_registry import workmate_agent
from app.apis.controllers.file_operations import InMemoryUpload, upload_session_files
from app.container_handlers.constants import AI_TOKEN_BATCH_SIZE
from app.utils import (
    add_token,
    get_thread_status,
    get_trace_id,
    logger,
    mark_done,
    prompt_belongs_to_session,
    read_stream,
    register_prompt,
    set_thread_status,
)

_background_tasks: set[asyncio.Task] = set()
_active_agent_tasks: dict[str, asyncio.Task] = {}
_active_prompt_ids: dict[str, str] = {}
_starting_sessions: set[str] = set()
_started_agent_sessions: set[str] = set()

USER_CANCELLED_TOOL_MESSAGE = "Execution cancelled by user."


class ActiveRunError(RuntimeError):
    """Raised when a session already owns a running agent task."""


def build_agent_query(user_query: str, uploaded_files: list[dict]) -> str:
    main_query = ""
    if uploaded_files:
        main_query += "<uploaded_files>\n"
        for file in uploaded_files:
            main_query += "  <file>\n"
            main_query += f"    <name>{file['file_name']}</name>\n"
            main_query += f"    <path>{file['container_path']}</path>\n"
            main_query += "  </file>\n"
        main_query += "</uploaded_files>\n"
    main_query += f"<user_query>\n{user_query}\n</user_query>\n"
    return main_query.strip()


async def _push(prompt_id: str, event: str, data: dict) -> None:
    payload = json.dumps({"event": event, "data": data})
    logger.debug(payload)
    await add_token(prompt_id, payload)


def _normalize_custom(payload) -> tuple[str, dict] | None:
    if not isinstance(payload, dict):
        return None

    kind = payload.get("kind")
    if kind == "tool.result":
        return "tool.result", {
            "id": payload.get("tool_call_id") or payload.get("id"),
            "name": payload.get("name"),
            "content": payload.get("content"),
            "status": payload.get("status", "success"),
        }
    if kind == "files.presented":
        return "files.presented", {"files": payload.get("files", [])}
    if kind == "widget.presented":
        return "widget.presented", {"widgets": payload.get("widgets", [])}

    return None


def _ai_tool_call_event(message: AIMessage) -> dict:
    return {
        "text": message.content or None,
        "tool_calls": [
            {
                "id": tool_call["id"],
                "name": tool_call["name"],
                "args": tool_call["args"],
            }
            for tool_call in message.tool_calls
        ],
    }


class _TokenBatcher:
    def __init__(self, batch_size: int) -> None:
        self._batch_size = batch_size
        self._buffer: list[str] = []

    def add(self, text: str) -> str | None:
        self._buffer.append(text)
        if len(self._buffer) >= self._batch_size:
            return self.flush()
        return None

    def flush(self) -> str | None:
        if not self._buffer:
            return None
        combined = "".join(self._buffer)
        self._buffer.clear()
        return combined


async def start_chat(
    session_id: str,
    user_query: str,
    uploads: list[InMemoryUpload],
) -> str:
    if not user_query.strip() and not uploads:
        raise ValueError("Either user_query or uploaded_files must be provided")

    if session_id in _starting_sessions or await get_thread_status(session_id) == "running":
        raise ActiveRunError(f"A generation is already running for session '{session_id}'")

    _starting_sessions.add(session_id)
    try:
        uploaded_records = await upload_session_files(session_id, uploads)
        query = build_agent_query(user_query, uploaded_records)

        prompt_id = str(uuid.uuid4())
        await register_prompt(session_id, prompt_id)
        await set_thread_status(session_id, "running")

        await _push(prompt_id, "upload.complete", {"files": uploaded_records})
        await _push(prompt_id, "user.message", {"text": user_query, "files": uploaded_records})

        _launch_agent_task(session_id, prompt_id, query)
        return prompt_id
    finally:
        _starting_sessions.discard(session_id)


def _launch_agent_task(
    session_id: str,
    prompt_id: str,
    agent_input,
) -> asyncio.Task:
    task = asyncio.create_task(_run_agent(prompt_id, session_id, agent_input))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    _active_agent_tasks[session_id] = task
    _active_prompt_ids[session_id] = prompt_id

    def _remove_active_task(completed_task: asyncio.Task) -> None:
        if _active_agent_tasks.get(session_id) == completed_task:
            _active_agent_tasks.pop(session_id, None)
            _active_prompt_ids.pop(session_id, None)

    task.add_done_callback(_remove_active_task)
    return task


async def get_chat_status(session_id: str) -> str | None:
    return await get_thread_status(session_id)


async def cancel_chat(session_id: str) -> tuple[str, bool]:
    status = await get_thread_status(session_id)
    if status is None:
        raise LookupError(f"No generation was found for session '{session_id}'")
    if status != "running":
        return status, False

    task = _active_agent_tasks.get(session_id)
    if task is None:
        raise RuntimeError(f"The active generation for session '{session_id}' is unavailable")

    task_had_started = session_id in _started_agent_sessions
    prompt_id = _active_prompt_ids.get(session_id)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    if not task_had_started:
        if prompt_id is not None:
            await _push(prompt_id, "stream.end", {})
            await mark_done(prompt_id)
        await set_thread_status(session_id, "complete")

    return (await get_thread_status(session_id)) or "complete", True


async def _repair_cancelled_tool_calls(agent, config: dict) -> None:
    snapshot = await agent.aget_state(config)
    messages = snapshot.values.get("messages", [])
    if not messages:
        return

    answered_tool_call_ids = {
        message.tool_call_id
        for message in messages
        if isinstance(message, ToolMessage) and message.tool_call_id
    }

    last_ai_message = next(
        (message for message in reversed(messages) if isinstance(message, AIMessage)),
        None,
    )
    if last_ai_message is None:
        return

    missing_messages: list[ToolMessage] = []
    for tool_call in last_ai_message.tool_calls:
        tool_call_id = tool_call.get("id")
        if not tool_call_id or tool_call_id in answered_tool_call_ids:
            continue
        missing_messages.append(
            ToolMessage(
                content=USER_CANCELLED_TOOL_MESSAGE,
                tool_call_id=tool_call_id,
                name=tool_call.get("name"),
            )
        )
        answered_tool_call_ids.add(tool_call_id)

    if missing_messages:
        logger.info(
            "Repairing cancelled tool calls for session '%s' with messages: %s",
            config["configurable"]["thread_id"],
            len(missing_messages),
        )
        await agent.aupdate_state(config, {"messages": missing_messages}, as_node="tools")


async def _run_agent(prompt_id: str, session_id: str, agent_input: str) -> None:
    _started_agent_sessions.add(session_id)
    stream = None
    cancelled = False
    config = {
        "configurable": {"thread_id": session_id},
        "metadata": {"trace_id": get_trace_id()},
    }
    try:
        await _repair_cancelled_tool_calls(workmate_agent, config)

        hm = HumanMessage(content=agent_input)
        logger.debug(hm.pretty_repr())
        input_state = {"messages": [hm]}

        token_batcher = _TokenBatcher(AI_TOKEN_BATCH_SIZE)

        async def _flush_token_batch() -> None:
            batched = token_batcher.flush()
            if batched:
                await _push(prompt_id, "ai.token", {"text": batched})

        stream = workmate_agent.astream(
            input_state,
            config=config,
            stream_mode=["messages", "updates", "custom"],
        )
        async for chunk in stream:
            chunk_type = chunk[0]
            chunk_data = chunk[1]

            if chunk_type == "messages":
                token, _metadata = chunk_data
                if isinstance(token, AIMessageChunk):
                    if token.tool_call_chunks:
                        continue
                    text = token.text
                    if text:
                        batched = token_batcher.add(text)
                        if batched is not None:
                            await _push(prompt_id, "ai.token", {"text": batched})

            elif chunk_type == "updates":
                await _flush_token_batch()
                for _source, update in chunk_data.items():
                    if not isinstance(update, dict):
                        continue
                    messages = update.get("messages") or []
                    if not messages:
                        continue
                    message = messages[-1]
                    if isinstance(message, AIMessage) and message.tool_calls:
                        await _push(prompt_id, "ai.tool_call", _ai_tool_call_event(message))

            elif chunk_type == "custom":
                await _flush_token_batch()
                normalized = _normalize_custom(chunk_data)
                if normalized:
                    event_name, event_data = normalized
                    await _push(prompt_id, event_name, event_data)

        await _flush_token_batch()
        await set_thread_status(session_id, "complete")
        await _push(prompt_id, "stream.end", {})

    except asyncio.CancelledError:
        cancelled = True
        logger.info("Cancelling stream for session '%s'", session_id)
        raise
    except Exception as exc:
        logger.error("Stream error: %s", exc)
        await set_thread_status(session_id, "error")
        await _push(prompt_id, "error", {"message": str(exc)})
        await _push(prompt_id, "stream.end", {})
    finally:
        if stream is not None:
            with contextlib.suppress(Exception):
                await stream.aclose()

        if cancelled and workmate_agent is not None:
            try:
                await _repair_cancelled_tool_calls(workmate_agent, config)
            except Exception:
                logger.exception(
                    "Failed to repair cancelled tool calls for session '%s'", session_id
                )
            await set_thread_status(session_id, "complete")
            await _push(prompt_id, "stream.end", {})

        _started_agent_sessions.discard(session_id)
        await mark_done(prompt_id)


async def stream_chat_events(
    session_id: str,
    prompt_id: str,
    start_id: str = "0-0",
    block_ms: int = 100,
) -> AsyncGenerator[str, None]:
    if not await prompt_belongs_to_session(session_id, prompt_id):
        raise LookupError(
            f"prompt_id '{prompt_id}' does not belong to session '{session_id}'"
        )

    async for entry_id, token in read_stream(prompt_id, block_ms, start_id):
        payload = json.loads(token)
        yield f"data: {json.dumps({'id': entry_id, **payload})}\n\n"
