from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.routes import cases_router
from app.core.config import get_settings


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    application = FastAPI(title=settings.app_name)
    application.include_router(health_router)
    application.include_router(cases_router)
    register_exception_handlers(application)
    return application


app = create_app()
