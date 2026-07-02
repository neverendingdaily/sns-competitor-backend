"""後方互換のための再エクスポート。実体は app.collectors.common.net に一般化された
（X専用ではなくプラットフォーム共通の基盤になった）。"""
from app.collectors.common.net import (  # noqa: F401
    DEFAULT_INTERVAL_RANGE,
    USER_AGENT,
    polite_get,
)
