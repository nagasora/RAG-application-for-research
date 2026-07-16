# Render デプロイ（公開検証環境）

## この構成の対象

リポジトリ直下の `render.yaml` は、PaperPilot を **公開検証**するための最小構成です。

```text
ブラウザ → PaperPilot Web（Next.js） → PaperPilot API（FastAPI）
                                      ├→ Neon PostgreSQL
                                      └→ Cloudflare R2（原本・抽出アセット）
```

現在の `render.yaml` は PostgreSQL 接続文字列と R2 をRenderのEnvironmentで受け取る構成です。R2/S3互換ストレージアダプタは実装済みで、R2を設定すればRenderサービスの再起動・再デプロイで原本とアセットは失われません。ただし、Render Freeの休止、Neonのプラン制約、バックアップ/復元未検証、単一プロセスのbackground取込という制約があるため、研究データの本番運用としては使わないでください。

## 事前準備

1. GitHub にこのリポジトリを push する。
2. Render に GitHub を接続する。
3. 本番用の OIDC プロバイダを用意し、次を確認する。
   - issuer URL
   - API audience
   - JWKS URL
   - フロントエンド URL を redirect/origin として許可できること
4. OpenAI を使う場合は API key を用意する。キーはリポジトリや `NEXT_PUBLIC_*` に保存しない。

## Render での作成順序

`render.yaml` を含むブランチを選んで **New + / Blueprint** を作成します。このBlueprintはAPIサービスを作成します。PostgreSQLはNeonなど外部サービスで用意します。初回作成時、Render は `sync: false` の値を入力するよう促します。

1. NeonなどでPostgreSQLを用意し、APIの `DATABASE_URL` に接続文字列を設定する。
2. API の Environment で、OIDC値、R2の `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`、必要に応じて `OPENAI_API_KEY` を入力する。`PAPER_STORAGE_BACKEND=r2`、`R2_ACCOUNT_ID`、`R2_BUCKET`、`R2_PREFIX` はBlueprintの値と一致させる。
3. API が `https://paperpilot-api-<suffix>.onrender.com/api/health` で `{"status":"ok","database":"ok"}` を返すことを確認する。
4. Dashboard の **New + / Web Service** で同じリポジトリからフロントエンドを作成する。Language は Docker、Root Directory は `frontend`、Dockerfile Path は `Dockerfile` にする。
5. `paperpilot-web` の `NEXT_PUBLIC_API_URL` に、上記 API の **HTTPS公開URL（末尾スラッシュなし）** を入れ、`NEXT_PUBLIC_AUTH_MODE=oidc` を設定してデプロイする。Auth0 のSPAログインを使う場合は、さらに `NEXT_PUBLIC_AUTH0_DOMAIN`、`NEXT_PUBLIC_AUTH0_CLIENT_ID`、`NEXT_PUBLIC_AUTH0_AUDIENCE`（API Identifier）を設定する。
6. API の `FRONTEND_ORIGIN` を、作成されたフロントエンドの `https://paperpilot-web-<suffix>.onrender.com` に変更して再デプロイする。
7. OIDC プロバイダにも同じフロントエンド URL を登録する。

`NEXT_PUBLIC_*` は Next.js のビルド時に埋め込まれます。値を変更した際は Web Service で **Save, rebuild, and deploy** を選びます。公開変数へ OpenAI key、OIDC secret、DB URL を入れてはいけません。Auth0 の Domain、SPA Client ID、API Identifier は公開設定値であり、Client Secret は設定しません。

### Auth0 を使う場合の値

API の Environment:

```text
OIDC_ISSUER=https://<tenant>.us.auth0.com/
OIDC_AUDIENCE=<Auth0 API の Identifier>
OIDC_JWKS_URL=https://<tenant>.us.auth0.com/.well-known/jwks.json
```

Web の Environment:

```text
NEXT_PUBLIC_AUTH0_DOMAIN=<tenant>.us.auth0.com
NEXT_PUBLIC_AUTH0_CLIENT_ID=<SPA の Client ID>
NEXT_PUBLIC_AUTH0_AUDIENCE=<Auth0 API の Identifier>
```

Auth0 SPA の Application Settings では、Web の公開URLを `Allowed Callback URLs`、`Allowed Logout URLs`、`Allowed Web Origins`、`Allowed Origins (CORS)` に登録する。

## 最小構成の制約

- `INGESTION_MODE=background` です。アップロードはすぐに `202 Accepted` とジョブ ID を返し、PDF の抽出状況は `/api/jobs/{job_id}` で確認します。単一 API プロセス内の簡易キューのため、サービス再起動中のジョブは完了できません。本番の確実な再試行には Celery worker + Redis を使ってください。
- OCR は無効です。必要になったら、まず有料 worker と共有オブジェクトストレージへ移行します。
- Free Web Service は 15 分無アクセスで停止し、次のアクセス時には起動待ちが発生します。
- Free Web Service は persistent disk を利用できません。
- DBの保持期間・容量・バックアップは、接続先（例: Neon）のプランに従います。Render Freeの制約ではありません。
- `AUTH_MODE=oidc` のまま公開します。`AUTH_MODE=dev`、`NEXT_PUBLIC_DEV_USER`、開発トークンを公開環境に設定してはいけません。

## 継続運用へ移行する条件

論文データを残す、複数ユーザーで使う、OCRを使う、または取込を非同期化する前に、次を実施してください。

1. R2/S3のversioning、ライフサイクル、アクセスキーのローテーション、DBとオブジェクトの復元手順を検証する。
2. API、Celery worker、embedding worker、beat とキューを分離し、全プロセスに同じR2/S3設定を与える。
3. Postgres を有料プランへ移し、バックアップと復元を確認する。
4. カスタムドメイン、OIDCの本番redirect URL、CORSの `FRONTEND_ORIGIN` を固定する。

## 公式ドキュメント

- [Render Blueprints](https://render.com/docs/blueprint-spec)
- [Render Docker デプロイ](https://render.com/docs/docker)
- [Render 無料枠の制約](https://render.com/docs/free)
- [Render 環境変数](https://render.com/docs/configure-environment-variables)
