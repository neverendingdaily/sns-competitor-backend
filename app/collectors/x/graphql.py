from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional
from urllib.parse import quote

import requests

from app import config
from app.collectors.common import net
from app.errors import UpstreamUnavailableError
from app.models import Account

logger = logging.getLogger(__name__)

GRAPHQL_BUCKET = "x.com:graphql"


@dataclass(frozen=True)
class GraphQLProfile:
    """fetch_profile_via_graphqlの内部専用の戻り値。Accountに加えて、
    UserTweets呼び出しに必要なX内部の数値ユーザーID(rest_id)を保持する。
    Accountの契約(フロント向けJSON)には一切含まれない。"""

    account: Account
    rest_id: str


@dataclass(frozen=True)
class RecentEngagement:
    """fetch_recent_tweetsの戻り値。engagement_rateは既に算出・丸め済み。"""

    engagement_rate: float
    last_posted_at: Optional[str]


class UserTweetsError(Exception):
    """UserTweets(直近ツイート)の取得・解析に失敗したことを示す内部例外。

    `CookieAuthError`とは意図的に別物にしている: エンゲージメント率取得は
    「既に成功した認証プロフィール取得に乗る追加機能」であり、この失敗で
    プロフィール本体を非認証metaタグ取得へ格下げしてしまうのは誤り。
    呼び出し元(profile_scraper)はこの例外をその場で握りつぶし、
    engagement_rate=0.0のまま進める。
    """


# UserTweetsが要求するfeatureフラグ。UserByScreenNameとは別のGraphQLクエリの
# ため、必要なフラグ構成も独立して変化しうる。devtoolsのNetworkタブで
# "UserTweets"という名前のリクエストの実際のfeaturesパラメータを確認し、
# 動作しなくなった場合はここを更新すること（README参照）。
USER_TWEETS_FEATURES = {
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "tweetypie_unmention_optimization_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "tweet_awards_web_tipping_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "rweb_video_timestamps_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": True,
    "responsive_web_media_download_video_enabled": False,
    "responsive_web_enhance_cards_enabled": False,
}

# UserByScreenNameが要求するfeatureフラグ。Xが定期的に構成を変更するため、
# 認証パスが急に失敗し始めた場合はdevtoolsのNetworkタブで実際のリクエストURLの
# featuresパラメータを確認し、ここを更新すること（README参照）。
DEFAULT_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "subscriptions_verification_info_is_identity_verified_enabled": True,
    "subscriptions_verification_info_verified_since_enabled": True,
    "highlights_tweets_tab_ui_enabled": True,
    "responsive_web_twitter_article_notes_tab_enabled": True,
    "subscriptions_feature_can_gift_premium": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}


class CookieAuthError(Exception):
    """Cookieが失効・不正であるなど、認証状態がX側に拒否されたことを示す内部例外。

    `ApiError`は継承しない: フロントの`{"error": ...}`レスポンスに漏らすべきでは
    なく、呼び出し元(profile_scraper)が非認証モードへフォールバックするための
    合図として使う。
    """


def fetch_profile_via_graphql(username: str, session: requests.Session) -> Optional[GraphQLProfile]:
    """認証済みセッションでX内部GraphQL(UserByScreenName)を叩き、実際の
    フォロワー数/フォロー数を含むAccountと、UserTweets呼び出しに使う
    rest_idをまとめて返す。見つからなければNone。
    Cookie拒否は`CookieAuthError`、接続不能は`UpstreamUnavailableError`を送出する。
    """
    variables = json.dumps({"screen_name": username, "withSafetyModeUserFields": True})
    features = json.dumps(DEFAULT_FEATURES)
    url = (
        f"https://x.com/i/api/graphql/{config.X_GRAPHQL_USERBYSCREENNAME_ID}/UserByScreenName"
        f"?variables={quote(variables)}&features={quote(features)}"
    )

    response = net.polite_get(
        url,
        session=session,
        interval_range=(config.X_API_JITTER_MIN, config.X_API_JITTER_MAX),
        max_concurrency=config.X_HYDRATE_CONCURRENCY,
        bucket=GRAPHQL_BUCKET,
    )
    if response is None:
        raise UpstreamUnavailableError(f"x.com GraphQL API is unreachable while fetching '{username}'")

    if response.status_code in (401, 403):
        raise CookieAuthError(f"x.com GraphQL API rejected the session (status={response.status_code})")
    if not response.ok:
        raise UpstreamUnavailableError(f"x.com GraphQL API returned {response.status_code} for '{username}'")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamUnavailableError(
            f"x.com GraphQL API returned a non-JSON response for '{username}'"
        ) from exc

    errors = payload.get("errors")
    if errors:
        messages = " / ".join(str(e.get("message", e)) for e in errors)
        if any("authenticat" in str(e).lower() for e in errors):
            raise CookieAuthError(f"x.com GraphQL API authentication error: {messages}")
        logger.warning("x.com GraphQL API returned errors for '%s': %s", username, messages)
        return None

    result = payload.get("data", {}).get("user", {}).get("result", {})
    if not result or result.get("__typename") == "UserUnavailable":
        return None

    legacy = result.get("legacy", {})
    if not legacy:
        return None

    # X側のスキーマ変更(2026-07時点で確認)により、表示名・アバター画像は
    # `legacy`から`core`/`avatar`という別オブジェクトに移動している。
    # `legacy`側にも同名フィールドが残っている可能性を考慮し、
    # 新スキーマの値を優先しつつ`legacy`をフォールバックとして参照する。
    core = result.get("core", {})
    avatar = result.get("avatar", {})
    verification = result.get("verification", {})

    display_name = core.get("name") or legacy.get("name") or username
    avatar_url = avatar.get("image_url") or legacy.get("profile_image_url_https", "")
    is_verified = bool(
        result.get("is_blue_verified")
        or verification.get("verified")
        or legacy.get("verified", False)
    )

    account = Account(
        id=username,
        platform="x",
        username=username,
        display_name=display_name,
        bio=legacy.get("description", ""),
        followers=int(legacy.get("followers_count", 0)),
        following=int(legacy.get("friends_count", 0)),
        posts_count=int(legacy.get("statuses_count", 0)),
        engagement_rate=0.0,  # ここでは埋めない。fetch_recent_tweetsの結果で後段が上書きする
        is_verified=is_verified,
        avatar_url=avatar_url,
        profile_url=f"https://x.com/{username}",
        category="",
        last_posted_at=datetime.now(timezone.utc).isoformat(),
    )
    return GraphQLProfile(account=account, rest_id=str(result.get("rest_id", "")))


