from __future__ import annotations

import threading
import time
from typing import Optional

from app.models import Account


class TTLCache:
    """プラットフォームを問わない、username→Accountの単純なTTLキャッシュ。
    ロック保護・monotonic時刻ベースで有効期限を管理する。"""

    def __init__(self, ttl_seconds: float):
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, Account]] = {}

    def get(self, key: str) -> Optional[Account]:
        """有効期限内であればキャッシュされたAccountを返す。期限切れ/未登録ならNone。"""
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            cached_at, account = entry
            if time.monotonic() - cached_at > self._ttl_seconds:
                del self._store[key]
                return None
            return account

    def put(self, key: str, value: Account) -> None:
        """取得に成功したAccountのみキャッシュする（失敗・Noneはキャッシュしない）。"""
        with self._lock:
            self._store[key] = (time.monotonic(), value)
