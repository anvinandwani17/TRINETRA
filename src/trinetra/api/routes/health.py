"""Process health only; does not claim analysis readiness."""

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from trinetra import __version__

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Confirm the API process responds, without checking external services."""
    return HealthResponse(
        status="ok", service=request.app.state.settings.app_name,
        version=__version__,
    )
