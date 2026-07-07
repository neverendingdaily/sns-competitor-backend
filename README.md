# sns-competitor-backend

SNS競合アカウント調査ツールのバックエンドAPI。フロントエンド（`20260702_sns-competitor-ui`）が期待する3つのRESTエンドポイントを実装する。

## セットアップ

```bash
cd 20260702_sns-competitor-backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # YOUTUBE_API_KEY を設定
```

## 起動

```bash
uvicorn app.main:app --reload --port 8000
```

フロントエンド側のSettingsページで `baseUrl=http://localhost:8000`、`useMock=false` に設定すると接続できる。

## エンドポイント

- `GET /api/v1/health`
- `POST /api/v1/accounts/search`
- `GET /api/v1/accounts/{platform}/{id}`

エラーは全て `{ "error": string }` 形式（フロントの `client.ts` が前提とする形）。

### `POST /api/v1/accounts/search` の `maxResults`（任意）

`SearchParams`に任意の`maxResults`（0〜50の整数）を指定すると、そのリクエストに限り発見（discovery）候補数の上限を環境変数の既定値（`X_DISCOVERY_MAX_CANDIDATES`等）から上書きできる。未指定時は環境変数の既定値を使う。**`0`を指定した場合はそのプラットフォームの検索処理自体を即座にスキップし（外部への通信は一切発生しない）、空配列を返す**。フロントエンドの「プラットフォーム別取得件数」設定から利用する想定。

## トラブルシューティング：結合テストでよくある症状

実機テストで頻出する3つの症状と、その原因・対応をまとめる（2026-07-02の実機調査で確認済み）。

| 症状 | 原因 | 対応 |
|---|---|---|
| YouTube検索/取得が`{"error": "YOUTUBE_API_KEY is not configured"}`（502）になる | `.env`に`YOUTUBE_API_KEY`が未設定（バグではなく意図した挙動） | `.env`にGoogle Cloud Consoleで取得したキーを設定してバックエンドを再起動 |
| Xの`engagementRate`が常に`0.0` | `X_GRAPHQL_USERTWEETS_ID`が未設定（プロフィール本体の取得とは独立した機能で、これだけフェイルソフトする設計。詳細は下記「エンゲージメント率」節） | `.env`に`X_GRAPHQL_USERTWEETS_ID`を追加設定（他のXトークンだけでは足りない点に注意） |
| Instagram/Threads/TikTokの検索（`queryType`が`keyword`/`hashtag`/`category`）が0件になる | これら3プラットフォームの候補発見（discovery）は`html.duckduckgo.com`（DuckDuckGo html-lite）に依存しており、**同サイトへの接続がネットワークレベルでブロックされている環境がある**（実機確認: `openssl s_client`で提示された証明書が`CN=internetpositif.id`という検閲/フィルタリングシステムのブロックページ証明書（期限切れ）で、DuckDuckGo自身の証明書ではなかった。`instagram.com`/`google.com`は同じ環境から正規の証明書が返る）。フロントエンドのAPI接続自体は問題ない（0件はバックエンドがfail-softで200 OK・空配列を返す設計のため、接続エラーとしては現れない） | `BRAVE_SEARCH_API_KEY`（Brave Search APIを第二の発見ソースとして使う。`.env.example`に取得手順あり）または`SERPAPI_API_KEY`（SerpAPI、補助）を設定する。または`DISCOVERY_PROXY_URL`（DDG宛のみのオプトインプロキシ）を設定する。詳細は下記「Discovery：DuckDuckGo遮断への対応」参照。**Google Custom Search API(`GOOGLE_CSE_API_KEY`)は2026年1月に新規プロジェクトへの提供が終了しており、新規キーでは常に403になるため使用不可**。`queryType=username`（ユーザー名の完全一致検索）であればDiscoveryを経由しないため、これらを設定しなくても影響を受けない（下記「ユーザー名検索（queryType=username）はDiscoveryを経由しない」参照） |
| Instagram検索・X検索がRender上で応答が返らない／結果が0件に見える（2026-07-07追記） | `BRAVE_SEARCH_API_KEY`自体は正しく機能しており`site:{platform}.com`のクエリも既に組み込まれている。真因は`app/collectors/common/net.py`の`_reserve_slot`が、`max_concurrency`（同時実行数）を上げても予約列がバケット単位で1本しかなく開始時刻の間隔が縮まらない実装だったこと。そのため候補数が多いプラットフォーム（旧既定値: X=45件×GraphQL2回、Instagram=30件）ではハイドレーション（プロフィール取得）だけで100秒超かかり、Renderの実行時間上限（約120秒）を超えて接続が切られ、フロントエンドには0件・応答なしとして見えていた | `_reserve_slot`を`max_concurrency`で待ち時間を分担するよう修正し、`X_DISCOVERY_MAX_CANDIDATES`の既定値を45→3、`INSTAGRAM_DISCOVERY_MAX_CANDIDATES`を30→10に削減（`.env.example`参照）。Render側で`X_DISCOVERY_MAX_CANDIDATES`/`INSTAGRAM_DISCOVERY_MAX_CANDIDATES`を独自に設定している場合はダッシュボード側の値も見直すこと |

