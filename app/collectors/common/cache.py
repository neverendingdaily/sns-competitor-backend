from __future__ import annotations

import threading
import time
from typing import Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """プラットフォームを問わない、key→値の単純なTTLキャッシュ。
    ロック保護・monotonic時刻ベースで有効期限を管理する。値の型は呼び出し元が
    `TTLCache[Account]`・`TTLCache[tuple[Account, Optional[int]]]`のように指定する
    （2026-07-08、TikTokがAccountに加えて推測いいね数も一緒にキャッシュする
    必要が生じたため、Account専用からジェネリックへ一般化した）。"""

    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> Optional[T]:
        """有効期限内であればキャッシュされた値を返す。期限切れ/未登録ならNone。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            cached_at, value = entry
            if time.monotonic() - cached_at > self._ttl_seconds:
                del self._store[key]
                return None
            return value

    def put(self, key: str, value: T) -> None:
        """取得に成功した値のみキャッシュする（失敗・Noneはキャッシュしない）。"""
        with self._lock:
            self._store[key] = (time.monotonic(), value)
