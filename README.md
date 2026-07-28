# Workmate AI

Workmate AI is a Claude-like agent runtime for working with files in a sandboxed environment. It combines a chat interface, a LangGraph agent, a Docker-backed filesystem, tool execution, skill discovery, and artifact delivery into one local developer project.

The project explores how modern file-working agents can upload documents, inspect or transform them, generate new artifacts, and render visual explanations while keeping execution isolated from the host machine.

![Workmate AI architecture](images/architecture.png)

## Core Capabilities

- Chat with an agent that can reason over uploaded files and generate new outputs.
- Execute bash commands and Python scripts inside a Docker sandbox with per-session isolation.
- Work with plain-text files such as Markdown, CSV, JSON, HTML, YAML, Python, and logs.
- Process document and data formats such as PDF, DOCX, PPTX, XLSX, and images using container-installed libraries.
- Discover and use filesystem-based skills from `sandbox_data/default_skills`.
- Stream model output, tool calls, generated files, and widgets through Redis-backed Server-Sent Events.
- Cancel in-flight agent runs and track thread status per session.
- Render self-contained HTML visualizations inside the chat UI.
- Delegate visualization work to a specialized sub-agent.
- Track multi-step work with built-in TODO tools.

## Architecture

Workmate has four main layers:

- **Frontend:** a Streamlit chat UI for file uploads, streaming responses, session files, and rendered widgets.
- **Backend:** a FastAPI service that accepts messages, manages sessions, serves file downloads, and streams agent events.
- **Event bus:** Redis stores stream tokens, prompt registration, and thread status so the API can stream results independently of the agent worker.
- **Agent runtime:** a LangGraph/LangChain agent with tools for file operations, bash execution, skills, TODOs, sub-agent delegation, and HTML widget rendering.

The Docker sandbox provides the agent's working filesystem. Host data under `sandbox_data` is bind-mounted into the container at `/sandbox_data`. Each session is isolated under `sessions/<session_id>/`, and the agent sees a virtual workspace at `/workspace`.

| Virtual path | Host / container path | Purpose |
| --- | --- | --- |
| `/workspace/uploads` | `sandbox_data/sessions/<id>/uploads` | Files uploaded by the user |
| `/workspace/scratchpad` | `sandbox_data/sessions/<id>/scratchpad` | Temporary working directory |
| `/workspace/output` | `sandbox_data/sessions/<id>/output` | Final artifacts for the user |
| `/workspace/skills` | `sandbox_data/default_skills` | Mounted skill library |

## Tech Stack

- **Python 3.12**
- **FastAPI**
- **Streamlit**
- **LangChain / LangGraph**
- **Azure OpenAI**
- **Redis**
- **Docker Compose**
- **uv**
- **LibreOffice and Python document/data libraries** inside the sandbox

## Project Structure

```text
.
|-- app/
|   |-- apis/                 # FastAPI routes and controllers
|   |-- agent_registry/       # Agent, tools, prompts, middleware, sub-agents
|   |-- container_handlers/   # Docker sandbox client, bash session, path helpers
|   |-- config/               # Settings loaded from environment
|   |-- utils/                # Logging, Redis helpers, filename utils
|   `-- validation_models/    # Request/response models
|-- sandbox_data/
|   |-- default_skills/       # Bundled agent skills (pdf, docx, pptx, xlsx)
|   `-- sessions/             # Per-session uploads, scratchpad, and output
|-- streamlit_app/
|   |-- app.py                # Streamlit entry point
|   |-- api_client.py         # Backend API client
|   `-- render.py             # Chat and widget rendering
|-- images/                   # README assets
|-- docker-compose.yml        # Redis, sandbox, backend, frontend
|-- Dockerfile                # Backend/frontend app image
|-- main.py                   # FastAPI entry point
|-- pyproject.toml
|-- uv.lock
`-- README.md
```

## Setup

### Prerequisites

- Python 3.12+
- Docker Desktop or Docker Engine with Docker Compose
- uv
- Azure OpenAI credentials

