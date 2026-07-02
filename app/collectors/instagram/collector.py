from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from app import config
from app.collectors.base import BaseCollector
from app.collectors.instagram import discovery, profile_fetch
from app.collectors.instagram import session as instagram_session
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)


class InstagramCollector(BaseCollector):
    platform = "instagram"

    def search(self, params: SearchParams) -> list[Account]:
        if params.query_type == "username":
            # ユーザー名が既知の場合はDiscovery（DDG）を経由せず直接候補にする。
            # YouTubeの_lookup_by_handle相当（app/collectors/youtube.py参照）。
            username = params.query.strip().lstrip("@")
            candidates = [username] if username else []
        else:
            candidates = discovery.discover_candidates(params.query, config.INSTAGRAM_DISCOVERY_MAX_CANDIDATES)
        if not candidates:
            return []

        # get_session()はプロセス内シングルトン。INSTAGRAM_COOKIES_PATH未設定ならNoneを返し、
        # profile_fetch.fetch_profileは非認証モード（それでも実データが取れる）で動作する。
        session = instagram_session.get_session()
        accounts: list[Account] = []

        with ThreadPoolExecutor(max_workers=config.INSTAGRAM_HYDRATE_CONCURRENCY) as executor:
            futures = {
                executor.submit(profile_fetch.fetch_profile, username, session): username
                for username in candidates
            }
            for future in as_completed(futures):
                username = futures[future]
                try:
                    account = future.result()
                except UpstreamUnavailableError:
                    logger.warning("skipping candidate %s: instagram.com unreachable", username)
                    continue

                if account is not None:
                    accounts.append(account)

                if len(accounts) >= config.INSTAGRAM_SEARCH_TARGET_COUNT:
                    # 未着手のfutureのみキャンセルされる（実行中のものは最後まで走る）。
                    for pending in futures:
                        pending.cancel()
                    break

        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        session = instagram_session.get_session()
        account = profile_fetch.fetch_profile(account_id, session)
        if account is None:
            raise AccountNotFoundError(f"instagram account '{account_id}' not found")
        return account

    # -- filters（x/tiktok/youtubeの各collectorと同じ内容。3箇所目の重複だが既存の
    # 独立実装方針を踏襲し、今回は共通化しない）------------------------------------

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
