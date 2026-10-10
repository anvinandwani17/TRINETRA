"""FastAPI entry point: uvicorn trinetra.api.main:app --reload."""

from fastapi import FastAPI

from trinetra import __version__
from trinetra.api.routes.health import router as health_router
from trinetra.core.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings()
    app = FastAPI(
        title=settings.app_name, version=__version__,
        description="Initial scaffold. Satellite analysis is not implemented.",
    )
    app.state.settings = settings
    app.include_router(health_router, prefix="/api/v1")
    return app


app = create_app()