いずれの症状も`.env`未設定または外部サイト側の理由によるもので、アプリのコード自体に起因する不具合ではないことを確認済み。

### ユーザー名検索（`queryType=username`）はDiscoveryを経由しない

フロントエンドの検索フォームには「ユーザー名」モード（`queryType=username`）があり、この場合はexact matchのユーザー名が分かっているため、x/tiktok/instagram/threadsの`search()`はDiscovery（Togetter/DDG）を呼ばず、入力値をそのまま候補として直接プロフィール取得（hydration）に進む（`youtube.py`の`_lookup_by_handle`と同じ設計）。入力の先頭に`@`が付いていても取り除いてから使う。これにより、Discoveryが利用できない状況（上記のDDGブロック等）でも、ユーザー名が分かっているアカウントの検索だけは影響を受けない。`keyword`/`hashtag`/`category`検索は引き続きDiscoveryに依存する。

## プラットフォームごとの実装状況

| Platform | 状態 | 方式 |
|---|---|---|
| youtube | 実装済み | YouTube Data API v3（公式） |
| x | 実装済み | Togetter巡回＋検索エンジン経由の候補発見 → x.comプロフィールページの個別スクレイピング（非公式・fail-soft設計）。Cookie設定時は認証済みGraphQL経由で実フォロワー数・エンゲージメント率も取得 |
| tiktok | 限定実装 | 公式oEmbed経由で存在確認・表示名のみ取得。`followers`等の統計は`0`固定（詳細は下記「TikTokモジュールの制約」） |
| instagram | 実装済み（非認証のみでフル実装） | 非ログインの内部API`web_profile_info`経由でフォロワー数・フォロー数・投稿数・bio・認証バッジ・engagement_rateまで取得。Cookie設定時はブロック耐性向上・非公開フォロー中アカウント閲覧の任意アップグレード（詳細は下記「Instagramモジュールの制約・実装方式」） |
| threads | 限定実装 | 非ログインでは実在/非実在を判別するシグナルが一切無いことを実サイトで確認済み。「構文が妥当なusernameは疑わしきは実在として扱う」保証フロア層のみ、`followers`等の統計は常に`0`固定（詳細は下記「Threadsモジュールの制約」） |

## YouTube収集の制約

- `isVerified` は公開APIに存在しないため常に `false`。
- `category` はクリーンな公開フィールドが無く、検索フィルタで指定された値をそのまま返すか空文字。
- `engagementRate` は直近5本の動画の平均(いいね+コメント)/再生数から算出する近似値で、YouTube公式の指標ではない。
- クォータ目安：デフォルト10,000 units/日、`search.list` は1回100 units、`channels.list`/`playlistItems.list`/`videos.list` は概ね1 unit。

## Xモジュールの制約・レイテンシ

### 発見（Discovery）とページネーション

