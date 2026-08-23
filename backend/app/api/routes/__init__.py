"""Versioned API routes."""

from app.api.routes.actions import router as actions_router
from app.api.routes.cases import router as cases_router

__all__ = ["actions_router", "cases_router"]
