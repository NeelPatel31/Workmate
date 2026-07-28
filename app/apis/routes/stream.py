from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.apis.controllers.chat_operations import (
    ActiveRunError,
    cancel_chat,
    get_chat_status,
    start_chat,
    stream_chat_events,
)
from app.apis.controllers.file_operations import InMemoryUpload
from app.container_handlers.session_paths import SessionPaths
from app.utils import logger
from app.validation_models import StreamEventsRequest

stream_router = APIRouter(tags=["stream"])


@stream_router.post("/send-message")
async def send_message_endpoint(
    session_id: str = Form(...),
    user_query: str = Form(""),
    files: list[UploadFile] = File(default=[]),
):
    """Accept a message request, upload files, return prompt_id."""
    uploads = [
        InMemoryUpload(
            filename=f.filename or "upload",
            content=await f.read(),
            content_type=f.content_type or "application/octet-stream",
        )
        for f in files
    ]

    try:
        prompt_id = await start_chat(session_id, user_query, uploads)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ActiveRunError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.error("Failed to start chat: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return JSONResponse(content={"prompt_id": prompt_id})


@stream_router.post("/stream-events")
async def stream_events_endpoint(request: StreamEventsRequest):
    """Stream agent events from Redis for a given prompt_id."""
    events = stream_chat_events(
        request.session_id,
        request.prompt_id,
        request.start_id,
        request.block_ms,
    )

    try:
        first_line = await events.__anext__()
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except StopAsyncIteration:

        async def _empty():
            if False:
                yield ""

        return StreamingResponse(_empty(), media_type="text/event-stream")

    async def generate():
        yield first_line
        async for line in events:
            yield line

    return StreamingResponse(generate(), media_type="text/event-stream")


@stream_router.get("/thread-status/{session_id}")
async def thread_status(session_id: str):
    status = await get_chat_status(session_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Generation not found")
    return JSONResponse(content={"session_id": session_id, "status": status})


@stream_router.post("/thread-cancel/{session_id}")
async def cancel_thread(session_id: str):
    try:
        status, cancelled = await cancel_chat(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if not cancelled:
        return JSONResponse(
            content={
                "session_id": session_id,
                "status": status,
                "cancelled": False,
                "message": "Generation is complete and cannot be stopped.",
            }
        )

    return JSONResponse(
        content={"session_id": session_id, "status": status, "cancelled": True}
    )


@stream_router.get("/files/download")
async def download_file(session_id: str, relative_path: str):
    if not relative_path.startswith(("uploads/", "output/")):
        raise HTTPException(status_code=400, detail="Invalid path")

    paths = SessionPaths.create(session_id)
    try:
        file_path = paths.resolve_host_path(relative_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(path=file_path, filename=Path(relative_path).name)
