# PaperPilot エージェント開発ガイド

## プロジェクト概要

PaperPilot は、論文の登録、根拠付き検索、論文比較、リサーチギャップ抽出を行う研究支援 MVP です。

- `backend/`: FastAPI。PDF/TXT/MD 取り込み、外部メタデータ取得、検索/RAG、比較、ギャップ抽出、JSON 永続化を担当する。
- `frontend/`: Next.js App Router + React + TypeScript + Tailwind。Library、Ask、Analysis の各 UI を担当する。
- 永続化は PostgreSQL + Alembic。認証は明示的な `dev` または OIDC、データ境界はワークスペース membership で決定する。
- 原本・OCR・表図抽出は ingestion job と immutable storage を通し、本番では Celery + Redis worker を利用できる。
- OpenAI API キーがない場合も、ローカル抽出フォールバックで動作する。

## マルチエージェント方針

複数領域にまたがる開発、独立して調べられる事項、実装後のレビューや検証は、原則としてサブエージェントへ委譲する。

1. 親エージェントが要件、依存関係、担当ファイルを整理する。
2. 調査は `codebase_explorer`、API・永続化は `backend_engineer`、検索/RAGは `rag_engineer`、UIは `frontend_engineer`、最終レビューは `quality_reviewer` を優先する。
3. 独立した読み取り作業は並列化する。並列実装は担当ファイルが重ならない場合に限る。
4. `backend/app/models.py`、API 契約、`frontend/app/page.tsx` などの共有境界は、一人の担当を決めてから編集する。
5. サブエージェントは担当範囲と検証結果を親へ返し、親が差分を統合して最終検証する。
6. 小さな一ファイル修正や、委譲コストが作業量を上回る場合は単独で進めてよい。

## 開発上の制約

- 既存のユーザー変更を保持し、関係のないファイルを変更しない。
- 日本語を含む全ファイルを UTF-8 として扱う。PowerShell の読み取りでは必要に応じて `-Encoding utf8` を指定する。
- `.env`、API キー、OIDC/開発用トークン、`backend/data/` 以下の原本・抽出アセットをコミットしない。
- `.env` ファイルは閲覧・編集しない。環境変数の設定例を変更する場合は `.env.example` のみ編集する。
- OpenAI の回答生成モデルは `gpt-5.4-nano` を使用し、`OPENAI_MODEL` の既定値と `.env.example` を一致させる。
- API の入出力を変える場合は、バックエンド DTO、フロントエンド型、エラー処理、SSE イベント、テストを一組として確認する。
- 外部 API と OpenAI を使うテストはモックし、通常の回帰テストをネットワークや秘密情報に依存させない。
- OIDC設定、worker、PostgreSQL migration、OCR外部CLIをローカルで検証していない場合は、本番確認済みと報告しない。
- フロントエンドの依存関係は `pnpm-lock.yaml` を基準にする。package manager や lockfile の整理は専用タスクとして行う。

## 検証コマンド

バックエンド（`backend/` で実行）:

```powershell
python -m py_compile app/main.py app/models.py app/rag.py app/store.py
python -m pytest tests -q
```

フロントエンド（`frontend/` で実行）:

```powershell
corepack pnpm install --frozen-lockfile
corepack pnpm exec tsc --noEmit
corepack pnpm build
```

依存関係が未導入で検証できない場合は、成功扱いにせず、未実行のコマンドと理由を最終報告に明記する。

## 完了条件

- 要求された挙動が実装され、関連する正常系・異常系が検証されている。
- API と UI の契約に不整合がない。
- RAG 回答の引用元、ページ、原文抜粋が入力データと対応している。
- 変更範囲に応じたテスト、型検査、ビルドの結果が報告されている。
- `quality_reviewer` の重大指摘が解消済み、または残存理由が明記されている。
