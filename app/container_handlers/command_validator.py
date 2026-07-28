import posixpath
import re
import shlex
from pathlib import Path

from .constants import (
    BLOCKED_COMMANDS,
    BLOCKED_COMMAND_PATTERNS,
    VIRTUAL_SCRATCHPAD,
    VIRTUAL_WORKSPACE,
    WORKSPACE_TOP_LEVELS,
)

from app.utils import logger


class CommandValidator:
    """Validates bash commands before they are sent to a session shell."""

    _ABSOLUTE_PATH_RE = re.compile(r"/(?:[\w.\-]+/)*[\w.\-]+")
    _STRING_LITERAL_RE = re.compile(r"""['"]([^'"]+)['"]""")

    def validate(
        self,
        command: str,
        current_cwd: str | None = None,
        virtual_root: str = VIRTUAL_WORKSPACE,
    ) -> tuple[bool, str | None]:
        command = command.strip()
        if not command:
            return False, "Empty command"

        cwd = current_cwd or VIRTUAL_SCRATCHPAD

        blocked_reason = self._check_blocked_commands(command)
        if blocked_reason:
            return False, blocked_reason

        pattern_reason = self._check_blocked_patterns(command)
        if pattern_reason:
            return False, pattern_reason

        path_reason = self._check_paths(command, cwd, virtual_root)
        if path_reason:
            return False, path_reason

        cd_reason = self._check_cd(command, cwd, virtual_root)
        if cd_reason:
            return False, cd_reason

        return True, None

    @staticmethod
    def normalize_session_virtual_path(
        raw_path: str,
        virtual_root: str = VIRTUAL_WORKSPACE,
    ) -> str:
        """Map agent-facing shorthand like /uploads/foo to /workspace/uploads/foo."""
        if not raw_path.startswith("/"):
            top_level = raw_path.split("/", 1)[0]
            if top_level in WORKSPACE_TOP_LEVELS:
                return posixpath.normpath(f"{virtual_root.rstrip('/')}/{raw_path}")
            return raw_path

        stripped = raw_path.lstrip("/")
        if not stripped:
            return raw_path

        top_level, _, _remainder = stripped.partition("/")
        root = virtual_root.rstrip("/")
        if top_level == root.lstrip("/"):
            return posixpath.normpath(raw_path)
        if top_level in WORKSPACE_TOP_LEVELS:
            return posixpath.normpath(f"{root}/{stripped}")
        return posixpath.normpath(raw_path)

    @staticmethod
    def resolve_virtual_path(raw_path: str, cwd: str) -> str:
        """Resolve a path against virtual cwd; return normalized virtual path."""
        normalized = CommandValidator.normalize_session_virtual_path(raw_path)
        if normalized.startswith("/"):
            return posixpath.normpath(normalized)
        return posixpath.normpath(posixpath.join(cwd, normalized))

    @staticmethod
    def within_workspace(resolved: str, virtual_root: str = VIRTUAL_WORKSPACE) -> bool:
        root = virtual_root.rstrip("/")
        normalized = posixpath.normpath(resolved).rstrip("/")
        return normalized == root or normalized.startswith(f"{root}/")

    @staticmethod
    def resolve_container_path(
        raw_path: str,
        cwd: str,
        virtual_root: str = VIRTUAL_WORKSPACE,
    ) -> tuple[bool, str]:
        """Resolve a path against cwd; return (within_workspace, resolved_virtual)."""
        resolved = CommandValidator.resolve_virtual_path(raw_path, cwd)
        return CommandValidator.within_workspace(resolved, virtual_root), resolved

    def _check_blocked_commands(self, command: str) -> str | None:
        try:
            tokens = shlex.split(command)
        except ValueError:
            return "Could not parse command"

        if not tokens:
            return "Empty command"

        for token in tokens:
            base = Path(token).name
            if base in BLOCKED_COMMANDS:
                return f"Command '{base}' is blocked"
        return None

    def _check_blocked_patterns(self, command: str) -> str | None:
        for pattern, reason in BLOCKED_COMMAND_PATTERNS:
            if re.search(pattern, command):
                logger.warning(f"Command matches blocked pattern: {pattern}")
                return reason
        return None

    def _extract_absolute_paths(self, command: str) -> list[str]:
        token_start_chars = set(" \t\n;|&=:(,")
        paths: list[str] = []
        for match in self._ABSOLUTE_PATH_RE.finditer(command):
            start = match.start()
            if start > 0:
                prev = command[start - 1]
                if prev == "." or prev not in token_start_chars:
                    continue
            paths.append(match.group())
        return paths

    def _check_paths(
        self,
        command: str,
        current_cwd: str,
        virtual_root: str,
    ) -> str | None:
        paths_to_check: list[str] = []

        for match in self._extract_absolute_paths(command):
            paths_to_check.append(match)

        for match in self._STRING_LITERAL_RE.findall(command):
            if match.startswith("/"):
                paths_to_check.append(match)
            elif "/" in match or match.startswith("."):
                paths_to_check.append(match)

        for raw_path in paths_to_check:
            reason = self._validate_single_path(raw_path, current_cwd, virtual_root)
            if reason:
                return reason

        return None

    def _check_cd(
        self,
        command: str,
        current_cwd: str,
        virtual_root: str,
    ) -> str | None:
        for segment in re.split(r"\s*;\s*|\s*&&\s*|\s*\|\|\s*", command):
            segment = segment.strip()
            if not segment:
                continue
            try:
                tokens = shlex.split(segment)
            except ValueError:
                continue
            if not tokens or tokens[0] != "cd":
                continue
            if len(tokens) == 1:
                return "cd without argument is not allowed in session shell"
            reason = self._validate_cd_target(tokens[1], current_cwd, virtual_root)
            if reason:
                return reason
        return None

    def _validate_cd_target(
        self,
        raw_path: str,
        current_cwd: str,
        virtual_root: str,
    ) -> str | None:
        within, _ = self.resolve_container_path(raw_path, current_cwd, virtual_root)
        if not within:
            return f"cd to '{raw_path}' escapes workspace directory"
        return None

    def _validate_single_path(
        self,
        raw_path: str,
        current_cwd: str,
        virtual_root: str,
    ) -> str | None:
        if raw_path.startswith("/"):
            effective = self.normalize_session_virtual_path(raw_path, virtual_root)
            if self.within_workspace(effective, virtual_root):
                return None
            return f"Path '{raw_path}' is outside workspace directory"

        resolved = self.resolve_virtual_path(raw_path, current_cwd)
        if not self.within_workspace(resolved, virtual_root):
            return f"Path '{raw_path}' escapes workspace directory"
        return None
