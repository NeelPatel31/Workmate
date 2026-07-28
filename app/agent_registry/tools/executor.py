import shutil
from pathlib import Path

from ...container_handlers import CONTAINER_MANAGER
from ...container_handlers.errors import ContainerFileSystemError
from ...container_handlers.bash_handler import BashSession


def _relative_session_path(session: BashSession, container_path: str) -> str:
    """Return agent-facing relative path under /workspace."""
    try:
        virtual_path = session.paths.container_to_virtual_path(container_path)
    except ValueError:
        return container_path
    return virtual_path.removeprefix(f"{session.paths.virtual_root.rstrip('/')}/")


async def view_container_path_content(
    session_id: str,
    path: str,
    view_range: list[int] | None = None,
    max_chars: int | None = None,
) -> str:
    """View the contents of a file in the file system."""
    session = await CONTAINER_MANAGER.get_or_create(session_id)
    container_path = session.resolve_container_path(path)

    relative_path = _relative_session_path(session, container_path)

    if not await session.path_exists(container_path):
        raise ContainerFileSystemError(f"Path not found: {relative_path}")

    if await session.is_directory(container_path):
        if view_range is not None:
            raise ContainerFileSystemError(
                "view_range is only supported when viewing files"
            )
        return await session.list_directory(container_path)

    if not await session.is_file(container_path):
        raise ContainerFileSystemError(f"Path not found: {relative_path}")

    content = await session.read_container_file(container_path)
    lines = content.splitlines()
    selected_lines, start_line = _apply_view_range(lines, view_range)
    formatted = _format_with_line_numbers(selected_lines, start_line)

    if max_chars is not None and len(formatted) > max_chars:
        omitted = len(formatted) - max_chars
        formatted = (
            f"{formatted[:max_chars]}\n"
            f"... ({omitted} characters truncated) ..."
        )

    return formatted


async def create_container_file(
    session_id: str,
    path: str,
    file_text: str,
) -> str:
    """Create a file in the file system."""
    session = await CONTAINER_MANAGER.get_or_create(session_id)
    container_path = session.resolve_container_path(path)
    await session.create_container_file(container_path, file_text)
    return f"Successfully created {_relative_session_path(session, container_path)}."


async def str_replace_in_container_file(
    session_id: str,
    path: str,
    old_string: str,
    new_string: str,
) -> str:
    """Replace a string in a file in the file system."""
    session = await CONTAINER_MANAGER.get_or_create(session_id)
    container_path = session.resolve_container_path(path)
    relative_path = _relative_session_path(session, container_path)

    if not await session.is_file(container_path):
        raise ContainerFileSystemError(f"File not found: {relative_path}")

    content = await session.read_container_file(container_path)
    count = content.count(old_string)
    if count == 0:
        raise ContainerFileSystemError(
            "No match found for replacement. Please check your text and try again."
        )
    if count > 1:
        raise ContainerFileSystemError(
            f"Found {count} matches for replacement text. "
            "Please provide more context to make a unique match."
        )

    await session.write_container_file(
        container_path,
        content.replace(old_string, new_string, 1),
    )
    return f"Successfully replaced text in {relative_path}."


async def insert_in_container_file(
    session_id: str,
    path: str,
    insert_line: int,
    insert_text: str,
) -> str:
    """Insert text into a file at a specific line."""
    session = await CONTAINER_MANAGER.get_or_create(session_id)
    container_path = session.resolve_container_path(path)
    relative_path = _relative_session_path(session, container_path)

    if not await session.is_file(container_path):
        raise ContainerFileSystemError(f"File not found: {relative_path}")

    if insert_line < 0:
        raise ContainerFileSystemError("insert_line must be >= 0")

    content = await session.read_container_file(container_path)
    lines = content.splitlines()
    text_lines = insert_text.splitlines()

    if insert_line == 0:
        new_lines = text_lines + lines
    elif insert_line > len(lines):
        raise ContainerFileSystemError(
            f"insert_line {insert_line} is beyond the end of the file ({len(lines)} lines)"
        )
    else:
        new_lines = lines[:insert_line] + text_lines + lines[insert_line:]

    new_content = "\n".join(new_lines) + ("\n" if content.endswith("\n") else "")
    await session.write_container_file(container_path, new_content)
    return f"Successfully inserted text after line {insert_line}."


def _apply_view_range(
    lines: list[str],
    view_range: list[int] | None,
) -> tuple[list[str], int]:
    if view_range is None:
        return lines, 1

    if len(view_range) != 2:
        raise ContainerFileSystemError("view_range must contain exactly 2 integers")

    start, end = view_range
    if start < 1:
        raise ContainerFileSystemError("view_range start must be >= 1")

    total = len(lines)
    if end == -1:
        end = total
    elif end < 1:
        raise ContainerFileSystemError("view_range end must be >= 1 or -1")

    if start > end:
        raise ContainerFileSystemError(
            f"Invalid view_range: start ({start}) > end ({end})"
        )
    if start > total:
        raise ContainerFileSystemError(
            f"view_range start {start} is beyond the end of the file ({total} lines)"
        )

    return lines[start - 1 : end], start


def _format_with_line_numbers(lines: list[str], start_line: int) -> str:
    return "\n".join(
        f"{index}: {line}" for index, line in enumerate(lines, start=start_line)
    )


async def present_container_files(
    session_id: str,
    paths: list[str],
) -> tuple[str, list[dict[str, str]]]:
    """Copy container files to host output/ and return metadata records."""
    session = await CONTAINER_MANAGER.get_or_create(session_id)
    records: list[dict[str, str]] = []
    messages: list[str] = []

    for raw_path in paths:
        virtual_path = session.resolve_virtual_path(raw_path)
        container_path = session.paths.virtual_to_container_path(virtual_path)
        relative_path = virtual_path.removeprefix(
            f"{session.paths.virtual_root.rstrip('/')}/"
        )
        if not await session.is_file(container_path):
            raise ContainerFileSystemError(f"File not found: {relative_path}")

        file_name = Path(container_path).name
        host_dest = session.paths.output / file_name
        try:
            host_src = session.paths.virtual_to_host_path(virtual_path)
            if host_src.is_file():
                if host_src.resolve() != host_dest.resolve():
                    host_dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(host_src, host_dest)
            else:
                await session.docker_client.copy_from_container(
                    session.container_name,
                    container_path,
                    host_dest,
                )
        except ValueError:
            await session.docker_client.copy_from_container(
                session.container_name,
                container_path,
                host_dest,
            )
        records.append(
            {
                "file_name": file_name,
                "host_path": str(host_dest),
                "container_path": virtual_path,
            }
        )
        messages.append(f"Presented {file_name}")

    return "\n".join(messages), records
