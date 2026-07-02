from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

import requests

from app import config
from app.collectors.common import net
from app.collectors.instagram import cache
from app.errors import UpstreamUnavailableError
from app.models import Account

logger = logging.getLogger(__name__)

BUCKET = "instagram.com"

PROFILE_URL_TEMPLATE = "https://www.instagram.com/{username}/"
API_URL_TEMPLATE = "https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"

# 実サイト確認済み(2026-07-02時点、大規模公開アカウント instagram/natgeo 等で計7リクエスト):
#
# 1. 非ログインの`GET https://www.instagram.com/{username}/`は200を返すが、中身は
#    クライアントレンダリングSPAの空シェル(splash screenのみ)で、og:title/og:description
#    等のメタタグはサーバー側に一切埋め込まれていない。ログイン壁へのリダイレクトも
#    無いが、実質的に使えるデータが無い（Xの非認証metaタグ方式はInstagramでは通用しない）。
#
# 2. 一方、内部API `GET https://www.instagram.com/api/v1/users/web_profile_info/
#    ?username={username}` は非ログインでもJSONで実データを返す。ヘッダ
#    `x-ig-app-id: 936619743392459`（IG公式Webクライアントが使う既知の固定値、
#    秘匿情報ではなく単なるクライアント識別子）が必須で、無いと400
#    `{"message":"useragent mismatch","status":"fail"}`になることを確認済み。
#    ログイン壁には一度も当たらなかった。
#
# 3. レスポンスにはフォロワー数(edge_followed_by.count)・フォロー数(edge_follow.count)・
#    投稿数(edge_owner_to_timeline_media.count)・bio・認証バッジ(is_verified)・
#    アバターURL(profile_pic_url_hd)・カテゴリ(category_name)に加え、直近の投稿
#    （最大12件、edge_owner_to_timeline_media.edges）のいいね数(edge_liked_by.count)・
#    コメント数(edge_media_to_comment.count)・投稿日時(taken_at_timestamp)まで
#    含まれている。そのためengagement_rateもこの1回のリクエストだけで算出でき、
#    X（プロフィール取得とUserTweets取得の2段階が必須）やTikTok（統計情報自体が
#    oEmbedに存在しない）より豊富なデータが非認証・単一リクエストで取得できる。
#
# 4. 存在しないユーザー名は404（HTML本文つき、JSONではない）で判別可能。
#
# 5. レート制限・ブロックは確認時点(7リクエスト・数十秒)では発生しなかったが、
#    Meta側は将来いつでも塞ぐ/レート制限を強化する可能性があるため、TikTokと同様に
#    控えめな`INSTAGRAM_JITTER_MIN/MAX`・`INSTAGRAM_HYDRATE_CONCURRENCY`の既定値にし、
#    Xのmetaタグ経路が陥っていた「bucket/interval未指定でpolite_get()を呼ぶ」という
#    一貫性の欠如を踏襲せず、ここでは明示的にbucket/interval_rangeを渡している。
#
# Cookie認証(INSTAGRAM_COOKIES_PATH, session.py)は上記と同じエンドポイントに
# ログイン状態を足すだけの任意アップグレードとして実装している（README参照）。


class _CookieAuthError(Exception):
    """Cookieが失効・不正であるなど、Instagram側に認証状態を拒否されたことを示す内部例外。
    `x/graphql.py`の`CookieAuthError`と同じ役割: `ApiError`は継承せず、呼び出し元
    (`fetch_profile`)が非認証モードへフォールバックするための合図として使う。"""


def fetch_profile(username: str, session: Optional[requests.Session] = None) -> Optional[Account]:
    """usernameのプロフィールを取得する。存在しない場合はNone、接続不能・想定外の
    応答の場合は`UpstreamUnavailableError`を送出する。

    `session`（cookie認証済みセッション、任意・`INSTAGRAM_COOKIES_PATH`未設定なら常にNone）
    が渡された場合はまずそのセッションで取得を試み、Instagram側にCookieを拒否された
    場合(401/403)のみ非認証モードにフォールバックする
    （`app/collectors/x/profile_scraper.py`と同じフェイルソフト設計）。
    非認証モードでも`web_profile_info`は実データを返すため、cookie無し・未拒否の
    どちらの場合も最終的な取得内容の豊富さに大差は無い（README参照）。
    """
    cached = cache.get(username)
    if cached is not None:
        return cached

    if session is not None:
        try:
            account = _fetch_web_profile_info(username, session=session)
        except _CookieAuthError:
            logger.warning(
                "Instagramのcookieが拒否されました。非認証モードにフォールバックします。"
                "INSTAGRAM_COOKIES_PATHの更新（再ログイン＋再エクスポート）とバックエンド再起動が必要です"
            )
        else:
            if account is not None:
                cache.set(username, account)
            return account

    account = _fetch_web_profile_info(username, session=None)
    if account is not None:
        cache.set(username, account)
    return account


