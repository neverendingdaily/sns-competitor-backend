import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import config
from app.errors import ApiError
from app.routers import accounts, health

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("sns_competitor_backend")

app = FastAPI(
    title="SNS競合分析バックエンド",
    description="X、YouTube、Instagram、TikTok、Threadsのアカウント情報を検索・取得するためのAPIです。",
    version=config.APP_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ALLOW_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(accounts.router)


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"error": exc.message})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": f"invalid request: {exc.errors()}"})


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unexpected error handling %s %s", request.method, request.url)
    return JSONResponse(status_code=500, content={"error": "internal server error"})
