from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from app import config
from app.collectors.base import BaseCollector
from app.collectors.x import discovery_search_engine, discovery_togetter, profile_scraper
from app.collectors.x import session as x_session
from app.errors import AccountNotFoundError, UpstreamUnavailableError
from app.models import Account, SearchParams

logger = logging.getLogger(__name__)


class XCollector(BaseCollector):
    platform = "x"

    def search(self, params: SearchParams) -> list[Account]:
        if params.query_type == "username":
            # ユーザー名が既知の場合はDiscovery（Togetter/DDG）を経由せず直接候補にする。
            # YouTubeの_lookup_by_handle相当（app/collectors/youtube.py参照）。
            username = params.query.strip().lstrip("@")
            candidates = [username] if username else []
        else:
            candidates = self._discover_candidates(params.query)
        if not candidates:
            return []

        session = x_session.get_session()
        accounts: list[Account] = []

        with ThreadPoolExecutor(max_workers=config.X_HYDRATE_CONCURRENCY) as executor:
            futures = {
                executor.submit(profile_scraper.fetch_profile, username, session): username
                for username in candidates
            }
            for future in as_completed(futures):
                username = futures[future]
                try:
                    account = future.result()
                except UpstreamUnavailableError:
                    logger.warning("skipping candidate %s: x.com unreachable", username)
                    continue

                if account is not None:
                    accounts.append(account)

                if len(accounts) >= config.X_SEARCH_TARGET_COUNT:
                    # 未着手のfutureのみキャンセルされる（実行中のものは最後まで走る）。
                    for pending in futures:
                        pending.cancel()
                    break

        return self._apply_filters(accounts, params)

    def get_account(self, account_id: str) -> Account:
        session = x_session.get_session()
        account = profile_scraper.fetch_profile(account_id, session)
        if account is None:
            raise AccountNotFoundError(f"x account '{account_id}' not found")
        return account

    # -- discovery -----------------------------------------------------

    def _discover_candidates(self, query: str) -> list[str]:
        limit = config.X_DISCOVERY_MAX_CANDIDATES

        with ThreadPoolExecutor(max_workers=2) as executor:
            togetter_future = executor.submit(
                self._safe_discover, "togetter", discovery_togetter.discover_candidates, query, limit
            )
            search_engine_future = executor.submit(
                self._safe_discover,
                "search engine",
                discovery_search_engine.discover_candidates,
                query,
                limit,
            )
            togetter_candidates = togetter_future.result()
            search_engine_candidates = search_engine_future.result()

        candidates: list[str] = []
        seen: set[str] = set()
        for username in [*togetter_candidates, *search_engine_candidates]:
            if username in seen:
                continue
            seen.add(username)
            candidates.append(username)
            if len(candidates) >= limit:
                break

        return candidates

    @staticmethod
    def _safe_discover(
        source: str, discover_fn: Callable[[str, int], list[str]], query: str, limit: int
    ) -> list[str]:
        try:
            return discover_fn(query, limit)
        except Exception:
            logger.exception("%s discovery failed for query=%s", source, query)
            return []

    # -- filters -------------------------------------------------------

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
