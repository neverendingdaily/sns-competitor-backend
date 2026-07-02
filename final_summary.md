# 最終検証・総仕上げレポート

**実施日**: 2026-07-02
**対象**: `20260702_sns-competitor-backend`（バックエンド） / `20260702_sns-competitor-ui`（フロントエンド）

このレポートは、全5プラットフォーム（YouTube / X / TikTok / Instagram / Threads）のCollector実装、およびDuckDuckGo(DDG)ブロック回避ロジック（Google CSEフォールバック）の実装完了を受けて行った、最終結合テスト・ビルド検証の結果をまとめたもの。

---

## 1. 結合テスト結果（全5プラットフォーム × 検索/詳細取得）

バックエンドを`uvicorn app.main:app --port 8000`で実起動し、実際にHTTPリクエストを送って検証した（モック無し）。

### 1-1. 検索エンドポイント `POST /api/v1/accounts/search`

| Platform | queryType | クエリ | 結果 | 判定 |
|---|---|---|---|---|
| youtube | username | `@google` | `502 { "error": "YOUTUBE_API_KEY is not configured" }` | ✅ 想定通り（キー未設定時の意図した挙動） |
| x | username | `elonmusk` | `200 OK`（1件） | ✅ |
| tiktok | username | `tiktok` | `200 OK`（1件） | ✅ |
| instagram | username | `natgeo` | `200 OK`（1件） | ✅ |
| threads | username | `zuck` | `200 OK`（1件） | ✅ |
| youtube | keyword | `cooking` | `502`（同上） | ✅ 想定通り |
| x | keyword | `cooking` | `200 OK`（13件） | ✅（Togetter発見が機能） |
| tiktok | keyword | `cooking` | `200 OK`（**0件**） | ✅ フェイルソフト（原因は2章参照） |
| instagram | keyword | `cooking` | `200 OK`（**0件**） | ✅ フェイルソフト（同上） |
| threads | keyword | `cooking` | `200 OK`（**0件**） | ✅ フェイルソフト（同上） |

### 1-2. 詳細取得エンドポイント `GET /api/v1/accounts/{platform}/{id}`

| Platform | id | 結果 | 判定 |
|---|---|---|---|
| youtube | `UC_x5XG1OV2P6uZZ5FSM9Ttw` | `502`（APIキー未設定） | ✅ 想定通り |
| x | `elonmusk` | `200 OK`（followers=240,646,637等、実データ取得） | ✅ |
| tiktok | `tiktok` | `200 OK`（followers=0固定、oEmbed限定実装） | ✅ 設計通り |
| instagram | `natgeo` | `200 OK`（followers=269,145,556、bio等フル取得） | ✅ |
| threads | `zuck` | `200 OK`（followers=0固定、保証フロア層実装） | ✅ 設計通り |
| instagram | 存在しないID | `404 { "error": "... not found" }` | ✅ |
| x | 存在しないID | `404 { "error": "... not found" }` | ✅ |

**結論**：5プラットフォーム全てで、200 OK・適切なフェイルソフト（502/404/空配列）のいずれかを返すことを実機で確認。クラッシュ・500エラー・想定外の例外は一切発生しなかった。

---

## 2. DuckDuckGo回避フォールバック（Google CSE）のルーティング確認

### 実際に発生したログ（`keyword=cooking`検索時）

```
WARNING:app.collectors.common.net:request to https://html.duckduckgo.com/html/?q=site%3Ax.com%20cooking failed:
  HTTPSConnectionPool(... SSLCertVerificationError(1, '[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: certificate has expired'))
WARNING:app.collectors.common.discovery_search_engine:search engine discovery unavailable for site=x.com query=cooking (offset=0)
```
（instagram.com / tiktok.com / threads.net でも同様のログを確認）

### コードレビューによる裏付け

- `app/collectors/instagram/discovery.py`（tiktok/threadsも同型）の`discover_candidates()`は、`ThreadPoolExecutor(max_workers=2)`で **DDG発見(`_discover_via_ddg`) と Google CSE発見(`_discover_via_cse`) を並列実行**し、結果をdedupe-mergeする実装になっていることをコードで確認済み。
- `app/collectors/common/discovery_google_cse.py`の`discover_via_google_cse()`は、`api_key`/`cx`が空文字の場合**即座に空リストを返す**（45行目 `if not api_key or not cx: return []`）フェイルソフト設計。

### 今回の実行環境での実際の挙動

| ソース | 状態 | 理由 |
|---|---|---|
| DuckDuckGo (html.duckduckgo.com) | ❌ 到達不可 | TLSハンドシェイクの時点で失敗。証明書エラーの内容は「期限切れ」だが、README記載の`openssl s_client`調査により実体は`CN=internetpositif.id`という検閲/フィルタリングシステムのブロックページ証明書と判明済み（ネットワーク環境側の遮断） |
| Google CSE | ⚠️ 未検証（未設定） | `GOOGLE_CSE_API_KEY`/`GOOGLE_CSE_CX`が`.env`に未設定のため、コード上は正しく「即空リスト返却」にフェイルソフトしているが、**実際にCSE経由で候補が取得できることまでは今回のテストでは確認できていない**（APIキーが無いため） |

**結論**：並列ルーティングの「配線」自体はコード・ログの両面で正常に機能していることを確認。ただし、Google CSE側の実疎通確認（本当に`googleapis.com`から候補が返るか）は、有効なAPIキー/cxが未設定のため**今回のテストではカバーできていない**。これはバグではなく単純に未設定によるもの（3章のフェイルソフト動作绳と一致）。キー設定後に再度`keyword`/`hashtag`/`category`検索を行い、ログに`google cse`関連のWARNINGが出ないこと・候補数が0件から回復することを確認するのがおすすめ。

---

