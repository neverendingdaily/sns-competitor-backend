from fastapi import APIRouter

from app import config

router = APIRouter()


@router.get("/api/v1/health")
def health() -> dict:
    return {"status": "ok", "version": config.APP_VERSION}