def _parse_x_created_at(raw: str) -> Optional[datetime]:
    try:
        return parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None


def fetch_recent_tweets(rest_id: str, followers: int, session: requests.Session) -> RecentEngagement:
    """直近の投稿を取得し、(いいね+RT+リプライ(+引用))÷フォロワー数でengagement_rateを
    算出する。取得・解析に失敗した場合は例外を出さず`UserTweetsError`を送出し、
    呼び出し元でengagement_rate=0.0にフォールバックさせる想定。
    """
    if not rest_id or followers <= 0:
        return RecentEngagement(engagement_rate=0.0, last_posted_at=None)

    variables = json.dumps(
        {
            "userId": rest_id,
            "count": 20,
            "includePromotedContent": False,
            "withQuickPromoteEligibilityTweetFields": False,
            "withVoice": False,
            "withV2Timeline": True,
        }
    )
    features = json.dumps(USER_TWEETS_FEATURES)
    url = (
        f"https://x.com/i/api/graphql/{config.X_GRAPHQL_USERTWEETS_ID}/UserTweets"
        f"?variables={quote(variables)}&features={quote(features)}"
    )

    response = net.polite_get(
        url,
        session=session,
        interval_range=(config.X_API_JITTER_MIN, config.X_API_JITTER_MAX),
        max_concurrency=config.X_HYDRATE_CONCURRENCY,
        bucket=GRAPHQL_BUCKET,
    )
    if response is None:
        raise UserTweetsError(f"x.com GraphQL API is unreachable while fetching tweets for '{rest_id}'")
    if not response.ok:
        raise UserTweetsError(f"x.com GraphQL API returned {response.status_code} for tweets of '{rest_id}'")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UserTweetsError(f"x.com GraphQL API returned a non-JSON response for tweets of '{rest_id}'") from exc

    if payload.get("errors"):
        messages = " / ".join(str(e.get("message", e)) for e in payload["errors"])
        raise UserTweetsError(f"x.com GraphQL API returned errors for tweets of '{rest_id}': {messages}")

    try:
        instructions = payload["data"]["user"]["result"]["timeline_v2"]["timeline"]["instructions"]
    except (KeyError, TypeError) as exc:
        raise UserTweetsError(f"unexpected UserTweets response shape for '{rest_id}'") from exc

    entries = []
    for instruction in instructions:
        if instruction.get("type") == "TimelineAddEntries":
            entries.extend(instruction.get("entries", []))

    tweets: list[dict] = []
    for entry in entries:
        entry_id = entry.get("entryId", "")
        if not entry_id.startswith("tweet-"):
            continue
        try:
            tweet_result = entry["content"]["itemContent"]["tweet_results"]["result"]
        except (KeyError, TypeError):
            continue

        if tweet_result.get("__typename") == "TweetWithVisibilityResults":
            tweet_result = tweet_result.get("tweet", tweet_result)

        legacy = tweet_result.get("legacy")
        if not legacy:
            continue
        if "retweeted_status_result" in legacy:
            # 純粋なリツイート。エンゲージメント数字は元投稿者のものであり
            # このアカウント自身のエンゲージメントではないため除外する。
            continue

        tweets.append(legacy)

    if not tweets:
        return RecentEngagement(engagement_rate=0.0, last_posted_at=None)

    def _sort_key(legacy: dict) -> datetime:
        return _parse_x_created_at(legacy.get("created_at", "")) or datetime.min.replace(tzinfo=timezone.utc)

    tweets.sort(key=_sort_key, reverse=True)
    sample = tweets[: config.X_ENGAGEMENT_RECENT_POSTS]

    interactions = 0
    newest_created_at: Optional[datetime] = None
    for legacy in sample:
        interactions += int(legacy.get("favorite_count", 0))
        interactions += int(legacy.get("retweet_count", 0))
        interactions += int(legacy.get("reply_count", 0))
        if config.X_ENGAGEMENT_INCLUDE_QUOTES:
            interactions += int(legacy.get("quote_count", 0))
        if config.X_ENGAGEMENT_INCLUDE_BOOKMARKS:
            # 「いいねだけでなくリプライ・リポスト・ブックマークが定期的について
            # いる」（モデリング基準）をengagement_rateへ反映するため2026-07-08追加。
            interactions += int(legacy.get("bookmark_count", 0))

        created_at = _parse_x_created_at(legacy.get("created_at", ""))
        if created_at and (newest_created_at is None or created_at > newest_created_at):
            newest_created_at = created_at

    engagement_rate = round((interactions / followers) * 100, 2)
    last_posted_at = newest_created_at.isoformat() if newest_created_at else None

    return RecentEngagement(engagement_rate=engagement_rate, last_posted_at=last_posted_at)
