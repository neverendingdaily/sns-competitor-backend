from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

from app.collectors.common import net

logger = logging.getLogger(__name__)

API_URL = "https://api.search.brave.com/res/v1/web/search"

# Brave Search APIの1ページあたりの最大件数(count)、およびoffsetの取りうる範囲
# （0始まり・ページ単位、無料枠は概ねoffset<=9が上限）。Google CSEの`start`
# （1始まり・結果件数単位）とは単位が異なる点に注意。
RESULTS_PER_PAGE = 20
MAX_OFFSET = 9


def discover_via_brave(
    query: str,
    *,
    site: str,
    username_pattern: "re.Pattern[str]",
    reserved_paths: set[str],
    max_pages: int,
    limit: int,
    api_key: str,
    bucket: Optional[str] = None,
    interval_range: Optional[tuple[float, float]] = None,
    max_concurrency: int = 1,
) -> list[str]:
    """Brave Search API(`site:{site} {query}`)をページネーションしながら検索し、
    `username_pattern`（`group(1)`がusername）で結果URL(`web.results[].url`)から
    ユーザー名を抽出する。Google CSEが新規プロジェクトで利用不可になったことを受けて
    追加した発見(discovery)ソースの一つ（`discovery_serpapi.py`と並列運用）。

    `api_key`が空文字の場合は未設定として即座に空リストを返す（呼び出し元が設定の
    有無を判定する必要がないフェイルソフト設計）。クォータ超過(429)や認証エラー
    (401/403)、接続失敗もフェイルソフトで空リストに縮退し、検索全体は失敗させない。
    """
    if not api_key:
        return []

    usernames: list[str] = []
    seen: set[str] = set()

    for page in range(max_pages):
        if page > MAX_OFFSET:
            break

        search_url = (
            f"{API_URL}?q={quote(f'site:{site} {query}')}"
            f"&count={RESULTS_PER_PAGE}&offset={page}"
        )

        response = net.polite_get(
            search_url,
            headers={"Accept": "application/json", "X-Subscription-Token": api_key},
            bucket=bucket,
            interval_range=interval_range,
            max_concurrency=max_concurrency,
        )
        if response is None:
            logger.warning("brave search discovery unreachable for site=%s query=%s", site, query)
            break
        if response.status_code in (401, 403, 429):
            # クォータ超過・キー無効等。実行時の設定ミスの可能性が高いため一度だけ
            # WARNINGを出すが、検索全体は空リストへフェイルソフトする。
            try:
                body = response.text[:500]
            except Exception:
                body = "<body unavailable>"
            logger.warning(
                "brave search quota/auth error (status=%d) for site=%s query=%s body=%s",
                response.status_code,
                site,
                query,
                body,
            )
            break
        if not response.ok:
            logger.warning(
                "brave search discovery returned %d for site=%s query=%s",
                response.status_code,
                site,
                query,
            )
            break

        try:
            payload = response.json()
        except ValueError:
            logger.warning("brave search returned a non-JSON response for site=%s query=%s", site, query)
            break

        items = ((payload.get("web") or {}).get("results")) or []
        if not items:
            break

        found_new = False
        for item in items:
            link = item.get("url", "")
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
