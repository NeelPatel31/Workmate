"""Path helpers for a single sandbox session directory tree.

``sandbox_data`` is bind-mounted into the container (``./sandbox_data`` ->
``/sandbox_data``), so the host tree and the container tree are the *same*
files and differ only by prefix. Container paths are therefore derived from
host paths via :func:`host_to_container` instead of being tracked separately.
"""

import posixpath
from dataclasses import dataclass
from pathlib import Path

from .constants import (
    CONTAINER_DEFAULT_SKILLS,
    CONTAINER_SANDBOX_DATA_ROOT,
    DEFAULT_SKILLS_DIR,
    SANDBOX_DATA_ROOT,
    SESSION_ROOT,
    SESSION_SUBDIRS,
    VIRTUAL_WORKSPACE,
)


def normalize_virtual_root(virtual_root: str) -> str:
    """Ensure virtual root is an absolute POSIX path (e.g. /workspace)."""
    root = virtual_root.rstrip("/")
    if not root.startswith("/"):
        root = f"/{root}"
    return root


def host_to_container(host_path: Path) -> str:
    """Map a host path under SANDBOX_DATA_ROOT to its container path.

    The bind mount guarantees host ``SANDBOX_DATA_ROOT`` == container
    ``/sandbox_data``, so this is a pure prefix swap.

    Example:
    project_root/sandbox_data/sessions/123 -> /sandbox_data/sessions/123
    """
    rel = host_path.resolve().relative_to(SANDBOX_DATA_ROOT.resolve())
    if not rel.parts:
        return CONTAINER_SANDBOX_DATA_ROOT
    return posixpath.join(CONTAINER_SANDBOX_DATA_ROOT, *rel.parts)


