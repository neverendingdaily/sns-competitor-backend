from fastapi import APIRouter

from app import config

router = APIRouter()


@router.get(
    "/api/v1/health",
    summary="ヘルスチェック",
    description="バックエンドAPIが正常に起動しているかを確認します。稼働中のアプリケーションバージョンも返します。",
)
def health() -> dict:
    return {"status": "ok", "version": config.APP_VERSION}
