import os
import queue
import sys
import threading
import uuid
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import streamlit as st

from streamlit_app.api_client import cancel_graph, iter_stream_events, start_graph
from streamlit_app.render import (
    format_tool_result_event,
    render_ai_text,
    render_chat_files,
    render_message,
)

API_BASE = os.getenv("API_BASE", "http://localhost:5001")
POLL_INTERVAL_SECONDS = 0.25


def _init_state() -> None:
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "uploaded_documents" not in st.session_state:
        st.session_state.uploaded_documents = []
    if "shared_files" not in st.session_state:
        st.session_state.shared_files = []
    if "active_run" not in st.session_state:
        st.session_state.active_run = None


def _new_session() -> None:
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []
    st.session_state.uploaded_documents = []
    st.session_state.shared_files = []
    st.session_state.active_run = None


def _consume_events(
    api_base: str,
    session_id: str,
    prompt_id: str,
    events: queue.Queue,
) -> None:
    try:
        for packet in iter_stream_events(api_base, session_id, prompt_id):
            events.put(packet)
    except Exception as exc:
        events.put({"event": "error", "data": {"message": str(exc)}})
        events.put({"event": "stream.end", "data": {}})


def _flush_ai_text(run: dict) -> None:
    text = run["ai_text_buffer"]
    if text:
        run["turn_messages"].append({"type": "ai_text", "content": text})
        run["ai_text_buffer"] = ""


def _apply_packet(run: dict, packet: dict) -> bool:
    event = packet.get("event")
    data = packet.get("data", {})

    if event == "upload.complete":
        for file in data.get("files", []):
            if file not in st.session_state.uploaded_documents:
                st.session_state.uploaded_documents.append(file)
        if run["user_message"]["files"]:
            run["user_message"]["files"] = data.get(
                "files", run["user_message"]["files"]
            )
    elif event == "user.message":
        run["user_message"]["text"] = data.get("text", run["user_message"]["text"])
        run["user_message"]["files"] = data.get("files", [])
    elif event == "ai.tool_call":
        _flush_ai_text(run)
        run["turn_messages"].append(
            {
                "type": "ai_tool_call",
                "text": data.get("text"),
                "tool_calls": data.get("tool_calls", []),
            }
        )
    elif event == "tool.result":
        _flush_ai_text(run)
        run["turn_messages"].append(format_tool_result_event(data))
    elif event == "ai.token":
        run["ai_text_buffer"] += data.get("text", "")
    elif event == "files.presented":
        for file in data.get("files", []):
            if file not in st.session_state.shared_files:
                st.session_state.shared_files.append(file)
    elif event == "widget.presented":
        _flush_ai_text(run)
        for widget in data.get("widgets", []):
            run["turn_messages"].append(
                {
                    "type": "widget",
                    "title": widget.get("title", "Visualization"),
                    "html_content": widget.get("html_content", ""),
                    "height": widget.get("height", 500),
                }
            )
    elif event == "error":
        run["error"] = data.get("message", "Unknown error")
    elif event == "stream.end":
        _flush_ai_text(run)
        return True
    return False


@st.fragment(run_every=POLL_INTERVAL_SECONDS)
def _render_active_run() -> None:
    run = st.session_state.active_run
    if run is None:
        return

    ended = False
    while True:
        try:
            packet = run["events"].get_nowait()
        except queue.Empty:
            break
        ended = _apply_packet(run, packet) or ended

    with st.chat_message("assistant"):
        if run["error"]:
            st.error(run["error"])

        if not ended and not run["cancel_requested"]:
            if st.button("End", key=f"end-{run['prompt_id']}"):
                try:
                    cancel_graph(API_BASE, st.session_state.session_id)
                    run["cancel_requested"] = True
                except Exception as exc:
                    st.error(f"Could not stop generation: {exc}")
        elif run["cancel_requested"]:
            st.caption("Stopping generation…")

        for message in run["turn_messages"]:
            render_message(message)
        if run["ai_text_buffer"]:
            render_ai_text(run["ai_text_buffer"])

    if ended:
        st.session_state.messages.extend(run["turn_messages"])
        st.session_state.active_run = None
        st.rerun()


def _start_run(user_text: str, attached_files: list) -> None:
    user_message = {
        "type": "user",
        "text": user_text,
        "files": [
            {"file_name": f.name, "host_path": "", "container_path": ""}
            for f in attached_files
        ],
    }
    prompt_id = start_graph(
        API_BASE, st.session_state.session_id, user_text, attached_files
    )
    events: queue.Queue = queue.Queue()
    worker = threading.Thread(
        target=_consume_events,
        args=(API_BASE, st.session_state.session_id, prompt_id, events),
        daemon=True,
    )
    st.session_state.messages.append(user_message)
    st.session_state.active_run = {
        "prompt_id": prompt_id,
        "events": events,
        "worker": worker,
        "user_message": user_message,
        "turn_messages": [],
        "ai_text_buffer": "",
        "error": None,
        "cancel_requested": False,
    }
    worker.start()


def main() -> None:
    st.set_page_config(page_title="Workmate AI", layout="wide")
    _init_state()

    col_title, col_action = st.columns([4, 1])
    with col_title:
        st.title("Workmate AI")
        st.caption(f"Session ID: {st.session_state.session_id}")
    with col_action:
        if st.button(
            "New Session",
            use_container_width=True,
            disabled=st.session_state.active_run is not None,
        ):
            _new_session()
            st.rerun()

    with st.sidebar:
        st.header("Chat Files")
        render_chat_files(
            st.session_state.uploaded_documents,
            st.session_state.shared_files,
            api_base=API_BASE,
            session_id=st.session_state.session_id,
        )

    for message in st.session_state.messages:
        role = "user" if message["type"] == "user" else "assistant"
        with st.chat_message(role):
            render_message(message)

    active = st.session_state.active_run
    prompt = st.chat_input(
        "Message Workmate AI",
        accept_file="multiple",
        file_type=None,
        disabled=active is not None,
    )

    if prompt:
        user_text = prompt.text or ""
        attached_files = list(prompt.files or [])
        if user_text.strip() or attached_files:
            try:
                _start_run(user_text, attached_files)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    if st.session_state.active_run is not None:
        _render_active_run()


if __name__ == "__main__":
    main()