- 候補発見はTogetterの検索結果ページから個別まとめ記事（`togetter.com/li/{id}`）へのリンクを辿り、記事内に埋め込まれたツイートの投稿者を抽出する2段階クロールで実装（実サイトで構造確認済み）。検索エンジン経由（DuckDuckGo HTML lite）は補助ソースだが、確認時点で`html.duckduckgo.com`への接続がTLSハンドシェイクの時点で失敗していた。**2026-07-02の追加調査で、これはDuckDuckGo自身の証明書の期限切れではなく、`openssl s_client`で確認したところ`CN=internetpositif.id`という検閲/フィルタリングシステムのブロックページ証明書（期限切れ）が返ってきており、このバックエンドが動いているネットワーク環境でduckduckgo.com自体がネットワークレベルでブロックされていることが原因と判明した**（`instagram.com`等は同じ環境から正規の証明書が返る。こちらの実装の問題ではなく、環境依存の外部要因）。Togetter・検索エンジンいずれのソースが落ちてもフェイルソフトに空リスト扱いになり、検索全体はエラーにならない（xはTogetterという第二の発見源を持つため、DDGのみに依存するinstagram/threads/tiktokより影響を受けにくい。詳細は上記「トラブルシューティング」参照）。
- 20〜50件規模の検索結果を返すため、Togetter検索結果ページ（`X_TOGETTER_MAX_PAGES`）・記事スキャン数（`X_TOGETTER_MAX_ARTICLES`）・DuckDuckGo（`X_DDG_MAX_PAGES`）それぞれでページネーションを行う。**Togetterの`?page=N`、DuckDuckGoの`s`オフセットは実装時点の推測であり、実サイトでの検証を推奨する。** 想定と異なるスキームだった場合でも「新規結果が見つからなければ停止」という設計のため、致命的には壊れず1ページ目のみの取得（旧来の動作）に自然に縮退する。
- 候補数は「最終的に返したい件数」(`X_SEARCH_TARGET_COUNT`、既定30)と「取得失敗を見込んで多めに発見しておく候補数」(`X_DISCOVERY_MAX_CANDIDATES`、既定45)に分離されている。

### 認証済みGraphQL経由でのフォロワー数・フォロー数取得（任意機能）

- **重要な技術的背景**：x.comのプロフィールページはクライアントレンダリングSPAで、フォロワー数・フォロー数はページ読み込み後にブラウザのJSがX内部のGraphQL API（`UserByScreenName`）を呼んで初めて表示される値。**非ログイン・ログイン済みを問わず初期HTML（metaタグ）には一切含まれない**ため、単純に「metaタグ取得にCookieを足すだけ」では取得できない。
- そのため、`X_COOKIES_PATH`・`X_WEB_BEARER`・`X_GRAPHQL_USERBYSCREENNAME_ID`（すべて`.env.example`に取得手順を記載）を設定すると、`app/collectors/x/graphql.py`が認証済みセッションで`UserByScreenName`を直接呼び出し、実際の`followers`/`following`/`isVerified`等を取得する。
- **これらの環境変数が未設定の場合は今まで通り非認証モードで動作し、`followers`/`following`は`0`固定**（フェイルソフト。ここより下の「未設定時の制約」を参照）。
- Cookieが失効・拒否された場合（`CookieAuthError`）も、検索全体をエラーにはせず自動的に非認証モードへフォールバックする。ログに`WARNING`が出るので、その場合はブラウザから再ログイン・Cookie再エクスポート・`X_COOKIES_PATH`更新・**バックエンド再起動**（セッションはプロセス内でキャッシュされるため）が必要。
- **利用規約・アカウント凍結リスク（重要）**：認証済みセッションを自動化してXの内部APIを呼ぶ行為はX利用規約に抵触し得ます。自分自身のアカウントを使う個人用調査ツールという前提で実装していますが、サブアカウントの利用・控えめな`X_HYDRATE_CONCURRENCY`/`X_API_JITTER_*`設定を推奨します。
- `queryId`（`X_GRAPHQL_USERBYSCREENNAME_ID`）や必須ヘッダ・bearerトークンはXが定期的に変更します。認証パスが急に失敗し始めた場合はdevtoolsで再確認してください。
- **将来案（Tier 3・未実装）**：X側がGraphQL APIを塞いだ場合の代替として、Playwright等のヘッドレスブラウザでSPAを実際にレンダリングして取得する方式が考えられるが、20〜50件規模では1件あたり数秒のレンダリングコストがかかり件数増加という目標と相反するため、今回は採用していない。

### エンゲージメント率（`UserTweets`経由、任意機能）

