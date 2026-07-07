from __future__ import annotations

import logging
import re
from typing import NamedTuple, Optional
from urllib.parse import quote

from app import config
from app.collectors.common.net import polite_get

logger = logging.getLogger(__name__)

API_URL = "https://api.search.brave.com/res/v1/web/search"

# 実際のBrave Search APIレスポンス（2026-07時点で実機確認済み）で観測した表記ゆれ:
#   "55 Following · 27 Followers"          （数値の直後にラベル、区切りなし）
#   "9,387 · Following · 6.6M · Followers" （数値とラベルの間に" · "が入る）
# のどちらにも対応できるよう、数値とラベルの間の区切り文字（middot/ハイフン/コロン/
# 空白）はすべて任意とする。日本語表記（"1.2万フォロワー"等）にも対応する。
_NUMBER = r"([\d][\d,]*\.?\d*\s*(?:[KkMmBb]|万|億)?)"
FOLLOWING_RE = re.compile(_NUMBER + r"\s*[·\-:]?\s*(?:Following|フォロー中?)", re.IGNORECASE)
FOLLOWERS_RE = re.compile(_NUMBER + r"\s*[·\-:]?\s*(?:Followers|フォロワー)", re.IGNORECASE)

_OWN_PROFILE_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


class EstimatedCounts(NamedTuple):
    followers: int
    following: int


def _parse_count_token(raw: str) -> int:
    s = raw.strip().replace(",", "").replace(" ", "")
    if not s:
        return 0
    multiplier = 1.0
    suffix = s[-1]
    if suffix in "KkMmBb":
        multiplier = {"k": 1_000.0, "m": 1_000_000.0, "b": 1_000_000_000.0}[suffix.lower()]
        s = s[:-1]
    elif suffix == "万":
        multiplier, s = 10_000.0, s[:-1]
    elif suffix == "億":
        multiplier, s = 100_000_000.0, s[:-1]
    try:
        return int(float(s) * multiplier)
    except ValueError:
        return 0


def _is_own_profile_url(url: str, username: str) -> bool:
    # 無関係なページ（他アカウントの言及・別プラットフォームの集計サイト等）の
    # 数値を誤って拾わないよう、本人のプロフィールURLに紐づく結果のみを対象にする。
    pattern = _OWN_PROFILE_RE_CACHE.get(username)
    if pattern is None:
        pattern = re.compile(
            rf"^https?://(www\.)?(x|twitter)\.com/{re.escape(username)}/?(?:[?#]|$)",
            re.IGNORECASE,
        )
        _OWN_PROFILE_RE_CACHE[username] = pattern
    return bool(pattern.match(url))


def estimate_counts(username: str) -> Optional[EstimatedCounts]:
    """Xのプロフィール取得（GraphQL/metaタグ）がブロックされてフォロワー数・
    フォロー数が0（取得不可のセンチネル値）になった場合のフォールバック。

    Brave Search APIの検索結果スニペット（title/description）に
    「N Following / N Followers」「N万フォロワー」等の記述が含まれていないか
    正規表現で解析し、推測値として補完する。あくまで検索エンジンが偶然
    インデックスしたテキストからの推測であり、見つからない・信頼できない
    場合はNoneを返すフェイルソフト設計（呼び出し元はNoneなら0のまま扱う）。
    """
    api_key = config.BRAVE_SEARCH_API_KEY
    if not api_key:
        return None

    query = f"{username} x.com followers following"
    search_url = f"{API_URL}?q={quote(query)}&count=10"

    response = polite_get(
        search_url,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        bucket="brave-search-api:x-follower-estimate",
        interval_range=(config.X_FOLLOWER_ESTIMATE_JITTER_MIN, config.X_FOLLOWER_ESTIMATE_JITTER_MAX),
        max_concurrency=config.X_FOLLOWER_ESTIMATE_CONCURRENCY,
    )
    if response is None:
        logger.warning("brave search follower-estimate unreachable for username=%s", username)
        return None
    if not response.ok:
        logger.warning(
            "brave search follower-estimate returned %d for username=%s", response.status_code, username
        )
        return None

    try:
        payload = response.json()
    except ValueError:
        return None

    items = ((payload.get("web") or {}).get("results")) or []

    followers: Optional[int] = None
    following: Optional[int] = None
    for item in items:
        url = item.get("url", "")
        if not _is_own_profile_url(url, username):
            continue

        text = f"{item.get('title', '')} {item.get('description', '')}"

        if followers is None:
            match = FOLLOWERS_RE.search(text)
            if match:
                followers = _parse_count_token(match.group(1))
        if following is None:
            match = FOLLOWING_RE.search(text)
            if match:
                following = _parse_count_token(match.group(1))

        if followers is not None and following is not None:
            break

    if followers is None and following is None:
        return None

    return EstimatedCounts(followers=followers or 0, following=following or 0)
