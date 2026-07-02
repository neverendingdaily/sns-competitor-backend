from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

import requests

from app.collectors.common.net import polite_get
from app.collectors.x import cache, graphql
from app.collectors.x.graphql import CookieAuthError
from app.errors import UpstreamUnavailableError
from app.models import Account

logger = logging.getLogger(__name__)

# 実サイトで確認済み(2026-07時点)のog/twitterメタタグ構造:
#   og:title / og:description(=bio) / og:image は存在する。
#   フォロワー数・フォロー数はメタタグに一切含まれない(ログイン無しでは非公開)。
#   twitter:label1/twitter:data1 のペアが "Posts" ラベルの時のみ投稿数が取れる。
# 存在しない/凍結されたusernameでもHTTPステータスは常に200(クライアントレンダリングSPAのため
# サーバー側で404にならない)。og:site_nameしか無くog:titleが欠落するのが「未発見」の唯一のシグナル。
#
# 認証済みセッション(session引数)がある場合はまずGraphQL(UserByScreenName)経由で
# 実際のフォロワー数・フォロー数を取得する。x.comのプロフィールページはSPAであり、
# それらの数字はクライアントJSがページ読み込み後にGraphQL APIを呼んで初めて画面に
# 出るものなので、非ログイン・ログイン済みを問わず初期HTML(metaタグ)には含まれない。
# そのため「metaタグ取得にCookieを足すだけ」では数字は取得できず、GraphQL経由の
# 別経路が必須となる。


def _parse_count(raw: str) -> int:
    raw = raw.strip().upper().replace(",", "")
    multiplier = 1.0
    if raw.endswith("K"):
        multiplier, raw = 1_000.0, raw[:-1]
    elif raw.endswith("M"):
        multiplier, raw = 1_000_000.0, raw[:-1]
    elif raw.endswith("B"):
        multiplier, raw = 1_000_000_000.0, raw[:-1]
    try:
        return int(float(raw) * multiplier)
    except ValueError:
        return 0


def fetch_profile(username: str, session: Optional[requests.Session] = None) -> Optional[Account]:
    """usernameのプロフィールを取得する。存在しない場合はNone、接続不能な場合は例外を送出する。

    `session`（認証済みセッション）が渡された場合はまずGraphQL経由での取得を試み、
    実際のフォロワー数・フォロー数を取得する。Cookieが拒否された場合(CookieAuthError)
    のみ、既存の非認証metaタグスクレイピングにフォールバックする。
    """
    cached = cache.get(username)
    if cached is not None:
        return cached

    if session is not None:
        try:
            profile = graphql.fetch_profile_via_graphql(username, session)
        except CookieAuthError:
            logger.warning(
                "Xのcookieが拒否されました。非認証モードにフォールバックします。"
                "X_COOKIES_PATHの更新（再ログイン＋再エクスポート）とバックエンド再起動が必要です"
            )
        else:
            if profile is None:
                return None

            account = profile.account
            # エンゲージメント率の取得はプロフィール取得とは別の失敗ドメインとして扱う。
            # 失敗してもここで取れた実データ(フォロワー数等)は活かし、
            # engagement_rate=0.0のまま返す（非認証metaタグへの格下げはしない）。
            try:
                engagement = graphql.fetch_recent_tweets(profile.rest_id, account.followers, session)
            except graphql.UserTweetsError:
                logger.warning("直近ツイートの取得に失敗しました。engagement_rateは0.0のままにします: %s", username)
            else:
                update: dict = {"engagement_rate": engagement.engagement_rate}
                if engagement.last_posted_at:
                    update["last_posted_at"] = engagement.last_posted_at
                account = account.model_copy(update=update)

            cache.set(username, account)
            return account

    account = _fetch_profile_via_meta_tags(username)
    if account is not None:
        cache.set(username, account)
    return account


def _fetch_profile_via_meta_tags(username: str) -> Optional[Account]:
    url = f"https://x.com/{username}"
    response = polite_get(url)
    if response is None:
        raise UpstreamUnavailableError(f"x.com is unreachable while fetching '{username}'")
    if response.status_code == 404:
        return None
    if not response.ok:
        raise UpstreamUnavailableError(f"x.com returned {response.status_code} for '{username}'")

    html = response.text
    meta: dict[str, str] = {}
    for key, value in re.findall(
        r'<meta\s+(?:property|name)="([^"]+)"\s+content="([^"]*)"', html, re.IGNORECASE
    ):
        meta[key] = value

    if "og:title" not in meta:
        # 存在しない/凍結されたアカウント（HTTPステータスは200のまま）
        return None

    posts_count = 0
    if meta.get("twitter:label1", "").strip().lower() == "posts":
        posts_count = _parse_count(meta.get("twitter:data1", "0"))

    display_name = meta.get("og:title", username).split("(")[0].strip() or username

    return Account(
        id=username,
        platform="x",
        username=username,
        display_name=display_name,
        bio=meta.get("og:description", ""),
        # フォロワー数・フォロー数はX側が非ログインの公開メタタグとして公開しておらず、
        # ヘッドレスブラウザ/ログインを使わない今回の方針では取得不可能なため0固定。
        # 既知の制約としてREADMEにも明記。
        followers=0,
        following=0,
        posts_count=posts_count,
        engagement_rate=0.0,
        is_verified=False,
        avatar_url=meta.get("og:image", ""),
        profile_url=url,
        category="",
        last_posted_at=datetime.now(timezone.utc).isoformat(),
    )