- 認証済みセッションが有効な場合、`app/collectors/x/graphql.py`の`fetch_recent_tweets`がX内部GraphQL(`UserTweets`)を追加で呼び出し、直近`X_ENGAGEMENT_RECENT_POSTS`件（既定5件）の投稿から`engagement_rate = round((いいね+RT+リプライ(+引用)) / followers * 100, 2)`を算出する。取得手順は`.env.example`参照（`X_GRAPHQL_USERTWEETS_ID`をdevtoolsで確認して設定）。
- 純粋なリツイート（他人のツイートの再共有）はエンゲージメント対象から除外する（そのツイート自体のいいね等は元投稿者のものであり、このアカウントのエンゲージメントではないため）。引用ツイートを含めるかは`X_ENGAGEMENT_INCLUDE_QUOTES`（既定true）で切替可能。
- **`X_GRAPHQL_USERTWEETS_ID`が未設定、またはUserTweets呼び出しが失敗した場合は`engagement_rate=0.0`のままフェイルソフトする。** 重要な設計上の判断として、この失敗はプロフィール本体の取得（フォロワー数等）には一切影響しない — 既に成功したGraphQLプロフィール取得を非認証metaタグ取得へ格下げすることはない。
- **レイテンシへの影響**：候補1件あたりの認証GraphQL呼び出しが1回→2回に倍増するため、上記「コールドサーチ30〜90秒」という見積もりは**60〜120秒程度**に上振れする可能性がある。1時間のプロフィールキャッシュがヒットしていれば再検索時の追加コストはない。

### 未設定時（非認証モード）の制約

- `X_COOKIES_PATH`等が未設定の場合、`bio`（og:description）・投稿数（`twitter:data1`、labelが"Posts"の時のみ）・表示名・アバター画像のみ取得できる。`followers`/`following`は常に`0`、`isVerified` / `category` / `engagementRate` / `lastPostedAt`も固定値・近似値のまま。
- 上記制約により、`followersMin`等のフィルタを非認証モードでXの検索に使うと常に空振りする点に注意。同じ理由で下記「品質フィルタ」のフォロワー数下限も、`followers=0`（取得不可のセンチネル値）はチェック対象外にしている。

### フォロワー数・フォロー数の推測フォールバック（Brave Searchスニペット解析）

`followers=0`かつ`following=0`（非認証metaタグ取得等による「取得不可」のセンチネル値）のまま返ってきたアカウントに限り、`app/collectors/x/follower_estimate.py`の`estimate_counts`がBrave Search API（`{username} x.com followers following`で検索）の検索結果スニペット（`title`/`description`）を正規表現で解析し、「55 Following · 27 Followers」「12.3万フォロワー」のような記述から数値を推測して補完する（`app/collectors/x/profile_scraper.py`の`fetch_profile`から呼び出し）。

- 無関係なページ（他アカウントの言及・別プラットフォームの集計サイト等）の数値を誤って拾わないよう、本人のプロフィールURL（`x.com/{username}`等）に紐づく検索結果のみを対象にする。
- `BRAVE_SEARCH_API_KEY`未設定、またはスニペットに該当する記述が見つからない場合はNoneを返し、`followers`/`following`は`0`のままフェイルソフトする（100%の精度は保証されない推測値である点に注意）。
- 呼び出しは`bucket="brave-search-api:x-follower-estimate"`として他プラットフォームのdiscovery用Brave呼び出しとは別のレート制御バケットで管理される（`X_FOLLOWER_ESTIMATE_JITTER_MIN/MAX`・`X_FOLLOWER_ESTIMATE_CONCURRENCY`、詳細は`.env.example`参照）。

### 品質フィルタ（スパム・放置アカウントの足切り）

`XCollector._is_quality_account`が`search`（一覧検索）の結果にのみ常時適用する（`filters`によるユーザー指定の絞り込みとは別）。アフィリエイトのモデリング対象として不適切なアカウントを除外する目的で、以下をすべて満たすアカウントのみ残す:

- フォロワー数が`X_MIN_FOLLOWERS`（既定100）以上。**ただし`followers=0`は「非認証モードで取得できなかった」センチネル値であり実際のフォロワー0人と区別できないため、この下限チェックの対象外**（`X_COOKIES_PATH`未設定のRender環境等で全件が誤って弾かれ検索結果が0件に見える不具合の再発防止、2026-07-07修正）
- **FF比（フォロワー数÷フォロー数）が1.0以上**（フォロワー数・フォロー数が共に取得・推測できている場合のみ判定。フォロワー数がフォロー数を下回るアカウントは、フォローバック狙いで大量フォローしている一般・スパムアカウントとみなして除外する。2026-07-08追加）
- **フォロワー数・フォロー数がBrave推測を含めても一切取得できなかった（0/0のままの）場合は、投稿数が1件以上あり、かつ自己紹介文に典型的なスパムキーワード（`XCollector.SPAM_BIO_KEYWORDS`参照。「相互フォロー」「フォロバ100」「副業で稼」等）が含まれていないこと**（判定材料が実質無いアカウントに対する最後の足切り。2026-07-08追加）
- プロフィール自己紹介（`bio`）が空でない
- アバター画像がデフォルト（タマゴアイコン）でない
- 最終投稿から`X_MAX_INACTIVE_DAYS`（既定180）日以内に活動している

