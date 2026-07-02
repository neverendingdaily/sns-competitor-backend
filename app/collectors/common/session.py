from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional, Sequence

import requests

logger = logging.getLogger(__name__)


def load_cookies(path: str, required_names: Sequence[str]) -> Optional[dict[str, str]]:
    """Cookie-Editor等のブラウザ拡張がエクスポートするJSON配列
    (`[{"name","value","domain","path",...}]`)を読み込む。

    `required_names`に列挙した名前が全て揃っていれば`{name: value}`を返す。
    未設定・ファイル無し・JSON不正・必須Cookie欠如など、いかなる理由でも
    例外は投げずNoneを返す（＝呼び出し元は非認証モードで継続する）。
    Cookieの内容は絶対にログ出力しない。
    """
    if not path:
        return None

    try:
        raw = Path(path).read_text(encoding="utf-8")
        records = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(
            "Cookieファイルの読み込みに失敗しました（内容はログしません）: %s",
            type(exc).__name__,
        )
        return None

    if not isinstance(records, list):
        logger.warning("Cookieファイルの内容がJSON配列ではありません")
        return None

    cookie_values: dict[str, str] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        value = record.get("value")
        if isinstance(name, str) and isinstance(value, str):
            cookie_values[name] = value

    missing = [name for name in required_names if name not in cookie_values]
    if missing:
        logger.warning("Cookieファイルに必須項目が見つかりません: %s", ", ".join(missing))
        return None

    return cookie_values


def build_session(
    cookies: dict[str, str],
    *,
    domain: str,
    headers: Optional[dict[str, str]] = None,
) -> requests.Session:
    """Cookie辞書からrequests.Sessionを構築する。"""
    session = requests.Session()
    for name, value in cookies.items():
        session.cookies.set(name, value, domain=domain)
    if headers:
        session.headers.update(headers)
    return session
