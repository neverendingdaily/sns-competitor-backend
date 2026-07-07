from __future__ import annotations

from app.models import Account

# フォロワー数・フォロー数がどうしても取得・推測できなかった（=0のまま残った）
# アカウントに対してのみ実質的に効いてくる簡易スパムキーワードチェック
# （実フォロワー数等で判定できるアカウントは他の条件で既に判定されているため、
# これは判定材料が乏しい場合の最後の砦として使う）。
DEFAULT_SPAM_KEYWORDS = (
    "相互フォロー", "フォロバ100", "フォロバ最速", "全フォロバ", "即フォロバ",
    "副業で稼", "在宅で稼", "簡単に稼", "権利収入", "不労所得", "月収",
    "line@", "LINE@", "出会い系", "エロ動画", "アダルト動画", "裏垢",
)


def has_spam_signal(bio: str, keywords: tuple[str, ...] = DEFAULT_SPAM_KEYWORDS) -> bool:
    lowered = bio.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def passes_universal_quality_gate(account: Account, *, min_followers: int) -> bool:
    """全プラットフォーム（X/Threads/Instagram/TikTok/YouTube）共通の品質ゲート。
    アフィリエイトのモデリング対象として不適切な、投稿ゼロ・一般/スパムアカウントを
    除外する目的で、以下のいずれか1つでも該当すればFalse（除外）を返す。

    - 投稿数が0（=このアカウントが実際に活動している証拠がAPI/スニペット解析の
      いずれからも一切得られなかった）
    - フォロワー数が`min_followers`未満。**Brave推測を含めても取得できず0のまま
      だった場合もこの条件に該当し除外される**（2026-07-08の改修より前は
      「0＝取得不可のセンチネル値」として下限チェックの対象外にしていたが、
      「取得失敗＝無価値なアカウントとみなす」方向へ意図的に厳格化した）
    - FF比（フォロワー数÷フォロー数）が1.0未満（フォロワー数・フォロー数の
      両方が0より大きい値として取得・推測できている場合のみ判定する。
      フォローバック狙いで大量フォローしている一般・スパムアカウントの典型的な
      シグナル）
    - 自己紹介文に典型的なスパムキーワードが含まれる

    リンク切れ（HTTP異常・スニペットからの「ページが見つかりません」等の検知）は
    このゲートの手前、プロフィール取得の時点（各`<platform>/profile_fetch.py`等が
    Noneを返す）で弾かれるため、ここには到達しない。
    """
    if account.posts_count <= 0:
        return False
    if account.followers < min_followers:
        return False
    if account.followers > 0 and account.following > 0 and account.followers < account.following:
        return False
    if has_spam_signal(account.bio):
        return False
    return True