### Configure Environment

Copy the example environment file:

```bash
cp .env.example .env
```

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Update `.env` with your Azure OpenAI configuration.

Required variables:

| Variable | Description |
| --- | --- |
| `APP_HOST` | Backend host, for example `0.0.0.0` |
| `APP_PORT` | Backend port, for example `5001` |
| `AZURE_OPENAI_API_KEY` | Azure OpenAI API key |
| `AZURE_OPENAI_API_ENDPOINT` | Azure OpenAI endpoint |
| `AZURE_OPENAI_API_VERSION` | Azure OpenAI API version |
| `AZURE_OPENAI_MODEL_NAME` | Azure OpenAI deployment/model name |

Optional variables:

| Variable | Description |
| --- | --- |
| `REDIS_HOST` | Redis host (`localhost` for local runs, `redis` inside Compose) |
| `REDIS_PORT` | Redis port (`6379` inside Compose; use `6380` when the backend runs on the host against Compose Redis) |
| `REDIS_DB` | Redis database index |
| `LANGSMITH_TRACING` | Enable LangSmith tracing (`true` / `false`) |
| `LANGSMITH_ENDPOINT` | LangSmith API endpoint |
| `LANGSMITH_API_KEY` | LangSmith API key |
| `LANGSMITH_PROJECT` | LangSmith project name |
| `API_BASE` | Streamlit backend URL override |

### Option A: Run Everything with Docker Compose

```bash
docker compose up --build
```

This starts:

| Service | URL / port |
| --- | --- |
| Frontend (Streamlit) | http://localhost:8501 |
| Backend (FastAPI) | http://localhost:5001 |
| Redis | localhost:6380 → container 6379 |
| Sandbox | Docker container named `sandbox` |

API docs: http://localhost:5001/docs

### Option B: Local Development

Start Redis and the sandbox:

```bash
docker compose up redis sandbox --build -d
```

Install Python dependencies:

```bash
uv sync
```

If the backend runs on the host against Compose Redis, set:

```bash
REDIS_HOST=localhost
REDIS_PORT=6380
```

Start the backend:

```bash
uv run python main.py
```

Start the frontend:

```bash
uv run streamlit run streamlit_app/app.py
```

Streamlit usually opens at http://localhost:8501. The API runs at http://localhost:5001 by default.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| `GET` | `/health` | Health check for Redis and LLM connectivity |
| `POST` | `/send-message` | Accept a user message and optional files; returns a `prompt_id` |
| `POST` | `/stream-events` | Stream agent events for a `prompt_id` as Server-Sent Events |
| `GET` | `/thread-status/{session_id}` | Get the current generation status for a session |
| `POST` | `/thread-cancel/{session_id}` | Cancel an in-flight agent run |
| `GET` | `/files/download` | Download a session file from `uploads/` or `output/` |

Typical chat flow:

1. `POST /send-message` with `session_id`, `user_query`, and optional files.
2. Receive `{ "prompt_id": "..." }`.
3. `POST /stream-events` with `session_id` and `prompt_id` to consume SSE events.
4. Optionally cancel with `POST /thread-cancel/{session_id}`.

FastAPI docs are available at:

```text
http://localhost:5001/docs
```

## Skills

Skills are stored in `sandbox_data/default_skills` and exposed to the agent at `/workspace/skills`.

Each skill is a directory with a `SKILL.md` file and optional supporting docs or scripts. The backend scans skill metadata from `SKILL.md` frontmatter and injects the available skills into the agent context through middleware.

Bundled skills:

- `pdf`
- `docx`
- `pptx`
- `xlsx`

## Current Limitations

- **Single shared Docker sandbox:** execution still happens in one sandbox container. Sessions are isolated at the filesystem and bash-session layer, but they share the same container runtime.
- **In-memory graph checkpoints:** conversation checkpoints use an in-memory saver, so agent state is not durable across backend restarts.
- **No resource quotas per user/session:** CPU, memory, execution time, file size, and storage quotas are not yet enforced per session.
- **Restricted dependency and network access:** installing new libraries at runtime and making arbitrary network connections are intentionally blocked or constrained to keep the sandbox safer.

