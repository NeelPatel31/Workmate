from pathlib import Path
from typing import Any

import streamlit as st
import streamlit.components.v1 as components


def render_user_message(text: str, files: list[dict[str, str]]) -> None:
    if text.strip():
        st.markdown(text)
    if files:
        st.markdown("**Attached files:**")
        for file in files:
            st.markdown(
                f"- `{file['file_name']}` "
                f"(container: `{file.get('container_path', '')}`)"
            )


def render_ai_tool_call(text: str | None, tool_calls: list[dict[str, Any]]) -> None:
    if text:
        st.markdown(text)
    for tool_call in tool_calls:
        name = tool_call.get("name", "unknown")
        tool_call_id = tool_call.get("id", "unknown")
        with st.expander(f"tool call | {name} | {tool_call_id}", expanded=False):
            st.json(tool_call)


def render_tool_result(payload: dict[str, Any]) -> None:
    name = payload.get("name") or "unknown"
    tool_call_id = payload.get("id") or payload.get("tool_call_id") or "unknown"
    status = payload.get("status", "success")
    status_label = "❌ failed" if status == "error" else "✅ success"
    with st.expander(
        f"ToolMessage | {name} | {tool_call_id} | {status_label}",
        expanded=False,
    ):
        st.json(payload)


def render_ai_text(content: str) -> None:
    if content.strip():
        st.markdown(content)


def render_widget(title: str, html_content: str, height: int = 500) -> None:
    if not html_content:
        return
    st.markdown(f"**📊 {title}**")
    with st.expander(f"Widget: {title}", expanded=True):
        components.html(html_content, height=height, scrolling=True)


@st.cache_data(show_spinner="Downloading file from container...")
def _download_file_cached(api_base: str, session_id: str, relative_path: str) -> bytes:
    from streamlit_app.api_client import download_file

    return download_file(api_base, session_id, relative_path)


def render_chat_files(
    uploaded_documents: list[dict[str, str]],
    shared_files: list[dict[str, str]],
    api_base: str | None = None,
    session_id: str | None = None,
) -> None:
    st.subheader("Uploaded documents")
    if uploaded_documents:
        for file in uploaded_documents:
            with st.expander(label=f"{file['file_name']}", expanded=False):
                st.json(
                    {
                        "file_name": file["file_name"],
                        "host_path": file.get("host_path", ""),
                        "container_path": file.get("container_path", ""),
                    }
                )
    else:
        st.caption("No uploaded documents yet.")

    st.divider()
    st.subheader("Shared by agent")
    if shared_files:
        for file in shared_files:
            with st.expander(label=f"{file['file_name']}", expanded=False):
                st.json(
                    {
                        "file_name": file["file_name"],
                        "host_path": file.get("host_path", ""),
                        "container_path": file.get("container_path", ""),
                    }
                )
            host_path = Path(file.get("host_path", ""))
            if host_path.is_file():
                st.download_button(
                    label=f"Download {file['file_name']}",
                    data=host_path.read_bytes(),
                    file_name=file["file_name"],
                    key=f"dl-{file['container_path']}",
                )
            elif api_base and session_id:
                container_path = file.get("container_path", "")
                if container_path.startswith("/workspace/"):
                    relative_path = container_path[len("/workspace/") :]
                elif container_path.startswith("/workspace"):
                    relative_path = container_path[len("/workspace") :].lstrip("/")
                else:
                    relative_path = container_path.lstrip("/")

                try:
                    file_data = _download_file_cached(
                        api_base=api_base,
                        session_id=session_id,
                        relative_path=relative_path,
                    )
                    st.download_button(
                        label=f"Download {file['file_name']}",
                        data=file_data,
                        file_name=file["file_name"],
                        key=f"dl-{file['container_path']}",
                    )
                except Exception as exc:
                    st.error(f"Failed to load download data: {exc}")
            else:
                st.warning("Download unavailable")
    else:
        st.caption("No files shared by the agent yet.")


def render_message(message: dict[str, Any]) -> None:
    msg_type = message["type"]
    if msg_type == "user":
        render_user_message(message.get("text", ""), message.get("files", []))
    elif msg_type == "ai_tool_call":
        render_ai_tool_call(message.get("text"), message.get("tool_calls", []))
    elif msg_type == "tool_result":
        render_tool_result(message)
    elif msg_type == "ai_text":
        render_ai_text(message.get("content", ""))
    elif msg_type == "widget":
        render_widget(
            message.get("title", "Visualization"),
            message.get("html_content", ""),
            message.get("height", 500),
        )


def format_tool_result_event(data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "tool_result", **data}
