from __future__ import annotations

import logging
import random
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# 呼び出し元がinterval_rangeを指定しなかった場合の既定値（小規模サイトへの
# 配慮を前提とした控えめな間隔）。プラットフォーム固有のconfigには依存しない
# （このモジュールは全プラットフォーム共通の基盤のため）。
DEFAULT_INTERVAL_RANGE = (4.0, 8.0)

# バケット単位（既定はホスト名、呼び出し元が明示的に分けることも可能）で
# 「同時実行数の上限」と「次の開始時刻の予約」を管理する。
# 例えば x.com は匿名スクレイピング(bucket="x.com")と認証GraphQL呼び出し
# (bucket="x.com:graphql")を別バケットとして扱い、認証パスだけ並列度と
# 間隔を変えられるようにしている。
_semaphores: dict[str, threading.Semaphore] = {}
_semaphores_lock = threading.Lock()

_next_start_at: dict[str, float] = {}
_schedule_lock = threading.Lock()


def _get_semaphore(key: str, max_concurrency: int) -> threading.Semaphore:
    with _semaphores_lock:
        semaphore = _semaphores.get(key)
        if semaphore is None:
            semaphore = threading.Semaphore(max_concurrency)
            _semaphores[key] = semaphore
        return semaphore


def _reserve_slot(key: str, interval_range: tuple[float, float], max_concurrency: int) -> None:
    """同一バケットへのリクエスト開始時刻を予約し、最小間隔を守る（礼儀正しさの担保）。

    `_next_start_at`はバケット単位で単一の予約列であるため、`interval_range`を
    そのまま使うと`max_concurrency`（同時実行数）を上げても開始時刻の間隔が
    縮まらず、実質的に直列実行と変わらない待ち時間になってしまう
    （`max_concurrency`本の並行レーンがあるのに予約列が1本だけのため）。
    そのため間隔を`max_concurrency`で割り、N本の並行レーンで待ち時間を
    分担しているのと同等になるよう調整する（1レーンあたりの間隔は変えず、
    全体のリクエストレートだけがconcurrencyに比例して上がる）。
    """
    with _schedule_lock:
        now = time.monotonic()
        start = max(now, _next_start_at.get(key, 0.0))
        lo, hi = interval_range
        step = random.uniform(lo, hi) / max(max_concurrency, 1)
        _next_start_at[key] = start + step

    wait = start - time.monotonic()
    if wait > 0:
        time.sleep(wait)


def polite_get(
    url: str,
    *,
    session: Optional[requests.Session] = None,
    headers: Optional[dict[str, str]] = None,
    timeout: float = 15.0,
    interval_range: Optional[tuple[float, float]] = None,
    max_concurrency: int = 1,
    bucket: Optional[str] = None,
    proxies: Optional[dict[str, str]] = None,
) -> Optional[requests.Response]:
    """接続レベルの失敗のみNoneを返す。HTTPステータスの解釈は呼び出し元に委ねる。

    `max_concurrency`件まで同時実行を許しつつ、同一バケットへのリクエスト開始
    時刻には`interval_range`の最小間隔を強制する（1件ずつ全待ち時間ブロックする
    旧方式と異なり、待機は「次の開始時刻まで」だけで済む）。

    プラットフォームを問わない共通基盤のため、`bucket`・`interval_range`・
    `max_concurrency`・`proxies`は呼び出し元（各プラットフォームのcollector/session）が
    自身のconfig値を渡すこと。`proxies`は明示的に渡されたリクエストだけがプロキシを
    経由する設計であり、環境変数（`HTTP_PROXY`等）による暗黙のプロキシ経由には依存しない
    （Cookie付き認証リクエストまで意図せずプロキシを通ってしまう事故を避けるため。
    README「Discovery専用プロキシ」参照）。
    """
    host = urlparse(url).netloc
    key = bucket or host
    resolved_interval_range = interval_range or DEFAULT_INTERVAL_RANGE

    semaphore = _get_semaphore(key, max_concurrency)
    semaphore.acquire()
    try:
        _reserve_slot(key, resolved_interval_range, max_concurrency)
        client = session or requests
        try:
            return client.get(
                url,
                headers={"User-Agent": USER_AGENT, **(headers or {})},
                timeout=timeout,
                proxies=proxies,
            )
        except requests.RequestException as exc:
            logger.warning("request to %s failed: %s", url, exc)
            return None
    finally:
        semaphore.release()
