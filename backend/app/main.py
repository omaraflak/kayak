from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from backend.app.agents.manager import agent_manager
from backend.app.config import settings
from backend.app.database import init_db
from backend.app.routes import (
    agents,
    conversations,
    models,
    settings as settings_route,
    skills,
    tasks,
    tool_builder,
    tools,
)
from backend.app.skills.registry import skill_registry
from backend.app.tools.registry import tool_registry


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await init_db()
    agent_manager.ensure_default_agents()
    agent_manager.load_all_agents()
    skill_registry.load_all_skills()

    # Import tools to ensure builtins are registered
    import backend.app.tools  # noqa: F401

    tool_registry.load_custom_tools()

    from backend.app.vllm.manager import vllm_manager
    import asyncio
    asyncio.create_task(vllm_manager.check_and_sync_status())
    yield
    # Shutdown


app = FastAPI(
    title="Kayak Agent Platform",
    description="Opinionated, open-source, containerized AI agent workspace",
    version="1.0.0",
    lifespan=lifespan,
)

# Enable CORS for frontend development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from backend.app.vllm import routes as vllm_routes

# Register API Routers
app.include_router(conversations.router)
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
        "default_model": settings.DEFAULT_MODEL,
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
