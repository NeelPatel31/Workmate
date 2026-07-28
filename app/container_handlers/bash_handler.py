import asyncio
import base64
import posixpath
import shlex
import shutil
import time
import uuid
from asyncio import StreamReader
from pathlib import Path

from .command_validator import CommandValidator
from .docker_client import DockerExecClient, ExecResult, _cap_timeout
from .errors import ContainerFileSystemError
from .session_paths import SessionPaths
from .constants import (
    BASH_SENTINEL,
    COMMAND_TIMEOUT_SEC,
    CONTAINER_AGENT_USER,
    CONTAINER_DEFAULT_SKILLS,
    CONTAINER_NAME,
    LOG_OUTPUT_PREVIEW_CHARS,
    OUTPUT_MAX_LINES,
    SESSION_ROOT,
    SESSION_SUBDIRS,
    WRITABLE_SESSION_SUBDIRS,
)
from app.utils import logger


class BashSession:
    """Stateful, isolated bash session for a single agent session."""

    def __init__(
        self,
        paths: SessionPaths,
        container_name: str = CONTAINER_NAME,
        validator: CommandValidator | None = None,
        docker_client: DockerExecClient | None = None,
        exec_user: str = CONTAINER_AGENT_USER,
    ) -> None:
        self.paths = paths
        self.container_name = container_name
        self.validator = validator or CommandValidator()
        self.docker_client = docker_client or DockerExecClient()
        self.exec_user = exec_user
        self._lock = asyncio.Lock()
        self._process: asyncio.subprocess.Process | None = None
        self._output_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._reader_tasks: list[asyncio.Task] = []
        self._active_exec_user = exec_user
        self._current_cwd = paths.virtual_scratchpad

    def validate_command(self, command: str) -> tuple[bool, str | None]:
        return self.validator.validate(
            command,
            current_cwd=self._current_cwd,
            virtual_root=self.paths.virtual_root,
        )

    async def execute(
        self, command: str, timeout: int | None = COMMAND_TIMEOUT_SEC
    ) -> tuple[str, bool]:
        ok, reason = self.validate_command(command)
        if not ok:
            return f"Error: {reason}", True

        timeout_sec = _cap_timeout(timeout)

        async with self._lock:
            await self._ensure_started(self.exec_user)
            await self._drain_queue()
            wrapped = self._wrap_command(command)
            await self.docker_client.write_stdin(self._process, wrapped)
            output, timed_out, cwd = await self._read_until_sentinel(timeout_sec)
            if timed_out:
                logger.warning(
                    "Session %s command timed out: %s",
                    self.paths.session_id,
                    command[:200],
                )
                return f"Error: Command timed out after {timeout_sec} seconds", True

            if cwd is not None and not self._cwd_within_session(cwd):
                logger.warning(
                    "Session %s cwd escaped boundary: %s",
                    self.paths.session_id,
                    cwd,
                )
                await self._stop()
                await self._ensure_started(self._active_exec_user)
                return (
                    f"Error: Working directory escaped session boundary ({cwd})",
                    True,
                )

            if cwd is not None:
                self._current_cwd = cwd

            logger.info(
                "Session %s executed: %s | output preview: %s",
                self.paths.session_id,
                command[:200],
                output[:LOG_OUTPUT_PREVIEW_CHARS],
            )
            return output, False

    async def reset(self, user: str | None = None) -> str:
        async with self._lock:
            await self._stop()
            self._current_cwd = self.paths.virtual_scratchpad
            await self._ensure_started(user or self.exec_user)
            return "Bash session restarted"

    async def upload(self, host_src: Path, dest_name: str) -> str:
        if not host_src.is_file():
            raise FileNotFoundError(f"Source file not found: {host_src}")
        if ".." in Path(dest_name).parts or Path(dest_name).is_absolute():
            raise ValueError("Invalid destination filename")

        host_dest = self.paths.uploads / dest_name
        host_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host_src, host_dest)

        host_dest.chmod(0o665)

        # The bind mount makes the host file immediately visible in the
        # container; the agent reads it through the uploads dir's r-x bits.
        return f"Uploaded to {host_dest}"

    async def download(self, relative_path: str) -> bytes:
        path = self.paths.resolve_host_path(relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {relative_path}")
        return path.read_bytes()

    async def close(self) -> None:
        async with self._lock:
            await self._stop()

    # ── Container filesystem operations (raw docker exec as agent user) ──

    def resolve_virtual_path(self, raw_path: str) -> str:
        """Resolve an agent path to a virtual /workspace/... path."""
        within, resolved = CommandValidator.resolve_container_path(
            raw_path,
            self._current_cwd,
            self.paths.virtual_root,
        )
        if not within:
            raise ContainerFileSystemError(
                f"Path '{raw_path}' escapes workspace directory"
            )
        return resolved

    def resolve_container_path(self, raw_path: str) -> str:
        """Resolve an agent path to the physical container path for docker exec."""
        virtual_path = self.resolve_virtual_path(raw_path)
        try:
            return self.paths.virtual_to_container_path(virtual_path)
        except ValueError as exc:
            raise ContainerFileSystemError(str(exc)) from exc

    async def path_exists(self, container_path: str) -> bool:
        result = await self._run_file_exec(
            f"test -e {shlex.quote(container_path)}"
        )
        return result.returncode == 0

    async def is_directory(self, container_path: str) -> bool:
        result = await self._run_file_exec(
            f"test -d {shlex.quote(container_path)}"
        )
        return result.returncode == 0

    async def is_file(self, container_path: str) -> bool:
        result = await self._run_file_exec(
            f"test -f {shlex.quote(container_path)}"
        )
        return result.returncode == 0

    async def read_container_file(self, container_path: str) -> str:
        result = await self._run_file_exec(f"cat {shlex.quote(container_path)}")
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ContainerFileSystemError(
                detail or f"Failed to read file: {container_path}"
            )
        return result.stdout

    async def list_directory(self, container_path: str) -> str:
        result = await self._run_file_exec(
            f"ls -la {shlex.quote(container_path)}"
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ContainerFileSystemError(
                detail or f"Failed to list directory: {container_path}"
            )
        return result.stdout.rstrip("\n")

    def validate_writable_path(self, container_path: str) -> None:
        relative = self._container_path_to_relative(container_path)
        if not relative:
            raise ContainerFileSystemError("Cannot write to session root")
        top_level = relative.split("/")[0]
        if top_level not in WRITABLE_SESSION_SUBDIRS:
            dirs = ", ".join(WRITABLE_SESSION_SUBDIRS)
            raise ContainerFileSystemError(
                f"Path '{container_path}' is not in a writable directory. "
                f"Writable directories: {dirs}"
            )

    async def create_container_file(self, container_path: str, content: str) -> None:
        self.validate_writable_path(container_path)
        if await self.path_exists(container_path):
            if await self.is_directory(container_path):
                raise ContainerFileSystemError(
                    f"Path is a directory: {container_path}"
                )
            raise ContainerFileSystemError(
                f"File already exists: {container_path}"
            )

        await self._write_container_content(container_path, content, mkdir_parents=True)

    async def write_container_file(self, container_path: str, content: str) -> None:
        self.validate_writable_path(container_path)
        if not await self.is_file(container_path):
            raise ContainerFileSystemError(f"File not found: {container_path}")

        await self._write_container_content(container_path, content)

    async def _run_file_exec(self, script: str) -> ExecResult:
        try:
            return await self.docker_client.run_exec(
                self.container_name,
                script,
                self.exec_user,
            )
        except asyncio.TimeoutError as exc:
            raise ContainerFileSystemError(
                f"Operation timed out after {COMMAND_TIMEOUT_SEC} seconds"
            ) from exc

    def _container_path_to_relative(self, container_path: str) -> str:
        try:
            return self.paths.container_to_relative(container_path)
        except ValueError as exc:
            raise ContainerFileSystemError(str(exc)) from exc

    async def _write_container_content(
        self,
        container_path: str,
        content: str,
        *,
        mkdir_parents: bool = False,
    ) -> None:
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        commands: list[str] = []
        if mkdir_parents:
            parent = posixpath.dirname(container_path)
            if parent and parent != "/":
                commands.append(f"mkdir -p {shlex.quote(parent)}")
        commands.append(
            f"echo {shlex.quote(encoded)} | base64 -d > {shlex.quote(container_path)}"
        )
        result = await self._run_file_exec(" && ".join(commands))
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise ContainerFileSystemError(
                detail or f"Failed to write file: {container_path}"
            )

    def _workspace_bwrap_args(self) -> list[str]:
        """Bind physical session/skills dirs onto the stable /workspace tree."""
        args: list[str] = ["--dir", self.paths.virtual_root]
        readonly = {"uploads"}

        for name in sorted(SESSION_SUBDIRS):
            physical = f"{self.paths.container_root}/{name}"
            virtual = self.paths.virtual_subdir(name)
            bind_flag = "--ro-bind" if name in readonly else "--bind"
            args.extend([bind_flag, physical, virtual])

        args.extend(
            [
                "--ro-bind",
                CONTAINER_DEFAULT_SKILLS,
                self.paths.virtual_skills,
            ]
        )
        return args

    def _build_bwrap_argv(self) -> list[str]:
        tmpfs_size = str(512 * 1024 * 1024)
        return [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/etc", "/etc",
            "--dev", "/dev",
            "--proc", "/proc",
            "--size", tmpfs_size, "--tmpfs", "/tmp",
            *self._workspace_bwrap_args(),
            "--chdir", self.paths.virtual_scratchpad,
            "--new-session",
            # Clean environment
            "--clearenv",
            "--setenv", "HOME", self.paths.virtual_scratchpad,
            "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
            "--setenv", "TMPDIR", "/tmp",
            "--setenv", "TERM", "xterm-256color",
            "--setenv", "LANG", "en_US.UTF-8",
            "bash",
            "--noprofile",
            "--norc",
        ]

    async def _ensure_started(self, user: str | None = None) -> None:
        exec_user = user or self.exec_user
        if (
            self._process is not None
            and self._process.returncode is None
            and self._active_exec_user == exec_user
        ):
            return
        if self._process is not None:
            await self._stop()

        self._active_exec_user = exec_user
        self._current_cwd = self.paths.virtual_scratchpad
        self._output_queue = asyncio.Queue()
        self._process = await self.docker_client.spawn(
            self.container_name, self._build_bwrap_argv(), user=exec_user
        )
        self._start_readers()
        await asyncio.sleep(0.2)
        if self._process.returncode is not None:
            errors = await self._collect_recent_stderr()
            raise RuntimeError(
                f"Bash session failed to start: {errors or 'unknown error'}"
            )
        await self._init_shell_umask()

    async def _init_shell_umask(self) -> None:
        """Group-writable files so dev can manage agent-created artifacts."""
        await self.docker_client.write_stdin(self._process, "umask 0002\n")
        await asyncio.sleep(0.05)
        await self._drain_queue()

    async def _collect_recent_stderr(self) -> str:
        lines: list[str] = []
        while True:
            try:
                label, line = self._output_queue.get_nowait()
                if label == "stderr":
                    lines.append(line.rstrip("\n"))
            except asyncio.QueueEmpty:
                break
        return "\n".join(lines)

    def _start_readers(self) -> None:
        self._reader_tasks = []
        if self._process is None:
            return
        for stream, label in (
            (self._process.stdout, "stdout"),
            (self._process.stderr, "stderr"),
        ):
            if stream is None:
                continue
            task = asyncio.create_task(self._read_stream(stream, label))
            self._reader_tasks.append(task)

    async def _read_stream(self, stream: StreamReader, label: str) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            await self._output_queue.put((label, line.decode(errors="replace")))

    async def _stop(self) -> None:
        for task in self._reader_tasks:
            task.cancel()
        for task in self._reader_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reader_tasks = []

        if self._process is not None:
            await self.docker_client.terminate(self._process)
            self._process = None
        await self._drain_queue()

    async def _drain_queue(self) -> None:
        while True:
            try:
                self._output_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def _wrap_command(self, command: str) -> str:
        # Wrap in a command group so </dev/null applies after the full command.
        # Appending </dev/null to the last line breaks heredoc terminators (e.g. PY).
        return (
            f"{{\n{command.rstrip()}\n}} </dev/null\n"
            f"printf '{BASH_SENTINEL}:%s:%s\\n' $? \"$PWD\"\n"
        )

    def _cwd_within_session(self, cwd: str) -> bool:
        root = self.paths.virtual_root.rstrip("/")
        normalized = cwd.rstrip("/")
        return normalized == root or normalized.startswith(f"{root}/")

    def _parse_sentinel_line(self, line: str) -> tuple[str | None, str | None]:
        if BASH_SENTINEL not in line:
            return None, None
        before, _, after = line.partition(BASH_SENTINEL)
        cwd = None
        if after.startswith(":"):
            parts = after[1:].split(":", 1)
            if len(parts) == 2:
                cwd = parts[1]
        output_before = before.rstrip() if before else None
        return output_before, cwd

    async def _read_until_sentinel(
        self, timeout: float
    ) -> tuple[str, bool, str | None]:
        stdout_lines: list[str] = []
        stderr_lines: list[str] = []
        cwd: str | None = None
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                label, line = await asyncio.wait_for(
                    self._output_queue.get(), timeout=min(remaining, 0.1)
                )
            except asyncio.TimeoutError:
                continue

            stripped = line.rstrip("\n")
            if BASH_SENTINEL in stripped:
                before, parsed_cwd = self._parse_sentinel_line(stripped)
                cwd = parsed_cwd
                if before:
                    if label == "stdout":
                        stdout_lines.append(before)
                    else:
                        stderr_lines.append(before)
                break

            if label == "stdout":
                stdout_lines.append(stripped)
            else:
                stderr_lines.append(stripped)
        else:
            await self._stop()
            await self._ensure_started(self._active_exec_user)
            return "", True, None

        return self._beautify_output(stdout_lines, stderr_lines), False, cwd

    def _beautify_output(self, stdout_lines: list[str], stderr_lines: list[str]) -> str:
        parts: list[str] = []
        template = "====================== {label} ======================\n\n{content}\n\n"
        if stdout_lines:
            content = self._truncate_lines_head(stdout_lines)
            parts.append(template.format(label="stdout", content=content))
        if stderr_lines:
            content = self._truncate_lines_tail(stderr_lines)
            parts.append(template.format(label="stderr", content=content))
        return "\n".join(parts)

    def _truncate_lines_head(self, lines: list[str]) -> str:
        if len(lines) <= OUTPUT_MAX_LINES:
            return "\n".join(lines)
        omitted = len(lines) - OUTPUT_MAX_LINES
        truncated = "\n".join(lines[:OUTPUT_MAX_LINES])
        return (
            f"{truncated}\n\n"
            f"... Output truncated ({omitted} lines omitted, {len(lines)} total lines) ..."
        )

    def _truncate_lines_tail(self, lines: list[str]) -> str:
        if len(lines) <= OUTPUT_MAX_LINES:
            return "\n".join(lines)
        omitted = len(lines) - OUTPUT_MAX_LINES
        truncated = "\n".join(lines[-OUTPUT_MAX_LINES:])
        return (
            f"... Output truncated ({omitted} lines omitted, {len(lines)} total lines) ...\n\n"
            f"{truncated}"
        )


class BashSessionsManager:
    """Manages session folders and BashSession instances."""

    def __init__(
        self,
        host_sessions_root: Path | None = None,
        container_name: str = CONTAINER_NAME,
    ) -> None:
        self.host_sessions_root = host_sessions_root or SESSION_ROOT
        self.container_name = container_name
        self._sessions: dict[str, BashSession] = {}
        self._lock = asyncio.Lock()
        self._validator = CommandValidator()
        self._docker_client = DockerExecClient()

    async def create_session(
        self,
        session_id: str | None = None,
        exec_user: str = CONTAINER_AGENT_USER,
    ) -> str:
        sid = session_id or str(uuid.uuid4())
        async with self._lock:
            if sid in self._sessions:
                raise ValueError(f"Session already exists: {sid}")

            paths = SessionPaths.create(sid, self.host_sessions_root)
            await self._provision_folders(paths)
            self._sessions[sid] = BashSession(
                paths,
                self.container_name,
                self._validator,
                self._docker_client,
                exec_user=exec_user,
            )
            return sid

    async def get_session(self, session_id: str) -> BashSession:
        async with self._lock:
            if session_id not in self._sessions:
                raise KeyError(f"Session not found: {session_id}")
            return self._sessions[session_id]

    async def get_or_create(self, session_id: str) -> BashSession:
        async with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]
        await self.create_session(session_id)
        return await self.get_session(session_id)

    async def destroy_session(self, session_id: str, remove_files: bool = False) -> None:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
            if session is None:
                raise KeyError(f"Session not found: {session_id}")
        await session.close()
        if remove_files:
            shutil.rmtree(session.paths.session_root, ignore_errors=True)
            await self._docker_client.remove_session(
                self.container_name, session.paths.container_root
            )

    async def bash_tool_helper(
        self,
        session_id: str,
        command: str | None = None,
        timeout: int | None = None,
        restart: bool = False,
    ) -> tuple[str, bool]:
        session = await self.get_or_create(session_id)

        if restart:
            message = await session.reset()
            return message, False

        if not command:
            return "Error: Missing 'command' in tool input", True

        return await session.execute(command, timeout=timeout)

    async def _provision_folders(self, paths: SessionPaths) -> None:
        self.host_sessions_root.mkdir(parents=True, exist_ok=True)
        paths.session_root.mkdir(parents=True, exist_ok=True)
        for subdir in paths.subdirs:
            subdir.mkdir(parents=True, exist_ok=True)
        await self._docker_client.provision_session(
            self.container_name, paths.container_root
        )