### レイテンシとキャッシュ

- プロフィール取得はホスト単位のセマフォ＋開始時刻予約方式（`app/collectors/common/net.py` — 元はX専用だったが全プラットフォーム共通基盤に一般化済み）で並列化されており（既定`X_HYDRATE_CONCURRENCY=5`）、認証済みGraphQL経由では旧来の「1件ずつ4〜8秒待つ」方式より大幅に高速。ただしTogetter/DuckDuckGoなど小規模サイトへの配慮から発見（Discovery）フェーズの同時実行数は増やしていないため、**コールドサーチ（キャッシュ無し・20〜50件規模）は30〜90秒程度かかり得る**（旧来の「15〜25秒」という記載は候補数3件時点のものであり、件数増加に伴い上方修正。エンゲージメント率算出も有効な場合はさらに上振れ、上記参照）。
- 取得成功したプロフィールは`X_PROFILE_CACHE_TTL`秒（既定3600秒＝1時間）だけプロセス内にキャッシュされる（`app/collectors/x/cache.py`、実体は`app/collectors/common/cache.py`の`TTLCache`）。同一ユーザー名の再検索・再取得はキャッシュがヒットしている間ほぼ即時に返る。

### その他

- 個別アカウント取得（`GET /accounts/x/{id}`）の `id` はX内部IDではなくusernameをそのまま使う簡易設計（ユーザー名変更時はidも変わる）。

## 共通基盤（`app/collectors/common/`）

元々X専用だった以下のモジュールは、プラットフォームを問わず使える形に一般化されている。新規プラットフォームを追加する際はこれらを再利用し、重複実装しないこと。

- `net.py` — `polite_get(url, *, session, headers, timeout, interval_range, max_concurrency, bucket, proxies)`。ホスト/バケット単位のセマフォ＋開始時刻予約によるポライトなスクレイピング基盤。`proxies`は呼び出し元が明示的に渡した場合のみそのリクエストに使われる（環境変数による暗黙のプロキシ検出には依存しない設計。下記「Discovery：DuckDuckGo遮断への対応」参照）
- `session.py` — `load_cookies(path, required_names)` / `build_session(cookies, domain, headers)`。ブラウザ拡張エクスポート形式のCookie読み込みと`requests.Session`構築
- `cache.py` — `TTLCache(ttl_seconds)`。username→AccountのシンプルなTTLキャッシュ
- `discovery_search_engine.py` — `discover_via_search_engine(query, *, site, username_pattern, reserved_paths, max_pages, limit, proxies, ...)`。DuckDuckGo html-lite経由の`site:`検索＋ページネーションによる候補ユーザー名発見
- `discovery_brave.py` — `discover_via_brave(query, *, site, username_pattern, reserved_paths, max_pages, limit, api_key, ...)`。Brave Search API経由の`site:`検索＋ページネーションによる候補ユーザー名発見（`api_key`未設定時は即座に空リストを返すフェイルソフト設計）。DDGへの接続がブロックされている環境向けの第二の発見ソース
- `discovery_serpapi.py` — `discover_via_serpapi(query, *, site, username_pattern, reserved_paths, max_pages, limit, api_key, ...)`。SerpAPI(Google engine)経由の`site:`検索＋ページネーションによる候補ユーザー名発見（`api_key`未設定時は即座に空リストを返すフェイルソフト設計）。DDG/Brave両方が使えない環境向けの第三の発見ソース
- `discovery_google_cse.py` — **【廃止・未使用】** Google Custom Search JSON API経由の実装。同APIが2026年1月に新規プロジェクトへの提供を終了した(既存プロジェクトも2027-01-01に完全終了予定)ため、新規発行のAPIキーでは常に403(accessNotConfigured)になり利用不能。上記`discovery_brave.py`/`discovery_serpapi.py`に置き換え済みで、どのdiscovery.pyからも呼び出していない（参照実装として残置。ファイル削除はワークスペースルールにより別途承認が必要）

`app/collectors/x/net.py`・`session.py`・`cache.py`・`discovery_search_engine.py`は、上記共通基盤を呼び出す薄いラッパー（X固有のBearer/CSRF組み立てなど）として残っている。

