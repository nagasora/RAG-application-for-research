# PaperPilot

論文を安全に取り込み、根拠付き検索・比較・研究ノート・リサーチギャップ抽出を行う研究支援アプリです。

初めて利用する方は、[PaperPilot 利用説明書](docs/USER_GUIDE.md)を参照してください。論文登録、根拠付き質問、比較、整理、知識グラフ、権限、トラブル対応を画面に沿って説明しています。

## 主な機能

- PostgreSQL + Alembic によるトランザクション永続化
- SHA-256 によるワークスペース単位の重複防止
- OIDC または明示的なローカル開発認証
- owner / editor / viewer のワークスペース権限
- サイズ・件数・ページ数制限付きのファイル別取り込み
- inline または Celery + Redis による取り込みジョブと進捗表示
- PDF / TXT / Markdown の原本保存、ページ・チャンク・引用根拠ビューア
- OpenAPIから生成するTypeScript API型と統一エラー処理
- キーワード検索、根拠付き回答、論文比較、Research Gap抽出
- タグ、論文ノート、検索履歴、比較結果保存
- BibTeX / RIS / CSVエクスポート
- オプションの日本語・英語OCR、表の構造化、図・caption抽出
- OpenAI APIキー未設定時のローカル抽出回答

## ローカル起動

### 1. PostgreSQL

```powershell
cd backend
docker compose up -d postgres
```

Celery取り込みとOCRも使う場合はRedisとworkerを起動します。

```powershell
docker compose up -d postgres redis bootstrap worker beat
```

### 2. バックエンド

```powershell
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python -m alembic upgrade head
python -m uvicorn app.main:app --reload --port 8000
```

`.env.example` はローカル用に `AUTH_MODE=dev` を指定します。本番環境では `AUTH_MODE=oidc` とし、issuer、audience、JWKS URLを設定してください。OCRは既定で無効です。

### 3. フロントエンド

```powershell
cd frontend
corepack pnpm install --frozen-lockfile
Copy-Item .env.local.example .env.local
corepack pnpm dev
```

ブラウザは `http://localhost:3000`、API仕様は `http://localhost:8000/docs` です。

## OpenAPI型の更新

バックエンドのPydantic契約を変更した後に実行します。

```powershell
cd frontend
corepack pnpm run openapi:generate
corepack pnpm run lint
```

生成物は `frontend/openapi/paperpilot.json` と `frontend/lib/api/schema.d.ts` です。

## 検証

```powershell
cd backend
python -m pytest tests -q

cd ..\frontend
corepack pnpm run lint
corepack pnpm run build
```

## データとプライバシー

- 原本と抽出アセットは `backend/data/` 以下へ保存され、Git対象外です。
- APIキー、OIDC設定、開発用トークンをコミットしないでください。
- OpenAI APIキーを設定すると、検索で選ばれた論文抜粋が回答生成のためモデルへ送信されます。
- 検索履歴は既定で質問・対象論文・引用だけを保存します。回答全文を保存する場合のみ `SEARCH_HISTORY_STORE_ANSWER=true` を指定します。

詳細なバックエンド設定は [backend/README.md](backend/README.md) を参照してください。
