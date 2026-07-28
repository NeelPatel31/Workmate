import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from app.container_handlers import CONTAINER_MANAGER
from app.utils.filename import normalize_upload_filename, unique_upload_name


@dataclass
class InMemoryUpload:
    """A file whose bytes have already been read out of the request."""

    filename: str
    content: bytes
    content_type: str = "application/octet-stream"


class FileRecord(TypedDict):
    file_name: str
    host_path: str
    container_path: str


async def upload_session_files(
    session_id: str,
    files: list[InMemoryUpload],
) -> list[FileRecord]:
    if not files:
        return []

    session = await CONTAINER_MANAGER.get_or_create(session_id)
    records: list[FileRecord] = []
    reserved_names: set[str] = set()

    for upload in files:
        filename = unique_upload_name(
            session.paths.uploads,
            normalize_upload_filename(upload.filename or "upload"),
            reserved=reserved_names,
        )
        reserved_names.add(filename)
        content = upload.content
        tmp_path: Path | None = None

        try:
            with tempfile.NamedTemporaryFile(delete=False) as tmp:
                tmp.write(content)
                tmp_path = Path(tmp.name)

            await session.upload(tmp_path, filename)
            host_dest = (session.paths.uploads / filename).resolve()
            if not host_dest.is_file():
                raise RuntimeError(f"Upload did not persist to host path: {host_dest}")

            records.append(
                {
                    "file_name": filename,
                    "host_path": str(host_dest),
                    "container_path": f"{session.paths.virtual_uploads()}/{filename}",
                }
            )
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    return records
