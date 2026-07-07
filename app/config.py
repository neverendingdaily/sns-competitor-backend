import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
BACKEND_API_KEY = os.getenv("BACKEND_API_KEY", "")

# --- 全プラットフォーム共通の品質ゲート（2026-07-08、app/collectors/common/
# quality_gate.py・snippet_signals.py参照） ---
# アフィリエイトのモデリング対象として不適切な、投稿ゼロ・リンク切れ・一般/スパム
# アカウントを検索結果から一律で除外するための足切り。`filters`によるユーザー指定の
# 絞り込みとは別に、`search`（一覧検索）の結果へ常時適用する。
#
# フォロワー数がこの値未満のアカウントは除外する（プラットフォームごとに独立して調整可能）。
X_MIN_FOLLOWERS = int(os.getenv("X_MIN_FOLLOWERS", "100"))
INSTAGRAM_MIN_FOLLOWERS = int(os.getenv("INSTAGRAM_MIN_FOLLOWERS", "100"))
THREADS_MIN_FOLLOWERS = int(os.getenv("THREADS_MIN_FOLLOWERS", "100"))
TIKTOK_MIN_FOLLOWERS = int(os.getenv("TIKTOK_MIN_FOLLOWERS", "100"))
YOUTUBE_MIN_FOLLOWERS = int(os.getenv("YOUTUBE_MIN_FOLLOWERS", "100"))

# API等から直接データが取得できずfollowers/following/postsCountのいずれかが0のまま
# だった場合のみ、Brave Search APIの検索結果スニペット（title/description）から
# 数値やリンク切れの文言を推測・抽出するフォールバックを試みる
# （app/collectors/common/snippet_signals.py、各プラットフォームの
# `<platform>/snippet_estimate.py`が薄いラッパーとして呼び出す）。
# BRAVE_SEARCH_API_KEY未設定なら自動的に無効（フェイルソフト）。discovery用の
# Brave呼び出し（bucket=ホスト名）とは別バケットにして、他プラットフォームの
# discoveryと待ち時間を奪い合わないようにしている。
# 旧`X_FOLLOWER_ESTIMATE_*`（X専用実装時の変数名）が設定されていればそれを
# 後方互換のフォールバック値として使う。
SNIPPET_ESTIMATE_JITTER_MIN = float(
    os.getenv("SNIPPET_ESTIMATE_JITTER_MIN", os.getenv("X_FOLLOWER_ESTIMATE_JITTER_MIN", "1.0"))
)
SNIPPET_ESTIMATE_JITTER_MAX = float(
    os.getenv("SNIPPET_ESTIMATE_JITTER_MAX", os.getenv("X_FOLLOWER_ESTIMATE_JITTER_MAX", "2.0"))
)
SNIPPET_ESTIMATE_CONCURRENCY = int(
    os.getenv("SNIPPET_ESTIMATE_CONCURRENCY", os.getenv("X_FOLLOWER_ESTIMATE_CONCURRENCY", "3"))
)

# 後方互換のため残す旧設定（新規環境変数が未設定の場合のフォールバック値として使う）
X_SEARCH_MAX_CANDIDATES = int(os.getenv("X_SEARCH_MAX_CANDIDATES", "3"))
X_REQUEST_JITTER_MIN = float(os.getenv("X_REQUEST_JITTER_MIN", "4"))
X_REQUEST_JITTER_MAX = float(os.getenv("X_REQUEST_JITTER_MAX", "8"))

# 検索で最終的に返したい件数（フィルタ後の目標値）
X_SEARCH_TARGET_COUNT = int(os.getenv("X_SEARCH_TARGET_COUNT", "30"))
# 取得失敗・フィルタ除外を見込んで発見しておく候補数の上限。
# 候補1件につきGraphQL呼び出しを2回（プロフィール取得＋直近ツイート取得）行うため、
# 件数が多いほどRenderの実行時間上限（約120秒）を超えやすい。
# net.py側のmax_concurrency対応（同時実行数が実際にスループットへ反映される）
# 修正後は10件でも実行時間上限内に収まることを実機確認済み。
X_DISCOVERY_MAX_CANDIDATES = int(
    os.getenv("X_DISCOVERY_MAX_CANDIDATES", os.getenv("X_SEARCH_MAX_CANDIDATES", "10"))
)

# Togetter探索のページネーション設定
X_TOGETTER_MAX_PAGES = int(os.getenv("X_TOGETTER_MAX_PAGES", "5"))
X_TOGETTER_MAX_ARTICLES = int(os.getenv("X_TOGETTER_MAX_ARTICLES", "8"))

# DuckDuckGo html-lite探索のページネーション設定
X_DDG_MAX_PAGES = int(os.getenv("X_DDG_MAX_PAGES", "3"))

# プロフィール取得（ハイドレーション）の同時実行数
X_HYDRATE_CONCURRENCY = int(os.getenv("X_HYDRATE_CONCURRENCY", "5"))

# 認証済みX GraphQL APIへのリクエスト間隔（秒）。ブラウザの通常利用に近い短い間隔で良い
X_API_JITTER_MIN = float(os.getenv("X_API_JITTER_MIN", "0.5"))
X_API_JITTER_MAX = float(os.getenv("X_API_JITTER_MAX", "1.5"))