## Discovery：DuckDuckGo遮断への対応

instagram/threads/tiktokの候補発見（discovery）はDDGのみに依存していたため、DDGがネットワークレベルでブロックされている環境（上記「トラブルシューティング」参照）では検索が0件になっていた。これに対応するため、各プラットフォームの`discovery.py`の`discover_candidates()`は、DDG（既存）・Brave Search API・SerpAPIを`ThreadPoolExecutor`で並列実行し、結果をdedupe-mergeするマルチソース設計になっている（`app/collectors/x/collector.py`の`_discover_candidates`が採用しているTogetter+DDGの統合パターンと同じ形）。各ソースは個別にtry/exceptでラップされており、一部が失敗・未設定でも他の結果だけで検索は継続する。

**【重要】Google Custom Search API（旧・第二の発見ソース）は廃止済み**：同APIは2026年1月に新規プロジェクトへの提供を終了し(既存プロジェクトも2027-01-01に完全終了予定)、新規発行のAPIキーでは`.env`/`GOOGLE_CSE_CX`を正しく設定しても常に403(`accessNotConfigured`/`This project does not have the access to Custom Search JSON API.`)が返り利用不能。以下のBrave Search API・SerpAPIに置き換え済み。

- **Brave Search API（`BRAVE_SEARCH_API_KEY`、任意・既定無効、推奨・主軸）**：`api.search.brave.com`は実機確認でこの種のISPレベル遮断の影響を受けていないことを確認済み（DDGとは別ドメインのため構造的に回避できる）。DDGとはページネーション方式（`offset`パラメータ、0始まりページ単位）・レスポンス形式（構造化JSON `web.results[].url`、HTML正規表現ではない）が異なる点に注意。無料枠・レート制限はプラン改定されうるため契約時にダッシュボードで最新情報を確認すること。取得手順は`.env.example`参照。
- **SerpAPI（`SERPAPI_API_KEY`、任意・既定無効、補助）**：Google検索結果を高精度に取得できるが無料枠は少なく(月100件程度)、実運用では従量課金前提。DDGとはページネーション方式（`start`パラメータ、0始まり結果件数単位）・レスポンス形式（構造化JSON `organic_results[].link`）が異なる点に注意。取得手順は`.env.example`参照。
- **`DISCOVERY_PROXY_URL`（任意・既定無効、緊急退避用）**：DDG宛のリクエストにのみ明示的に渡すオプトインHTTPプロキシ。他プラットフォーム（instagram.com・x.com・tiktok.comのoEmbed等）の通信には一切使わない。共有・データセンター系プロキシの出口IPは他テナントと共有されており、DDG側から見て既にレート制限/ブロック対象になっている可能性がある点に注意（`net.py`が担保する「自分のペースを守る」礼儀正しさの前提が崩れうる）。上記のBrave Search APIの方が構造的に堅牢なため基本はそちらを推奨し、こちらは補助的な位置づけ。
- 複数設定した場合はDDG（プロキシ経由）・Brave・SerpAPIの候補がすべてdedupe-mergeされる。

## TikTokモジュールの制約（限定実装）

- **プロフィールページはWAFで保護されている（実サイトで確認済み・2026-07時点）**：`https://www.tiktok.com/@{username}`へ非ログインで`requests.get`すると、Akamai/SlardarのWAFチャレンジページ（「Please wait...」の中身の無いHTML）が返り、統計情報を含むSSR埋め込みJSONは一切取得できない。ブラウザ相当のヘッダーを付与しても変化なし。プランで想定していた「非ログインでSSR JSONを取得」という方式は**現状動作しない**。
- 代わりに、TikTok公式の**oEmbedエンドポイント**（埋め込みカード生成用、非WAF・非ログインで動作）経由で、アカウントの存在確認（実在なら200、存在しないと400を返すことを実サイトで確認済み）と表示名（`author_name`）のみを取得する限定実装になっている。
- **`followers` / `following` / `postsCount` / `engagementRate` / `bio` / `avatarUrl`は常に`0`または空のまま。** `followersMin`等のフィルタをTikTokの検索に使うと常に空振りする点に注意（Xの非認証モードと同じ制約）。
- Cookie認証（`sessionid`等）を使えばWAFを回避してSSRページから実データが取得できる可能性はあるが、実際のTikTok Cookieが無い環境では検証できておらず**未実装**（`app/collectors/common/session.py`の`load_cookies`/`build_session`はそのまま再利用できる設計になっている）。エンゲージメント率算出も同様に未実装。
- 候補発見（discovery）はDuckDuckGo html-lite経由の`site:tiktok.com`検索のみ（Togetterのような第三者インデックスサイトの代替は無い）。

