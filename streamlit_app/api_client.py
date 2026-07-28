import json
from collections.abc import Iterator
from io import BytesIO
from typing import Any

import httpx

STREAM_BLOCK_MS = 1000


def start_graph(
    api_base: str,
    session_id: str,
    user_query: str,
    files: list[Any] | None = None,
) -> str:
    """Start a graph run and return its Redis stream identifier."""
    base = api_base.rstrip("/")

    data = {"session_id": session_id, "user_query": user_query}
    multipart_files: list[tuple[str, tuple[str, BytesIO, str]]] = []

    for upload in files or []:
        name = getattr(upload, "name", None) or "upload"
        raw = upload.getvalue() if hasattr(upload, "getvalue") else upload.read()
        mime = getattr(upload, "type", None) or "application/octet-stream"
        multipart_files.append(("files", (name, BytesIO(raw), mime)))

    with httpx.Client(timeout=None) as client:
        response = client.post(
            f"{base}/send-message",
            data=data,
            files=multipart_files or None,
        )
        response.raise_for_status()
        return response.json()["prompt_id"]


def iter_stream_events(
    api_base: str,
    session_id: str,
    prompt_id: str,
) -> Iterator[dict[str, Any]]:
    """Read SSE packets for an already-started graph run."""
    base = api_base.rstrip("/")

    with httpx.Client(timeout=None) as client:
        last_id = "0-0"
        ended = False

        while not ended:
            payload = {
                "session_id": session_id,
                "prompt_id": prompt_id,
                "start_id": last_id,
                "block_ms": STREAM_BLOCK_MS,
            }
            with client.stream(
                "POST",
                f"{base}/stream-events",
                json=payload,
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    packet = json.loads(line[6:])
                    entry_id = packet.get("id")
                    if entry_id:
                        last_id = entry_id
                    yield packet
                    if packet.get("event") == "stream.end":
                        ended = True
                        break


def cancel_graph(api_base: str, session_id: str) -> dict[str, Any]:
    """Request cancellation of the active generation for a session."""
    with httpx.Client(timeout=None) as client:
        response = client.post(f"{api_base.rstrip('/')}/thread-cancel/{session_id}")
        response.raise_for_status()
        return response.json()


def download_file(api_base: str, session_id: str, relative_path: str) -> bytes:
    """Download a file from the server."""
    params = {
        "session_id": session_id,
        "relative_path": relative_path,
    }
    with httpx.Client(timeout=None) as client:
        response = client.get(
            f"{api_base.rstrip('/')}/files/download",
            params=params,
        )
        response.raise_for_status()
        return response.content
