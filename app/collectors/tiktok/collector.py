from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from app import config
from app.collectors.base import BaseCollector
from app.collectors.common.quality_gate import passes_universal_quality_gate
from app.collectors.tiktok import discovery, profile_fetch
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)


class TikTokCollector(BaseCollector):
    platform = "tiktok"

    def search(self, params: SearchParams) -> list[Account]:
        if params.max_results == 0:
            # 呼び出し元がこのプラットフォームの検索を明示的にスキップしたい場合
            # （フロントエンドのプラットフォーム別取得件数設定で0を指定した場合）。
            return []

        if params.query_type == "username":
            # ユーザー名が既知の場合はDiscovery（DDG）を経由せず直接候補にする。
            # YouTubeの_lookup_by_handle相当（app/collectors/youtube.py参照）。
            username = params.query.strip().lstrip("@")
            candidates = [username] if username else []
        else:
            limit = params.max_results if params.max_results is not None else config.TIKTOK_DISCOVERY_MAX_CANDIDATES
            candidates = discovery.discover_candidates(params.query, limit)
        if not candidates:
            return []

        hydrated: list[tuple[Account, Optional[int]]] = []
        with ThreadPoolExecutor(max_workers=config.TIKTOK_HYDRATE_CONCURRENCY) as executor:
            futures = {
                executor.submit(profile_fetch.fetch_profile, username): username for username in candidates
            }
            for future in as_completed(futures):
                username = futures[future]
                try:
                    result = future.result()
                except UpstreamUnavailableError:
                    logger.warning("skipping candidate %s: tiktok.com unreachable", username)
                    continue

                if result is not None:
                    hydrated.append(result)

                if len(hydrated) >= config.TIKTOK_SEARCH_TARGET_COUNT:
                    for pending in futures:
                        pending.cancel()
                    break

        # 投稿ゼロ・フォロワー不足・FF比1.0未満・スパムキーワード等の全プラット
        # フォーム共通の品質ゲート（`_apply_filters`のユーザー指定条件とは別、常時適用）。
        # さらにTikTok固有として、Brave推測で総いいね数が取得できた場合に限り
        # 「フォロワーに対していいねが極端に少ない」アカウントも除外する。
        accounts = [
            account
            for account, likes in hydrated
            if passes_universal_quality_gate(
                account, min_followers=config.TIKTOK_MIN_FOLLOWERS, min_ff_ratio=config.TIKTOK_MIN_FF_RATIO
            )
            and self._passes_likes_ratio(account, likes)
        ]
        return self._apply_filters(accounts, params)

    @staticmethod
    def _passes_likes_ratio(account: Account, likes: Optional[int]) -> bool:
        # 総いいね数が推測できなかった場合はこのチェック自体をスキップし、
        # フォロワー数・FF比・投稿数のみで判定する（フェイルソフト）。
        if likes is None:
            return True
        if account.followers <= 0:
            return True
        ratio = likes / account.followers
        return ratio >= config.TIKTOK_MIN_LIKES_FOLLOWER_RATIO

    def get_account(self, account_id: str) -> Account:
        result = profile_fetch.fetch_profile(account_id)
        if result is None:
            raise AccountNotFoundError(f"tiktok account '{account_id}' not found")
        account, _likes = result
        return account

    def _apply_filters(self, accounts: list[Account], params: SearchParams) -> list[Account]:
        filters = params.filters
        result = accounts

        if filters.followers_min is not None:
            result = [a for a in result if a.followers >= filters.followers_min]
        if filters.followers_max is not None:
            result = [a for a in result if a.followers <= filters.followers_max]
        if filters.engagement_min is not None:
            result = [a for a in result if a.engagement_rate >= filters.engagement_min]
        if filters.verified_only:
            result = [a for a in result if a.is_verified]
        if filters.category:
            result = [a for a in result if a.category == filters.category]

        return result
