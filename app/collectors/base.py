from abc import ABC, abstractmethod

from app.models import Account, Platform, SearchParams


class BaseCollector(ABC):
    platform: Platform

    @abstractmethod
    def search(self, params: SearchParams) -> list[Account]:
        ...

    @abstractmethod
    def get_account(self, account_id: str) -> Account:
        ...