def _fetch_web_profile_info(username: str, session: Optional[requests.Session]) -> Optional[Account]:
    url = API_URL_TEMPLATE.format(username=quote(username))
    headers = {"x-ig-app-id": config.INSTAGRAM_IG_APP_ID, "Accept": "*/*"}

    response = net.polite_get(
        url,
        session=session,
        headers=headers,
        bucket=BUCKET,
        interval_range=(config.INSTAGRAM_JITTER_MIN, config.INSTAGRAM_JITTER_MAX),
        max_concurrency=config.INSTAGRAM_HYDRATE_CONCURRENCY,
    )
    if response is None:
        raise UpstreamUnavailableError(f"instagram.com is unreachable while fetching '{username}'")

    if response.status_code == 404:
        # 実サイトで確認済み: 存在しないユーザー名はHTML本文つきの404を返す
        return None
    if response.status_code in (401, 403):
        if session is not None:
            raise _CookieAuthError(f"instagram.com rejected the session (status={response.status_code})")
        raise UpstreamUnavailableError(
            f"instagram.com returned {response.status_code} for '{username}' (unauthenticated)"
        )
    if not response.ok:
        raise UpstreamUnavailableError(f"instagram.com returned {response.status_code} for '{username}'")

    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamUnavailableError(
            f"instagram.com web_profile_info returned a non-JSON response for '{username}'"
        ) from exc

    user = (payload or {}).get("data", {}).get("user") if isinstance(payload, dict) else None
    if not user:
        # JSON自体は返ってきたがuserが無い（レート制限等の`{"status":"fail"}`系エラーを
        # 含む）。404ではないため「存在しない」とは断定せず、502として呼び出し元に伝える。
        detail = payload.get("message", payload) if isinstance(payload, dict) else payload
        raise UpstreamUnavailableError(
            f"instagram.com web_profile_info returned no user data for '{username}': {detail}"
        )

    return _build_account(username, user)


def _build_account(username: str, user: dict) -> Account:
    followers = int((user.get("edge_followed_by") or {}).get("count", 0) or 0)
    following = int((user.get("edge_follow") or {}).get("count", 0) or 0)
    media = user.get("edge_owner_to_timeline_media") or {}
    posts_count = int(media.get("count", 0) or 0)

    engagement_rate, last_posted_at = _compute_engagement(media.get("edges") or [], followers)

    display_name = user.get("full_name") or username
    avatar_url = user.get("profile_pic_url_hd") or user.get("profile_pic_url") or ""
    category = user.get("category_name") or user.get("business_category_name") or ""

    return Account(
        id=username,
        platform="instagram",
        username=username,
        display_name=display_name,
        bio=user.get("biography", "") or "",
        followers=followers,
        following=following,
        posts_count=posts_count,
        engagement_rate=engagement_rate,
        is_verified=bool(user.get("is_verified", False)),
        avatar_url=avatar_url,
        profile_url=PROFILE_URL_TEMPLATE.format(username=username),
        category=category,
        last_posted_at=last_posted_at,
    )


def _compute_engagement(edges: list[dict], followers: int) -> tuple[float, str]:
    """直近投稿(最大`INSTAGRAM_ENGAGEMENT_RECENT_POSTS`件、`web_profile_info`が
    一度に返す最大12件から先頭N件をサンプリング)の(いいね+コメント)合計÷フォロワー数で
    engagement_rateを算出する（`app/collectors/x/graphql.py`の`fetch_recent_tweets`と
    同じ「サンプル合計÷分母」方式で、件数で平均は取らない）。

    非公開アカウント（フォロー外）等でedgesが空の場合や、フォロワー数が0以下の場合は
    engagement_rate=0.0にフェイルソフトする。last_posted_atは取得できた投稿のうち
    最新のtaken_at_timestampを使い、無ければ現在時刻にフォールバックする
    （X/TikTokの非認証フォールバックと同じ位置づけ、ただしInstagramは通常このパスに
    落ちない）。
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    if not edges:
        return 0.0, now_iso

    sample = edges[: config.INSTAGRAM_ENGAGEMENT_RECENT_POSTS]

    interactions = 0
    newest_ts: Optional[int] = None
    for edge in sample:
        node = edge.get("node") or {}
        interactions += int((node.get("edge_liked_by") or {}).get("count", 0) or 0)
        interactions += int((node.get("edge_media_to_comment") or {}).get("count", 0) or 0)
        ts = node.get("taken_at_timestamp")
        if isinstance(ts, int) and (newest_ts is None or ts > newest_ts):
            newest_ts = ts

    last_posted_at = (
        datetime.fromtimestamp(newest_ts, tz=timezone.utc).isoformat() if newest_ts is not None else now_iso
    )

    if followers <= 0:
        return 0.0, last_posted_at

    engagement_rate = round((interactions / followers) * 100, 2)
    return engagement_rate, last_posted_at
