from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

from app import config
from app.collectors.common.net import polite_get
from app.models import Account

logger = logging.getLogger(__name__)

API_URL = "https://api.search.brave.com/res/v1/web/search"

# 実際のBrave Search APIレスポンス（2026-07時点で実機確認済み、X/Threads/TikTok
# それぞれの本人プロフィールURL）で観測した表記ゆれ:
#   X       : "55 Following · 27 Followers"（区切りなし直結、または" · "区切り）
#   Threads : "5.6M followers • 150 threads"（区切り文字は "•"、ラベルは小文字）
#   TikTok  : "3676Following · 2.3MFollowers"（数値とラベルの間に空白が一切無い）
# のいずれにも対応できるよう、数値とラベルの間の区切り文字（middot/bullet/ハイフン/
# コロン/空白）はすべて任意とする。日本語表記（"1.2万フォロワー"等）にも対応する。
_NUMBER = r"([\d][\d,]*\.?\d*\s*(?:[KkMmBb]|万|億)?)"
_SEP = r"\s*[·•\-:]?\s*"

FOLLOWER_LABELS = ("Followers", "フォロワー")
FOLLOWING_LABELS = ("Following", "フォロー中", "フォロー")
# プラットフォームによって「投稿」の呼び方が異なる（X=Posts、Threads=threads、
# TikTok/YouTube=videos、Instagram=posts）ため主要な表記をまとめて扱う。
POST_LABELS = ("Posts", "posts", "Threads", "threads", "Videos", "videos", "投稿", "スレッド", "動画")

# リンク切れ（削除済み・非公開・存在しないアカウント）を示す典型的な文言。
# HTTPステータスで判別できないプラットフォーム（Threads等）向けの補助シグナル。
NOT_FOUND_PHRASES = (
    "page isn't available", "page not found", "this content isn't available",
    "couldn't find this account", "sorry, this page", "user not found",
    "account not found", "doesn't exist", "no longer exists",
    "ページが見つかりません", "見つかりませんでした", "存在しません",
    "アカウントが見つかりません", "削除されました",
)


@dataclass(frozen=True)
class SnippetSignals:
    followers: Optional[int] = None
    following: Optional[int] = None
    posts: Optional[int] = None
    not_found: bool = False


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


def _build_label_pattern(labels: tuple[str, ...]) -> "re.Pattern[str]":
    alt = "|".join(re.escape(label) for label in labels)
    return re.compile(_NUMBER + _SEP + rf"(?:{alt})", re.IGNORECASE)


_FOLLOWERS_RE = _build_label_pattern(FOLLOWER_LABELS)
_FOLLOWING_RE = _build_label_pattern(FOLLOWING_LABELS)
_POSTS_RE = _build_label_pattern(POST_LABELS)


def _extract_number(text: str, pattern: "re.Pattern[str]") -> Optional[int]:
    match = pattern.search(text)
    if not match:
        return None
    return _parse_count_token(match.group(1))


def _has_not_found_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase.lower() in lowered for phrase in NOT_FOUND_PHRASES)


def fetch_snippet_signals(
    *,
    username: str,
    query: str,
    own_profile_pattern: "re.Pattern[str]",
    bucket: str,
    interval_range: Optional[tuple[float, float]] = None,
    max_concurrency: Optional[int] = None,
) -> SnippetSignals:
    """Brave Search APIの検索結果スニペット（title/description）を解析し、
    フォロワー数・フォロー数・投稿数・リンク切れ（削除済み等）を推測する
    プラットフォーム共通ロジック。

    APIがブロックされる・スニペットに該当の記述が無い等、推測できない場合は
    全フィールドがNone/FalseのSnippetSignalsを返すフェイルソフト設計
    （呼び出し元は「取得できなかった」として扱えばよい）。

    無関係なページ（他アカウントの言及・別プラットフォームの集計サイト等）の
    数値を誤って拾わないよう、`own_profile_pattern`にマッチする結果URL
    （本人のプロフィールページ）のスニペットのみを解析対象にする。
    """
    api_key = config.BRAVE_SEARCH_API_KEY
    if not api_key:
        return SnippetSignals()

    search_url = f"{API_URL}?q={quote(query)}&count=10"
    response = polite_get(
        search_url,
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
        bucket=bucket,
        interval_range=interval_range or (config.SNIPPET_ESTIMATE_JITTER_MIN, config.SNIPPET_ESTIMATE_JITTER_MAX),
        max_concurrency=max_concurrency or config.SNIPPET_ESTIMATE_CONCURRENCY,
    )
    if response is None:
        logger.warning("brave search snippet-signals unreachable for username=%s", username)
        return SnippetSignals()
    if not response.ok:
        logger.warning(
            "brave search snippet-signals returned %d for username=%s", response.status_code, username
        )
        return SnippetSignals()

    try:
        payload = response.json()
    except ValueError:
        return SnippetSignals()

    items = ((payload.get("web") or {}).get("results")) or []

    followers: Optional[int] = None
    following: Optional[int] = None
    posts: Optional[int] = None
    not_found = False

    for item in items:
        url = item.get("url", "")
        if not own_profile_pattern.match(url):
            continue

        # Brave Search APIはtitle/description中のハイライト箇所を<strong>タグで
        # 囲んで返す（例: "<strong>5.6M</strong> followers"）。除去しないと数値と
        # ラベルの間にタグ文字列が挟まり隣接パターンの正規表現がマッチしなくなるため、
        # 解析前にHTMLタグを取り除く（実機確認済み、2026-07-08）。
        raw_text = f"{item.get('title', '')} {item.get('description', '')}"
        text = re.sub(r"<[^>]+>", "", raw_text)

        if not_found is False and _has_not_found_phrase(text):
            not_found = True
        if followers is None:
            followers = _extract_number(text, _FOLLOWERS_RE)
        if following is None:
            following = _extract_number(text, _FOLLOWING_RE)
        if posts is None:
            posts = _extract_number(text, _POSTS_RE)

        if not_found or (followers is not None and following is not None and posts is not None):
            break

    return SnippetSignals(followers=followers, following=following, posts=posts, not_found=not_found)


def merge_into_account(account: Account, signals: SnippetSignals) -> Account:
    """アカウントの`followers`/`following`/`postsCount`のうち、既に0（未取得の
    センチネル値）のフィールドだけをsignalsの推測値で補完する。実際に取得できた
    値（0より大きい）は上書きしない。
    """
    update: dict = {}
    if account.followers == 0 and signals.followers is not None:
        update["followers"] = signals.followers
    if account.following == 0 and signals.following is not None:
        update["following"] = signals.following
    if account.posts_count == 0 and signals.posts is not None:
        update["posts_count"] = signals.posts
    return account.model_copy(update=update) if update else account
