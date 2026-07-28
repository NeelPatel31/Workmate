import asyncio
from dataclasses import dataclass
from pathlib import Path

from .constants import CONTAINER_AGENT_USER, CONTAINER_ROOT, COMMAND_TIMEOUT_SEC
from app.utils import logger


def _cap_timeout(timeout: int | None) -> int:
    if timeout is None:
        return COMMAND_TIMEOUT_SEC
    return min(int(timeout), COMMAND_TIMEOUT_SEC)


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str


class DockerExecClient:
    """Async wrapper for Docker container I/O and process execution."""

    async def spawn(
        self,
        container: str,
        argv: list[str],
        user: str = CONTAINER_AGENT_USER,
    ) -> asyncio.subprocess.Process:
        cmd = ["docker", "exec", "-i", "-u", user, container, *argv]
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

    async def provision_session(self, container: str, container_path: str) -> None:
        """Apply ownership and mode bits as root.

        The session directories themselves are created on the host by the
        manager; the bind mount makes them visible here, so this only needs to
        fix ownership/mode so the agent can read uploads and write output.
        """
        permission_script = (
            f"chmod -R 775 {container_path} && "
            f"chmod -R 775 {container_path}/uploads && "
            f"chown {CONTAINER_AGENT_USER}:{CONTAINER_AGENT_USER} {container_path}/output && chmod -R 777 {container_path}/output && "
            f"chown {CONTAINER_AGENT_USER}:{CONTAINER_AGENT_USER} {container_path}/scratchpad && chmod -R 777 {container_path}/scratchpad"
        )
        await self._run_exec(container, permission_script, CONTAINER_ROOT, f"permission {container_path}")
        logger.info(f"Provisioned {container_path} in {container}")

    async def run_exec(
        self,
        container: str,
        script: str,
        user: str = CONTAINER_AGENT_USER,
        timeout: int | None = None,
    ) -> ExecResult:
        """Run a one-off command in the container and return the result."""
        timeout_sec = _cap_timeout(timeout)
        process = await asyncio.create_subprocess_exec(
            "docker",
            "exec",
            "-u",
            user,
            container,
            "sh",
            "-c",
            script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )
        except asyncio.TimeoutError:
            await self.terminate(process)
            raise
        return ExecResult(
            returncode=process.returncode or 0,
            stdout=stdout_bytes.decode(),
            stderr=stderr_bytes.decode(),
        )

    async def _run_exec(self, container: str, script: str, user: str, action: str) -> None:
        result = await self.run_exec(container, script, user)
        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to {action} in {container} as {user}: {result.stderr.strip()}"
            )

    async def remove_session(self, container: str, container_path: str) -> None:
        script = f"rm -rf {container_path}"
        await self._run_exec(container, script, CONTAINER_ROOT, f"remove {container_path}")

    async def copy_from_container(
        self, container: str, container_path: str, host_path: Path
    ) -> None:
        """Download a file via dev-readable paths."""
        src = f"{container}:{container_path}"
        host_path.parent.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            "docker",
            "cp",
            src,
            str(host_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=_cap_timeout(None),
            )
        except asyncio.TimeoutError:
            await self.terminate(process)
            raise RuntimeError(f"Timed out copying {src} to {host_path}") from None
        if (process.returncode or 0) != 0:
            raise RuntimeError(
                f"Failed to copy {src} to {host_path}: {stderr_bytes.decode().strip()}"
            )

    @staticmethod
    async def write_stdin(process: asyncio.subprocess.Process, data: str) -> None:
        if process.stdin is None:
            raise RuntimeError("Process stdin is not available")
        process.stdin.write(data.encode())
        await process.stdin.drain()

    @staticmethod
    async def terminate(
        process: asyncio.subprocess.Process, grace_sec: float = 5.0
    ) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=grace_sec)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