## Instagramモジュールの制約・実装方式

- **実サイト確認済み(2026-07-02時点)**：非ログインの`GET https://www.instagram.com/{username}/`はSPAの空シェルを返すのみでmetaタグにデータが埋め込まれておらず、Xのようなmetaタグ方式は通用しない。一方、内部API`GET https://www.instagram.com/api/v1/users/web_profile_info/?username={username}`（ヘッダ`x-ig-app-id: 936619743392459`必須。秘匿情報ではないIG公式Webクライアントの既知の固定ID）は**非ログインのままフォロワー数・フォロー数・投稿数・bio・認証バッジ・アバター・カテゴリに加え、直近12件の投稿のいいね数・コメント数・投稿日時まで**JSONで返すことを確認済み。ログイン壁には一度も当たらなかった。
- これにより`engagementRate`（直近`INSTAGRAM_ENGAGEMENT_RECENT_POSTS`件のいいね+コメント合計÷フォロワー数×100）・`lastPostedAt`も**Cookie無し・追加リクエスト無しで**算出できる。X（プロフィール取得とエンゲージメント取得が別の認証GraphQL呼び出し2回必要）より単純かつ豊富な非認証実装になっている。
- 存在しないユーザー名はHTML本文つきの404で判別可能。JSONが返ってきても`data.user`が欠けている場合（レート制限等）は「存在しない」扱いにせず`UpstreamUnavailableError`（502）にしている。
- **Cookie認証（`INSTAGRAM_COOKIES_PATH`、既定未設定＝無効）は必須機能の補完ではなく任意アップグレード**：同じ`web_profile_info`エンドポイントに`sessionid`を足すだけで、目的はブロック・レート制限のされにくさと非公開フォロー中アカウントの閲覧のみ。Cookieが拒否された場合は自動的に非認証モードへフォールバックする（ログにWARNING、`app/collectors/x/graphql.py`の`CookieAuthError`と同じフェイルソフト設計）。
- **利用規約・アカウント凍結リスク（Xより慎重に）**：Meta（Instagram/Threads運営元）はXよりもスクレイピング行為に対して積極的にアカウント凍結・法的措置（Bright Data/hiQ訴訟等）を取ってきた実績があるため、Cookie認証tierを有効化する場合はX以上に自己責任判断・控えめな設定を推奨する。自動投稿・大量アカウント運用等へのスコープ拡大はしない。
- **Instagram/Threadsは同一のMetaログイン基盤を共有**しており、`INSTAGRAM_COOKIES_PATH`と`THREADS_COOKIES_PATH`（実装した場合）は同じCookieエクスポートファイルを指してよい（2回ログイン・エクスポートする必要はない）。
- discovery（候補発見）は`instagram.com/{username}`という`@`プレフィックス無しのURL構造のため、`/p/`・`/reel/`・`/explore/`等の予約済みトップレベルパスとユーザー名が衝突する。`app/collectors/instagram/discovery.py`の`RESERVED_PATHS`に明示的に列挙して対処している（TikTok/Xの`RESERVED_PATHS`が空集合／少数で成立するのとは対照的）。
- **既知の制約**：本実装時点で`html.duckduckgo.com`のTLS証明書が期限切れで接続不能だったため（Xモジュールで報告済みの既知の環境問題）、`RESERVED_PATHS`はDDGの実検索結果ではなくInstagram公式の既知URL構造から列挙したもの。DDG復旧後に実際に混入する偽ユーザー名を観測したら追記すること。DDG探索バケットは他プラットフォームと共有のデフォルトバケット（`html.duckduckgo.com`、`max_concurrency=1`）に相乗りしており、プラットフォームをまたいだ直列化を意図的に許容している（体感速度が問題になれば`bucket="html.duckduckgo.com:instagram"`のような分離を検討）。
- 非公開アカウント（フォロー外）は投稿一覧が空になるため`engagementRate=0.0`・`lastPostedAt`は取得時刻にフォールバックする。プライベートアカウント・ビジネス/クリエイターカテゴリの細かな挙動差・高頻度リクエスト時の挙動は未検証。