# Xの認証Cookie（auth_token/ct0を含むブラウザ拡張エクスポートJSON）へのパス。
# 空文字なら非認証モードで動作する（フォロワー数・フォロー数は0固定のまま）。
# Cookieはアカウント認証情報そのものなのでリポジトリ外のパスを推奨。
X_COOKIES_PATH = os.getenv("X_COOKIES_PATH", "")

# X Web版クライアントのbearerトークンとGraphQL UserByScreenNameのqueryId。
# devtoolsのNetworkタブで確認して設定する（Xが変更することがある）。
# 空文字の場合は認証モードを無効化する。
X_WEB_BEARER = os.getenv("X_WEB_BEARER", "")
X_GRAPHQL_USERBYSCREENNAME_ID = os.getenv("X_GRAPHQL_USERBYSCREENNAME_ID", "")
# UserTweets(直近ツイート取得)のqueryId。UserByScreenNameとは別に管理されており、
# 単独でローテーションしうる。取得手順はREADME参照。
X_GRAPHQL_USERTWEETS_ID = os.getenv("X_GRAPHQL_USERTWEETS_ID", "")

# エンゲージメント率の算出対象とする直近投稿数（YouTube収集のN=5に合わせる）
X_ENGAGEMENT_RECENT_POSTS = int(os.getenv("X_ENGAGEMENT_RECENT_POSTS", "5"))
# 引用ツイート(quote_count)をエンゲージメント数に含めるか
X_ENGAGEMENT_INCLUDE_QUOTES = os.getenv("X_ENGAGEMENT_INCLUDE_QUOTES", "true").strip().lower() == "true"

# --- Xの品質フィルタ（アフィリエイトのモデリングに適さないスパム/放置アカウントの足切り） ---
# フォロワー数の下限（X_MIN_FOLLOWERS）は上部「全プラットフォーム共通の品質ゲート」に移動済み。
# 最終投稿からこの日数を超えて経過している（=放置されている）アカウントは除外する（X固有）
X_MAX_INACTIVE_DAYS = int(os.getenv("X_MAX_INACTIVE_DAYS", "180"))

# プロフィール取得結果のキャッシュ有効期限（秒）
X_PROFILE_CACHE_TTL = int(os.getenv("X_PROFILE_CACHE_TTL", "3600"))

# --- TikTok ---
# 実サイト確認済み(2026-07時点): プロフィールページはWAFチャレンジで保護されており
# 非ログインでは取得不可。公式oEmbedエンドポイント(非WAF・非ログイン)経由で
# 存在確認・表示名のみを取得する限定実装（followers等の統計は0/空のまま。
# README「TikTokの制約」参照）。Cookie認証・エンゲージメント算出は未実装のため
# 対応する設定値もまだ追加していない（今後の拡張）。
TIKTOK_SEARCH_TARGET_COUNT = int(os.getenv("TIKTOK_SEARCH_TARGET_COUNT", "20"))
TIKTOK_DISCOVERY_MAX_CANDIDATES = int(os.getenv("TIKTOK_DISCOVERY_MAX_CANDIDATES", "30"))
TIKTOK_DDG_MAX_PAGES = int(os.getenv("TIKTOK_DDG_MAX_PAGES", "3"))
TIKTOK_HYDRATE_CONCURRENCY = int(os.getenv("TIKTOK_HYDRATE_CONCURRENCY", "3"))
TIKTOK_JITTER_MIN = float(os.getenv("TIKTOK_JITTER_MIN", "1"))
TIKTOK_JITTER_MAX = float(os.getenv("TIKTOK_JITTER_MAX", "3"))
TIKTOK_PROFILE_CACHE_TTL = int(os.getenv("TIKTOK_PROFILE_CACHE_TTL", "3600"))

