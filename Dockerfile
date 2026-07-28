# Use an official lightweight Python image
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_COMPILE_BYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y curl && \
    ARCH=$(uname -m) && \
    if [ "$ARCH" = "aarch64" ]; then DOCKER_ARCH="aarch64"; else DOCKER_ARCH="x86_64"; fi && \
    curl -fsSL "https://download.docker.com/linux/static/stable/${DOCKER_ARCH}/docker-26.1.4.tgz" | tar -xz -C /usr/local/bin --strip-components=1 docker/docker && \
    rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-install-project --no-dev

COPY . .

EXPOSE 5001
EXPOSE 8501

ENV PATH="/app/.venv/bin:$PATH"
