from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import quote

from app.collectors.common import net

logger = logging.getLogger(__name__)

API_URL = "https://serpapi.com/search.json"

# SerpAPI(Google engine)の1ページあたりの最大件数(num)、およびstartパラメータの
# 単位（0始まり・結果件数単位）。Google CSEの`start`（1始まり）とは異なる点に注意。
RESULTS_PER_PAGE = 10


def discover_via_serpapi(
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
    """SerpAPI(Google engine, `site:{site} {query}`)をページネーションしながら
    検索し、`username_pattern`（`group(1)`がusername）で結果URL
    (`organic_results[].link`)からユーザー名を抽出する。Google CSEが新規プロジェクト
    で利用不可になったことを受けて追加した発見(discovery)ソースの一つ
    （`discovery_brave.py`と並列運用）。

    `api_key`が空文字の場合は未設定として即座に空リストを返す（呼び出し元が設定の
    有無を判定する必要がないフェイルソフト設計）。クォータ超過(429)や認証エラー
    (401)、接続失敗もフェイルソフトで空リストに縮退し、検索全体は失敗させない。
    """
    if not api_key:
        return []

    usernames: list[str] = []
    seen: set[str] = set()

    for page in range(max_pages):
        start = page * RESULTS_PER_PAGE

        search_url = (
            f"{API_URL}?engine=google&q={quote(f'site:{site} {query}')}"
            f"&num={RESULTS_PER_PAGE}&start={start}&api_key={quote(api_key)}"
        )

        response = net.polite_get(
            search_url,
            bucket=bucket,
            interval_range=interval_range,
            max_concurrency=max_concurrency,
        )
        if response is None:
            logger.warning("serpapi discovery unreachable for site=%s query=%s", site, query)
            break
        if response.status_code in (401, 429):
            # クォータ超過・キー無効等。実行時の設定ミスの可能性が高いため一度だけ
            # WARNINGを出すが、検索全体は空リストへフェイルソフトする。
            try:
                body = response.text[:500]
            except Exception:
                body = "<body unavailable>"
            logger.warning(
                "serpapi quota/auth error (status=%d) for site=%s query=%s body=%s",
                response.status_code,
                site,
                query,
                body,
            )
            break
        if not response.ok:
            logger.warning(
                "serpapi discovery returned %d for site=%s query=%s",
                response.status_code,
                site,
                query,
            )
            break

        try:
            payload = response.json()
        except ValueError:
            logger.warning("serpapi returned a non-JSON response for site=%s query=%s", site, query)
            break

        if payload.get("error"):
            logger.warning("serpapi returned an error for site=%s query=%s: %s", site, query, payload.get("error"))
            break

        items = payload.get("organic_results") or []
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
