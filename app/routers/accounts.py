from fastapi import APIRouter, Depends

from app.auth import optional_bearer
from app.collectors.registry import get_collector
from app.models import Account, Platform, SearchParams

router = APIRouter()


@router.post("/api/v1/accounts/search", response_model=list[Account])
def search_accounts(params: SearchParams, _auth: None = Depends(optional_bearer)) -> list[Account]:
    collector = get_collector(params.platform)
    return collector.search(params)


@router.get("/api/v1/accounts/{platform}/{account_id}", response_model=Account)
def get_account(platform: Platform, account_id: str, _auth: None = Depends(optional_bearer)) -> Account:
    collector = get_collector(platform)
    return collector.get_account(account_id)