## Future Enhancements

Workmate AI is designed to be extensible through tools. The following additions would significantly improve the agent's ability to gather information, interact with users, and produce higher-quality outputs.

### 1. `feb_search`

Search the web for relevant information, documentation, articles, or references.

**Why it's useful**

- Enables the agent to gather up-to-date information beyond its training data.
- Helps answer questions that require recent knowledge.
- Supports research-oriented workflows and fact gathering.
- Can be used as a retrieval source before generating reports or documents.

**Example use cases**

- Researching a technology before generating a summary.
- Finding documentation for a library or framework.
- Gathering sources for a report or presentation.
- Looking up current events, company information, or product details.

**Inputs**

| Argument | Type | Description |
|----------|------|-------------|
| `query` | string | Search query |

---

### 2. `feb_fetch`

Fetch and extract content from a specific URL.

**Why it's useful**

- Allows the agent to inspect web pages directly.
- Supports extracting documentation, articles, PDFs, and structured content.
- Enables deeper analysis after discovering URLs through `feb_search`.

**Example use cases**

- Reading a documentation page before writing code.
- Extracting content from a blog post.
- Summarizing a research article.
- Processing publicly available PDFs.

**Inputs**

| Argument | Type | Description |
|----------|------|-------------|
| `url` | string | URL to fetch |
| `allowed_domains` | string[] | Optional allowlist |
| `blocked_domains` | string[] | Optional blocklist |
| `text_content_token_limit` | integer | Maximum extracted content size |
| `pdf_extract_text` | boolean | Extract text from PDFs |

---

### 3. `ask_user_input`

Collect structured user preferences before proceeding with a task, inspired from Claude, Cursor & Antigravity. It uses the Human-in-the-loop concept.

Instead of making assumptions, the agent can pause and request additional information through selectable options.

**Why it's useful**

- Improves recommendation quality.
- Reduces ambiguity.
- Creates more interactive workflows.
- Helps gather constraints before generating outputs.

**Example use cases**

- Understanding user priorities before making recommendations.
- Collecting requirements for a project.
- Gathering preferences before generating documents or plans.
- Clarifying workflow choices.

**Supported interaction types**

- `single_select`
- `multi_select`
- `rank_priorities`

**Example**

```json
{
  "question": "What matters most?",
  "options": [
    "Speed",
    "Accuracy",
    "Cost"
  ],
  "type": "rank_priorities"
}
```

---

### 4. `message_compose`

Generate polished, goal-oriented communications.

The tool can create multiple communication variants optimized for different outcomes while maintaining the same intent.

**Why it's useful**

- Produces professional messages quickly.
- Generates multiple communication styles.
- Supports business, customer, and internal communication workflows.
- Helps users choose the most appropriate tone.

**Supported formats**

- Email
- Text Message
- Other custom message formats

**Example use cases**

- Job application emails.
- Follow-up messages.
- Customer communication.
- Internal team updates.
- Meeting requests.
- Professional outreach.

**Inputs**

| Argument | Type | Description |
|----------|------|-------------|
| `kind` | string | Email, text message, or other |
| `summary_title` | string | Short description of intent |
| `content` | string | Message content |

**Output**

Multiple message variants, each containing:

- Goal-oriented label
- Message body
- Optional email subject

## Contributing

Contributions are welcome, especially focused improvements to the runtime, sandboxing model, skills, file handling, tests, and UI.

Suggested workflow:

1. Fork the repository.
2. Create a focused feature branch.
3. Make the change.
4. Run the backend, frontend, Redis, and Docker sandbox locally.
5. Open a pull request with a clear description of the change.

## License

Workmate AI is licensed under the MIT License. See [LICENSE](LICENSE) for details.
