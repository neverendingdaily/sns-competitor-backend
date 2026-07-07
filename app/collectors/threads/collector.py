from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import config
from app.collectors.base import BaseCollector
from app.collectors.common.quality_gate import passes_universal_quality_gate
from app.collectors.threads import discovery, profile_fetch
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)


class ThreadsCollector(BaseCollector):
    platform = "threads"

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
            limit = params.max_results if params.max_results is not None else config.THREADS_DISCOVERY_MAX_CANDIDATES
            candidates = discovery.discover_candidates(params.query, limit)
        if not candidates:
            return []

        accounts: list[Account] = []
        with ThreadPoolExecutor(max_workers=config.THREADS_HYDRATE_CONCURRENCY) as executor:
            futures = {
                executor.submit(profile_fetch.fetch_profile, username): username for username in candidates
            }
            for future in as_completed(futures):
                username = futures[future]
                try:
                    account = future.result()
                except UpstreamUnavailableError:
                    logger.warning("skipping candidate %s: threads.com unreachable", username)
                    continue

                if account is not None:
                    accounts.append(account)

                if len(accounts) >= config.THREADS_SEARCH_TARGET_COUNT:
                    for pending in futures:
                        pending.cancel()
                    break

        # 投稿ゼロ・フォロワー不足・FF比1.0未満・スパムキーワード等の全プラット
        # フォーム共通の品質ゲート（`_apply_filters`のユーザー指定条件とは別、常時適用）。
        accounts = [
            a
            for a in accounts
            if passes_universal_quality_gate(
                a, min_followers=config.THREADS_MIN_FOLLOWERS, min_ff_ratio=config.THREADS_MIN_FF_RATIO
            )
        ]
        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        account = profile_fetch.fetch_profile(account_id)
        if account is None:
            raise AccountNotFoundError(f"threads account '{account_id}' not found")
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