# --- Instagram ---
# 実サイト確認済み(2026-07-02時点): 非ログインの内部API `web_profile_info`
# (x-ig-app-id ヘッダ必須、秘匿情報ではない既知の固定値)がフォロワー数・
# フォロー数・投稿数・bio・認証バッジ・直近投稿のいいね/コメント数まで
# ログイン壁無しで返すことを確認済み。X/TikTokと異なり非認証のみでフル実装
# （README「Instagramモジュールの制約・実装方式」参照）。
# INSTAGRAM_COOKIES_PATH等の認証ティアは必須機能ではなく任意のアップグレード
# （ブロック耐性向上・非公開フォロー中アカウント閲覧が目的）。
INSTAGRAM_SEARCH_TARGET_COUNT = int(os.getenv("INSTAGRAM_SEARCH_TARGET_COUNT", "20"))
# 候補数が多いほどハイドレーション（プロフィール取得）に時間がかかり、Renderの
# 実行時間上限（約120秒）を超えて検索結果が0件に見えてしまうため既定値を抑えている。
INSTAGRAM_DISCOVERY_MAX_CANDIDATES = int(os.getenv("INSTAGRAM_DISCOVERY_MAX_CANDIDATES", "10"))
INSTAGRAM_DDG_MAX_PAGES = int(os.getenv("INSTAGRAM_DDG_MAX_PAGES", "3"))
INSTAGRAM_HYDRATE_CONCURRENCY = int(os.getenv("INSTAGRAM_HYDRATE_CONCURRENCY", "3"))
INSTAGRAM_JITTER_MIN = float(os.getenv("INSTAGRAM_JITTER_MIN", "2"))
INSTAGRAM_JITTER_MAX = float(os.getenv("INSTAGRAM_JITTER_MAX", "5"))
INSTAGRAM_PROFILE_CACHE_TTL = int(os.getenv("INSTAGRAM_PROFILE_CACHE_TTL", "3600"))
# engagement_rate算出対象の直近投稿数（web_profile_infoが一度に返す最大12件から先頭N件）
INSTAGRAM_ENGAGEMENT_RECENT_POSTS = int(os.getenv("INSTAGRAM_ENGAGEMENT_RECENT_POSTS", "5"))
# IG公式Webクライアントが使う既知の固定ID（秘匿情報ではない。無いと400になる）
INSTAGRAM_IG_APP_ID = os.getenv("INSTAGRAM_IG_APP_ID", "936619743392459")
# 任意のcookie認証（sessionid）へのパス。空文字なら非認証モード（それでも実データは取得できる）。
# Instagram/Threadsは同一のMetaログイン基盤を共有するため、THREADS_COOKIES_PATHと
# 同じファイルを指してよい。Cookieはアカウント認証情報そのものなのでリポジトリ外のパスを推奨。
INSTAGRAM_COOKIES_PATH = os.getenv("INSTAGRAM_COOKIES_PATH", "")

# --- Threads ---
# 実サイト確認済み(2026-07-02時点): 非ログインではプロフィールページが実在/非実在を
# 判別可能なシグナルを一切返さない完全なログイン壁（TikTokのWAFより強い。
# TikTokはoEmbedで200/400の存在確認ができるが、Threadsにはその手段すら無い）。
# 「構文が妥当なusernameは疑わしきは実在として扱う」保証フロア層のみの実装
# （README「Threadsモジュールの制約」参照）。Cookie認証は未実装（今後の拡張）。
THREADS_SEARCH_TARGET_COUNT = int(os.getenv("THREADS_SEARCH_TARGET_COUNT", "20"))
THREADS_DISCOVERY_MAX_CANDIDATES = int(os.getenv("THREADS_DISCOVERY_MAX_CANDIDATES", "30"))
THREADS_DDG_MAX_PAGES = int(os.getenv("THREADS_DDG_MAX_PAGES", "3"))
THREADS_HYDRATE_CONCURRENCY = int(os.getenv("THREADS_HYDRATE_CONCURRENCY", "3"))
THREADS_JITTER_MIN = float(os.getenv("THREADS_JITTER_MIN", "1"))
THREADS_JITTER_MAX = float(os.getenv("THREADS_JITTER_MAX", "3"))
THREADS_PROFILE_CACHE_TTL = int(os.getenv("THREADS_PROFILE_CACHE_TTL", "3600"))

# --- Discovery（instagram/threads/tiktok共通）: DuckDuckGo遮断への対応 ---
# 実機確認済み(2026-07-02時点): このバックエンドが動くネットワーク環境で
# duckduckgo.comがISPレベルで遮断されている（README「トラブルシューティング」参照）。
# 両方とも既定空文字＝無効・独立してオプトイン。詳細は`.env.example`参照。

# 【廃止】Google Custom Search JSON APIは2026年1月に新規プロジェクトへの提供を終了し
# (既存プロジェクトも2027-01-01に完全終了予定)、新規発行のAPIキーでは常に403
# (accessNotConfigured)が返るため発見(discovery)ソースとして利用不能になった。
# `discovery_google_cse.py`は参照実装として残しているが、以下のBrave Search API /
# SerpAPIに置き換え済み（各discovery.pyからは呼び出していない）。

# DDGの第二の発見(discovery)ソースとしてBrave Search APIを使う場合に設定。
# 無料枠は月2,000クエリ程度(要最新確認)だが各検索が最大DDG_MAX_PAGES回分の
# クエリを消費しうる点はGoogle CSE時代と同様。取得手順は.env.example参照。
BRAVE_SEARCH_API_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "")

# DDGの第三の発見(discovery)ソースとしてSerpAPIを使う場合に設定。
# 無料枠は月100クエリ程度と少なく基本的に有料(従量課金)前提。取得手順は.env.example参照。
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY", "")

# DDGへの接続専用のオプトインHTTPプロキシ（緊急退避用・既定無効）。他プラットフォームの
# 通信には一切使わない（Cookie付き認証リクエストが意図せずプロキシを経由する事故を防ぐため、
# 環境変数HTTP_PROXY等によるrequestsの暗黙のプロキシ検出には依存しない設計）。
# 例: http://user:pass@host:port
DISCOVERY_PROXY_URL = os.getenv("DISCOVERY_PROXY_URL", "")

CORS_ALLOW_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOW_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

APP_VERSION = os.getenv("APP_VERSION", "0.1.0")
