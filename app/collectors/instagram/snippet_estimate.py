from __future__ import annotations

import re

from app.collectors.common.snippet_signals import SnippetSignals, fetch_snippet_signals

_OWN_PROFILE_RE_CACHE: dict[str, "re.Pattern[str]"] = {}


def _own_profile_pattern(username: str) -> "re.Pattern[str]":
    pattern = _OWN_PROFILE_RE_CACHE.get(username)
    if pattern is None:
        pattern = re.compile(
            rf"^https?://(www\.)?instagram\.com/{re.escape(username)}/?(?:[?#]|$)",
            re.IGNORECASE,
        )
        _OWN_PROFILE_RE_CACHE[username] = pattern
    return pattern


def estimate(username: str) -> SnippetSignals:
    """Instagramは`web_profile_info`で通常フルの実データが取得できるため、
    このフォールバックが呼ばれるのは稀（非公開アカウント・レート制限等で
    followers/following/postsCountの一部が0のまま返ってきた場合のみ）。
    薄いラッパーで、実処理は`app/collectors/common/snippet_signals.py`（全
    プラットフォーム共通）に委譲する。
    """
    return fetch_snippet_signals(
        username=username,
        query=f"{username} instagram.com followers following",
        own_profile_pattern=_own_profile_pattern(username),
        bucket="brave-search-api:instagram-snippet-estimate",
    )