## 3. フロントエンドビルドテスト

```bash
cd 20260702_sns-competitor-ui
npm run build   # = tsc --noEmit && vite build
```

```
✓ 53 modules transformed.
dist/index.html                   0.60 kB │ gzip:  0.42 kB
dist/assets/index-Cxgx6bLb.css   13.42 kB │ gzip:  3.26 kB
dist/assets/index-DrVT5VDy.js   229.20 kB │ gzip: 71.99 kB
✓ built in 483ms
BUILD_EXIT_CODE=0
```

**結論**：TypeScript型エラー・ビルドエラーともに0件。`tsc --noEmit`を通過しているため、UIコンポーネントとバックエンドのレスポンス型（`Account`等）の間に型不整合は無い。

---

## 4. 現在のアーキテクチャの制約まとめ

| 項目 | 制約内容 |
|---|---|
| **YouTube** | `isVerified`は常に`false`（公開APIに存在しない）。`engagementRate`は直近5本の近似値。クォータ既定10,000 units/日、`search.list`のみ1回100 units消費 |
| **X（非認証時）** | `followers`/`following`は常に`0`。Cookie未設定だと`followersMin`等のフィルタが常に空振りする |
| **X（認証時）** | フォロワー数等はGraphQL(`UserByScreenName`)経由で取得可能だが、`X_GRAPHQL_USERTWEETS_ID`が別途無いと`engagementRate`は`0.0`のまま |
| **TikTok（WAF）** | プロフィールページはAkamai/Slardar系WAFで保護されており、非ログインでのSSR取得は**不可**（実サイト確認済み）。oEmbed経由で存在確認・表示名のみの限定実装。`followers`等は常に`0` |
| **Instagram** | 非ログインの内部API`web_profile_info`でフル実装（followers/bio/engagementRate等取得可）。唯一Cookie無しで統計まで取れるプラットフォーム |
| **Threads（ログイン壁）** | 非ログインでは実在/非実在を判別するシグナルが**一切存在しない**（実在・非実在どちらもHTTP 200・同一のSPAシェル）。「構文が妥当なusernameは疑わしきは実在として扱う」保証フロア層のみ。存在しないユーザー名でも404にならずスタブが返る既知の制約あり |
| **Discovery（DDG）** | 現在の実行環境で`duckduckgo.com`がネットワーク（ISP/検閲システム）レベルで遮断されている。Google CSEが唯一の構造的回避策（`googleapis.com`は遮断の影響を受けないことを確認済み） |
| **利用規約リスク** | X/Instagram/Threadsの認証済みCookie運用は各社利用規約に抵触し得る。特にMeta系(Instagram/Threads)はX以上に凍結・法的措置の実績があるため、有効化する場合は自己責任・サブアカウント推奨 |

---

## 5. ユーザーが次に手動で行うべきこと

### 必須（YouTube機能を有効化する場合）
1. [Google Cloud Console](https://console.cloud.google.com/apis/credentials) で「YouTube Data API v3」を有効化し、APIキーを発行
2. `.env`の`YOUTUBE_API_KEY=`に設定 → バックエンド再起動

### 推奨（Instagram/Threads/TikTokのkeyword/hashtag/category検索を有効化する場合）
3. DDGがネットワーク遮断されている環境のため、以下のいずれかを設定：
   - **推奨**: [Programmable Search Engine](https://programmablesearchengine.google.com/) で検索エンジンを作成し「ウェブ全体を検索」を有効化 → `GOOGLE_CSE_API_KEY` / `GOOGLE_CSE_CX` を`.env`に設定（無料枠1日100クエリ、実運用では有料枠$5/1000クエリを推奨）
   - 補助: `DISCOVERY_PROXY_URL`（DDG宛のみのオプトインプロキシ、緊急退避用）
   - ※ `queryType=username`（ユーザー名の完全一致検索）はこれらが無くても影響を受けない

### 任意（Xのフォロワー数・エンゲージメント率を実データ化する場合）
4. 自分のXアカウントでログインし、Cookie-Editor等で`x.com`のCookieをエクスポート → `X_COOKIES_PATH`に設定
5. devtoolsで`UserByScreenName`/`UserTweets`のGraphQLリクエストを確認 → `X_WEB_BEARER` / `X_GRAPHQL_USERBYSCREENNAME_ID` / `X_GRAPHQL_USERTWEETS_ID`を設定
   - 現在の`.env`は`X_COOKIES_PATH`/`X_WEB_BEARER`/`X_GRAPHQL_USERBYSCREENNAME_ID`は設定済みだが、**`X_GRAPHQL_USERTWEETS_ID`のみ未設定**のため、`engagementRate`は現状`0.0`のまま（実測でも確認済み）。設定する場合はdevtoolsでの追加取得が必要

### 任意（Instagramのブロック耐性向上・非公開フォロー中アカウント閲覧）
6. Meta(Instagram)アカウントでログイン → Cookieエクスポート → `INSTAGRAM_COOKIES_PATH`に設定（Threadsも同じファイルを流用可能）

### 未対応・将来拡張（今回は変更不要）
- TikTok/ThreadsのCookie認証は共に**未実装**（実際のCookieが無い環境のため検証不能。今後の拡張として保留）
- ルーター（`app/routers/accounts.py`）・スキーマ（`app/models.py`）は今回の検証で変更の必要性は確認されなかった

---

## 付記：セキュリティ上の注意

`.env`には既にX認証情報（`X_COOKIES_PATH`, `X_WEB_BEARER`, `X_GRAPHQL_USERBYSCREENNAME_ID`）が設定済みであることを確認した。これらは実際の認証情報のため、本レポートには値そのものを記載していない。`.gitignore`で`.env`が除外されていることを確認し、リポジトリにコミットしないよう改めて注意すること。