@dataclass(frozen=True)
class SessionPaths:
    """Immutable view of one session's on-disk and virtual layout.

    Host:      sandbox_data/sessions/<session_id>/{uploads,output,scratchpad}
    Container: /sandbox_data/sessions/<session_id>/...
    Virtual:   /workspace/{uploads,output,scratchpad,skills}
    """

    session_id: str # session_id is the id of the session
    session_root: Path # session_root is the root of the session directory on the host
    virtual_root: str # virtual_root AKA workspace/

    @classmethod
    def create(
        cls,
        session_id: str,
        sessions_root: Path | None = None,
        virtual_root: str = VIRTUAL_WORKSPACE,
    ) -> "SessionPaths":
        root = (sessions_root or SESSION_ROOT).resolve()
        return cls(
            session_id=session_id,
            session_root=root / session_id,
            virtual_root=normalize_virtual_root(virtual_root),
        )

    # Container root is derived from the host root via the bind-mount prefix.
    @property
    def container_root(self) -> str:
        return host_to_container(self.session_root)

    # Compatibility aliases used across the codebase
    @property
    def host_root(self) -> Path:
        return self.session_root

    @property
    def uploads(self) -> Path:
        return self.session_root / "uploads"

    @property
    def output(self) -> Path:
        return self.session_root / "output"

    @property
    def scratchpad(self) -> Path:
        return self.session_root / "scratchpad"

    @property
    def subdirs(self) -> tuple[Path, ...]:
        return tuple(self.session_root / name for name in SESSION_SUBDIRS)

    @property
    def virtual_scratchpad(self) -> str:
        return f"{self.virtual_root}/scratchpad"

    @property
    def virtual_skills(self) -> str:
        return f"{self.virtual_root}/skills"

    def virtual_subdir(self, name: str) -> str:
        return f"{self.virtual_root}/{name}"

    def virtual_uploads(self) -> str:
        return f"{self.virtual_root}/uploads"

    def container_uploads(self) -> str:
        return f"{self.container_root}/uploads"

    def container_output(self) -> str:
        return f"{self.container_root}/output"

    def container_scratchpad(self) -> str:
        return f"{self.container_root}/scratchpad"

    def resolve_host_path(self, relative_path: str) -> Path:
        """Resolve a path relative to session root; raises ValueError on escape.

        Example:
        "uploads/foo.txt" -> project_root/sandbox_data/sessions/123/uploads/foo.txt
        """
        candidate = (self.session_root / relative_path).resolve()
        session_root = self.session_root.resolve()
        try:
            candidate.relative_to(session_root)
        except ValueError as exc:
            raise ValueError(
                f"Path '{relative_path}' escapes session directory"
            ) from exc
        return candidate

    def virtual_to_host_path(self, virtual_path: str) -> Path:
        """Map a sandbox path like /workspace/uploads/foo to a host Path.

        Example:
        /workspace/skills/foo.py -> project_root/sandbox_data/default_skills/foo.py
        /workspace/uploads/foo.txt -> project_root/sandbox_data/sessions/123/uploads/foo.txt
        """
        is_skills, rel = self._resolve_virtual(virtual_path)
        if is_skills:
            if not DEFAULT_SKILLS_DIR.is_dir():
                raise ValueError("Skills directory is not available in this sandbox")
            return self._resolve_global_path(DEFAULT_SKILLS_DIR, rel)
        return self.resolve_host_path(rel)

    def virtual_to_container_path(self, virtual_path: str) -> str:
        """Map /workspace/... to the physical container path for docker exec.

        Example:
        /workspace/skills/foo.py -> /sandbox_data/default_skills/foo.py
        /workspace/uploads/foo.txt -> /sandbox_data/sessions/123/uploads/foo.txt
        """
        is_skills, rel = self._resolve_virtual(virtual_path)
        if is_skills:
            return f"{CONTAINER_DEFAULT_SKILLS}/{rel}" if rel else CONTAINER_DEFAULT_SKILLS
        return f"{self.container_root}/{rel}" if rel else self.container_root

    def container_to_relative(self, container_path: str) -> str:
        """Map a physical container path to its workspace-relative path.

        Example:
        /sandbox_data/default_skills/foo.py -> skills/foo.py
        /sandbox_data/sessions/123/uploads/foo.txt -> uploads/foo.txt
        /sandbox_data/sessions/123 -> ""
        """
        normalized = posixpath.normpath(container_path.rstrip("/"))
        skills_root = CONTAINER_DEFAULT_SKILLS.rstrip("/")
        if normalized == skills_root or normalized.startswith(f"{skills_root}/"):
            rel = normalized[len(skills_root) :].lstrip("/")
            return f"skills/{rel}" if rel else "skills"

        root = self.container_root.rstrip("/")
        if normalized == root:
            return ""
        prefix = f"{root}/"
        if not normalized.startswith(prefix):
            raise ValueError(
                f"Path '{container_path}' is outside session directory"
            )
        return normalized[len(prefix) :]

    def container_to_virtual_path(self, container_path: str) -> str:
        """Map a physical container path back to the agent-facing virtual path.

        Example:
        /sandbox_data/default_skills/foo.py -> /workspace/skills/foo.py
        /sandbox_data/sessions/123/uploads/foo.txt -> /workspace/uploads/foo.txt
        """
        rel = self.container_to_relative(container_path)
        return f"{self.virtual_root}/{rel}" if rel else self.virtual_root

    def _resolve_virtual(self, virtual_path: str) -> tuple[bool, str]:
        """Split a /workspace path into (is_skills, relative).

        Example:
        /workspace/skills/foo.py -> (True, "foo.py")
        /workspace/uploads/foo.txt -> (False, "uploads/foo.txt")
        """
        rel = self._relative_under_virtual(virtual_path)
        if rel == "skills" or rel.startswith("skills/"):
            return True, rel[len("skills") :].lstrip("/")
        return False, rel

    def _relative_under_virtual(self, virtual_path: str) -> str:
        """Ensure a path is relative to the workspace root.

        Example:
        /workspace/skills/foo.py -> skills/foo.py
        /workspace/uploads/foo.txt -> uploads/foo.txt
        """
        workspace = self.virtual_root
        normalized = posixpath.normpath(virtual_path.rstrip("/"))
        if normalized != workspace and not normalized.startswith(f"{workspace}/"):
            raise ValueError(
                f"Path '{virtual_path}' is outside virtual workspace ({workspace})"
            )
        return normalized[len(workspace) :].lstrip("/")

    @staticmethod
    def _resolve_global_path(root: Path, relative_path: str) -> Path:
        candidate = (root / relative_path).resolve()
        root_resolved = root.resolve()
        try:
            candidate.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"Path '{relative_path}' escapes global directory"
            ) from exc
        return candidate
