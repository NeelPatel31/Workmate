import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.apis.routes import routes
from app.utils import close_redis, connect_redis, logger, trace_id_var


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_redis()
    yield
    await close_redis()


app = FastAPI(
    title="Workmate-AI",
    description=(
        "Claude-style AI workspace for working with files. Upload documents, "
        "execute tools, generate artifacts, run code in a sandbox, and build "
        "complex workflows through an extensible agent architecture."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    trace_id = f"G-{uuid.uuid4().hex[-8:]}"
    token = trace_id_var.set(trace_id)
    start_time = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Trace-ID"] = trace_id
        return response
    except Exception:
        status_code = 500
        raise
    finally:
        process_time = time.perf_counter() - start_time
        logger.info(
            f"Endpoint: {request.url.path} | Type: {request.method} | "
            f"Time Taken: {process_time:.4f}s | Status Code: {status_code}"
        )
        trace_id_var.reset(token)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for route in routes:
    app.include_router(route)