## Threadsモジュールの制約（限定実装）

- **プロフィールページは完全なログイン壁で保護されている（実サイトで確認済み・2026-07-02時点）**：`https://www.threads.net/@{username}`は`https://www.threads.com/@{username}`へ301リダイレクトされる（threads.comが実サービスドメイン）。非ログインで`requests.get`すると、**実在アカウント（`@zuck`・`@mosseri`で確認）と意図的に存在しないユーザー名のいずれもHTTP 200を返し、レスポンス本文（約26万バイト）はほぼ同一の汎用SPAシェル**。`<title>`は常に`"Threads"`のみ、`og:title`/`og:description`/`og:image`等のOGPタグは一切無し（出現回数0）、さらにリクエストしたusername文字列自体がレスポンスHTML中に一度も出現しないことを確認した＝アカウント固有のサーバーサイドレンダリングが行われていない。埋め込まれた`<script type="application/json">`（29個）の中身もfeatureフラグ等の汎用ブートストラップ設定のみ（Web版Threadsの内部コードネームが`"Barcelona"`であることが判明したのみで、プロフィール統計は含まれない）。
- 結果として、**TikTokのoEmbed（200/400で存在確認可能）やXのog:title有無判定のような「実在/非実在を区別できるシグナル」がThreadsには一切存在しない**。TikTokより一段弱い保証フロア層として実装：ユーザー名が構文的に妥当（英数字・`.`・`_`、1〜30文字）であれば「疑わしきは実在として扱い」、接続確認のみ行った上で`displayName=username`・`followers`等は`0`/空のスタブAccountを返す。構文的に不正なユーザー名のみ`None`（→404）として扱える、唯一確認可能な「存在しない」ケース。
- **`followers` / `following` / `postsCount` / `engagementRate` / `bio` / `avatarUrl`は常に`0`または空のまま。** さらに、**存在しないユーザー名を指定してもTikTok/Xと異なり404にならずスタブAccountが返ってしまう点が既知の制約**（`followersMin`等のフィルタを使うと常に空振りする点も同様にTikTokの非認証モードと同じ制約）。
- 候補発見（discovery）はDuckDuckGo html-lite経由の`site:threads.net`検索のみ。ThreadsのURLは`/@username`型（TikTok/Xと同型）でInstagramのような予約パス衝突（`/p/`・`/reel/`等）が起きないことを確認済みのため、`RESERVED_PATHS`は空集合。
- **Cookie認証（Meta/Instagramと共有の`sessionid`）は未実装**：ThreadsアカウントはInstagramアカウントと同一のMetaログインを共有するため、実装する場合は`THREADS_COOKIES_PATH`を`INSTAGRAM_COOKIES_PATH`と同じCookieエクスポートファイルに向けてよい設計にすべき（2回ログイン・エクスポートする必要は無い）。ただし実際のMeta Cookieが無い環境のため、認証済みページのレンダリング形式（埋め込みJSON構造等）を検証できておらず未実装（TikTokのCookie認証が同じ理由で未実装なのと同様の判断）。**利用規約・アカウント凍結リスクについては、Xの認証Cookie運用よりさらに慎重な扱いを推奨する** — Meta系プラットフォーム（Instagram/Threads）はXよりスクレイピングに対して積極的にアカウント凍結・法的措置を取ってきた実績があるため、実装する場合もサブアカウント限定・控えめな同時実行数/間隔設定を強く推奨し、自動投稿・大量アカウント運用等へのスコープ拡大は行わないこと。

## 今後の拡張（フェーズ2）

- **Threadsのcookie認証**: 実際のMeta Cookieで動作検証してから追加実装する（`INSTAGRAM_COOKIES_PATH`と同じファイルを指せる設計を想定。上記「Threadsモジュールの制約」参照）。
- **TikTokのCookie認証・エンゲージメント算出**: 実際のTikTok Cookieで動作検証してから追加実装する。
- **DDG探索バケットの分離**: 現状tiktok/instagram/threadsの全プラットフォームがDuckDuckGo探索でデフォルトのホストバケット（`html.duckduckgo.com`、`max_concurrency=1`）を共有しており、プラットフォームをまたいで直列化されている。体感速度が問題になった場合のみ、プラットフォームごとに`bucket`を分離する変更を検討する。
- ルーター（`app/routers/accounts.py`）・スキーマ（`app/models.py`）は変更不要。
