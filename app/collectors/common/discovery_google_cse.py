from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

from app.collectors.common import net

logger = logging.getLogger(__name__)

# 【廃止・未使用】Google Custom Search JSON APIは2026年1月に新規プロジェクトへの
# 提供を終了し（既存プロジェクトも2027-01-01に完全終了予定）、新規発行のAPIキーでは
# 常に403(accessNotConfigured)が返るため、discovery(instagram/threads/tiktok)の
# どのdiscovery.pyからも呼び出さなくなった。置き換え先は`discovery_brave.py`
# （Brave Search API）・`discovery_serpapi.py`（SerpAPI）。このファイルは参照実装
# として残しているが、削除については別途承認が必要（ワークスペースのファイル削除
# ルールのため）。

API_URL = "https://www.googleapis.com/customsearch/v1"

# Google Custom Search JSON APIの1ページあたりの最大件数、および`start`パラメータの
# 取りうる範囲（1始まり・10刻み・start+num<=100の制約があるため実質10ページまで）。
# DuckDuckGo html-lite(`&s=`オフセット、HTML全文への正規表現)とはページネーション方式・
# レスポンス形式のいずれも異なる点に注意（README「Google CSEはDDGの単純な代替ではない」参照）。
RESULTS_PER_PAGE = 10
MAX_START = 100


def discover_via_google_cse(
    query: str,
    *,
    site: str,
    username_pattern: "re.Pattern[str]",
    reserved_paths: set[str],
    max_pages: int,
    limit: int,
    api_key: str,
    cx: str,
    bucket: Optional[str] = None,
    interval_range: Optional[tuple[float, float]] = None,
    max_concurrency: int = 1,
) -> list[str]:
    """Google Custom Search JSON API(`site:{site} {query}`)をページネーションしながら
    検索し、`username_pattern`（`group(1)`がusername）で結果URL(`items[].link`)から
    ユーザー名を抽出する。DuckDuckGo経由の`discover_via_search_engine`が使えない
    （ブロックされている等）環境向けの第二の発見(discovery)ソース。

    `api_key`/`cx`が空文字の場合は未設定として即座に空リストを返す（呼び出し元が
    設定の有無を判定する必要がないフェイルソフト設計）。クォータ超過(403/429)や
    接続失敗もフェイルソフトで空リストに縮退し、検索全体は失敗させない。
    """
    if not api_key or not cx:
        return []

    usernames: list[str] = []
    seen: set[str] = set()

    for page in range(max_pages):
        start = page * RESULTS_PER_PAGE + 1
        if start > MAX_START:
            break

        search_url = (
            f"{API_URL}?key={quote(api_key)}&cx={quote(cx)}"
            f"&q={quote(f'site:{site} {query}')}&start={start}"
        )

        response = net.polite_get(
            search_url,
            bucket=bucket,
            interval_range=interval_range,
            max_concurrency=max_concurrency,
        )
        if response is None:
            logger.warning("google cse discovery unreachable for site=%s query=%s", site, query)
            break
        if response.status_code in (403, 429):
            # クォータ超過・キー無効等。実行時の設定ミスの可能性が高いため一度だけ
            # WARNINGを出すが、検索全体は空リストへフェイルソフトする。
            # レスポンス本文にGoogle側の実際の理由（accessNotConfigured/
            # API_KEY_HTTP_REFERRER_BLOCKED/dailyLimitExceededUnreg等）が
            # JSONで入っているため、診断のためログに含める（APIキー自体は
            # レスポンス本文に含まれないため機密漏洩の懸念はない）。
            try:
                body = response.text[:500]
            except Exception:
                body = "<body unavailable>"
            logger.warning(
                "google cse quota/auth error (status=%d) for site=%s query=%s body=%s",
                response.status_code,
                site,
                query,
                body,
            )
            break
        if not response.ok:
            logger.warning(
                "google cse discovery returned %d for site=%s query=%s",
                response.status_code,
                site,
                query,
            )
            break

        try:
            payload = response.json()
        except ValueError:
            logger.warning("google cse returned a non-JSON response for site=%s query=%s", site, query)
            break

        items = payload.get("items") or []
        if not items:
            break

        found_new = False
        for item in items:
            link = item.get("link", "")
            match = username_pattern.search(link)
            if not match:
                continue
            username = match.group(1)
            if username.lower() in reserved_paths or username in seen:
                continue
            seen.add(username)
            usernames.append(username)
            found_new = True
            if len(usernames) >= limit:
                return usernames

        if not found_new:
            break

    return usernames
