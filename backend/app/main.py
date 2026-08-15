import asyncio
from contextlib import asynccontextmanager
import logging
from typing import Set
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from backend.app.agent import turns
from backend.app.agent.sandbox import sandbox_manager
from backend.app.agents.manager import agent_manager
from backend.app.config import settings
from backend.app.database import init_db, reconcile_interrupted_state
from backend.app.routes import (
    activity,
    agents,
    auth,
    conversations,
    models,
    settings as settings_route,
    skills,
    tasks,
    tool_builder,
    tools,
    workspace,
)
from backend.app.routes.auth import PUBLIC_API_PATHS, is_authorized
from backend.app.skills.registry import skill_registry
from backend.app.tools.registry import tool_registry
from backend.app.vllm import routes as vllm_routes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Background tasks are held here for their lifetime: asyncio keeps only weak
# references to running tasks, so a fire-and-forget task can be garbage collected
# mid-flight.
_background_tasks: Set[asyncio.Task] = set()


def track_background_task(task: asyncio.Task) -> asyncio.Task:
    """Retains a strong reference to a background task until it finishes."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    # A previous process may have died mid-turn, leaving conversations pinned in a
    # RUNNING state that the UI can never clear on its own.
    await reconcile_interrupted_state()

    agent_manager.ensure_default_agents()
    agent_manager.load_all_agents()
    skill_registry.load_all_skills()

    # Import tools to ensure builtins are registered
    import backend.app.tools  # noqa: F401

    tool_registry.load_custom_tools()

    from backend.app.vllm.manager import vllm_manager

    track_background_task(asyncio.create_task(vllm_manager.check_and_sync_status()))

    if not settings.AUTH_TOKEN and settings.HOST not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Kayak is bound to %s with no KAYAK_AUTH_TOKEN set. Agents have shell and "
            "filesystem access, so anyone who can reach this port can run code on this "
            "host. Set KAYAK_AUTH_TOKEN or bind to 127.0.0.1.",
            settings.HOST,
        )

    yield

    # Shutdown: stop sandbox containers this process started so they do not outlive it.
    await turns.cancel_all()
    await sandbox_manager.shutdown_all()


app = FastAPI(
    title="Kayak Agent Platform",
    description="Opinionated, open-source, containerized AI agent workspace",
    version="1.0.0",
    lifespan=lifespan,
)

# Explicit origins rather than a wildcard: credentialed requests are rejected by
# browsers under a wildcard, and the session cookie makes this a credentialed API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def require_auth_token(request: Request, call_next):
    """Rejects unauthenticated API calls when a shared secret is configured."""
    path = request.url.path
    if (
        settings.AUTH_TOKEN
        and path.startswith("/api")
        and path not in PUBLIC_API_PATHS
        and request.method != "OPTIONS"
        and not is_authorized(request)
    ):
        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
    return await call_next(request)


# Register API Routers
app.include_router(auth.router)
app.include_router(conversations.router)
app.include_router(activity.router)
app.include_router(workspace.router)
app.include_router(agents.router)
app.include_router(models.router)
app.include_router(vllm_routes.router)
app.include_router(skills.router)
app.include_router(tools.router)
app.include_router(tool_builder.router)
app.include_router(tasks.router)
app.include_router(settings_route.router)


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "total_agents": len(agent_manager.list_agents()),
        "total_skills": len(skill_registry.list_skills()),
        "total_tools": len(tool_registry.list_all_tools()),
    }


# Mount Frontend if built
FRONTEND_DIST = settings.BASE_DIR / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount(
        "/assets",
        StaticFiles(directory=str(FRONTEND_DIST / "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path.startswith("api"):
            raise HTTPException(status_code=404, detail="API endpoint not found")
        index_path = FRONTEND_DIST / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path))
        raise HTTPException(status_code=404, detail="Frontend build index.html not found")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
