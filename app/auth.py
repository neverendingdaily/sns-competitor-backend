from typing import Optional

from fastapi import Header

from app import config
from app.errors import UnauthorizedError


def optional_bearer(authorization: Optional[str] = Header(default=None)) -> None:
    if not config.BACKEND_API_KEY:
        return

    expected = f"Bearer {config.BACKEND_API_KEY}"
    if authorization != expected:
        raise UnauthorizedError("invalid or missing API key")
