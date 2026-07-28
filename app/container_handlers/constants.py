from pathlib import Path

# Docker sandbox
CONTAINER_NAME = "sandbox"
CONTAINER_AGENT_USER = "agent"   # agent bash execution (restricted)
CONTAINER_ROOT = "root"

# Host layout: sandbox_data/{sessions/<id>, default_skills}
_REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_DATA_ROOT = _REPO_ROOT / "sandbox_data"
SESSION_ROOT = SANDBOX_DATA_ROOT / "sessions"
DEFAULT_SKILLS_DIR = SANDBOX_DATA_ROOT / "default_skills"

# Container physical layout (bind-mounted from SANDBOX_DATA_ROOT)
CONTAINER_SANDBOX_DATA_ROOT = "/sandbox_data"
CONTAINER_SESSIONS_ROOT = f"{CONTAINER_SANDBOX_DATA_ROOT}/sessions"
CONTAINER_DEFAULT_SKILLS = f"{CONTAINER_SANDBOX_DATA_ROOT}/default_skills"

# Agent-facing virtual workspace inside bwrap
VIRTUAL_WORKSPACE = "/workspace"
VIRTUAL_SCRATCHPAD = f"{VIRTUAL_WORKSPACE}/scratchpad"

# Per-session subdirectories (under sessions/<id>/)
SESSION_SUBDIRS = ("uploads", "output", "scratchpad")

# All top-level dirs under /workspace (session dirs + global skills)
WORKSPACE_TOP_LEVELS = (*SESSION_SUBDIRS, "skills")

# Agent-writable subdirectories under each session
WRITABLE_SESSION_SUBDIRS = ("output", "scratchpad")

# Command execution
COMMAND_TIMEOUT_SEC = 90
BASH_SENTINEL = "__BASH_DONE__"

# Output limits (per Claude bash tool best practices)
OUTPUT_MAX_LINES = 300

# Commands that must never run inside the sandbox
BLOCKED_COMMANDS = frozenset(
    {
        "sudo",
        "su",
        "mount",
        "umount",
        "chroot",
        "kill",
        "pkill",
        "killall",
        "reboot",
        "shutdown",
        "poweroff",
        "halt",
        "init",
        "systemctl",
        "service",
        "iptables",
        "nsenter",
        "docker",
        "podman",
        "exec",
        "exit",
        "nohup",
        "disown",
        "setsid",
        "screen",
        "tmux",
        "at",
        "crontab"
    }
)

# Regex patterns for dangerous command constructs
BLOCKED_COMMAND_PATTERNS = (
    (r"rm\s+(-[a-zA-Z]*r[a-zA-Z]*\s+)?/", "Attempt to recursively remove files or directories from the root ('rm -rf /') is not allowed."),
    (r":\(\)\s*\{", "Potential fork bomb detected (':(){ :|:& };') -- shell function bomb not allowed."),
    (r"/dev/tcp/", "Access to /dev/tcp/ (raw TCP connections) is not allowed."),
    (r"/dev/udp/", "Access to /dev/udp/ (raw UDP connections) is not allowed."),
    (r">\s*/etc/", "Redirecting output to /etc/ (writing to system configuration) is not allowed."),
    (r">\s*/usr/", "Redirecting output to /usr/ (writing to system files) is not allowed."),
    (r">\s*/var/", "Redirecting output to /var/ (writing to system files) is not allowed."),
    (r"curl\s+.*\|\s*bash", "Piping downloaded code into bash ('curl ... | bash') is not allowed."),
    (r"wget\s+.*\|\s*bash", "Piping downloaded code into bash ('wget ... | bash') is not allowed."),
    (r"while\s+(?:true|:)\b", "Infinite while loops (e.g., 'while true') are not allowed."),
    (r"for\s*\(\(\s*;", "Infinite for loops (e.g., 'for ((;;))') are not allowed."),
    (r"\bfork\s*;", "The bash 'fork' builtin (creates fork bombs) is not allowed."),
)

# Audit logging
LOG_OUTPUT_PREVIEW_CHARS = 300

# SSE streaming: number of AI message chunks to batch before sending to the client
AI_TOKEN_BATCH_SIZE = 5
