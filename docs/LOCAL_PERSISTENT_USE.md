# PaperPilot を個人PCで永続利用する

この手順は PaperPilot をネットへ公開せず、自分のPCだけで使い続けるためのものです。
論文原本・抽出アセットは `backend/data/`、検索・ノート・グラフなどのデータはローカル
PostgreSQL volume に保存されます。Docker Desktop を停止してもデータは消えません。

## 初回だけ行うこと

Docker Desktop を起動し、リポジトリ直下のPowerShellで次を実行します。

```powershell
docker compose -f docker-compose.local.yml up -d --build
```

設定ファイルは不要です。OpenAIを使いたい場合など、任意の設定だけをリポジトリ直下の
`.env` に置けます（Git管理されません）。`AUTH_MODE=dev`、`PAPER_STORAGE_BACKEND=local`、
`INGESTION_MODE=inline` はCompose側で固定されます。

この一つのComposeが PostgreSQL、API、フロントエンドを起動します。`http://localhost:3000` を開きます。API は `http://localhost:8000` にだけ待ち受けるため、
同じPC以外からはアクセスできません。

この構成の開発用認証は、同じPCの他プロセスから偽装できる前提です。個人専用のOSアカウントで
使い、他人と共有するPCやLANへ公開する用途には使用しないでください。

## 次回以降

Docker Desktop 起動後、次だけ実行します。

```powershell
docker compose -f docker-compose.local.yml up -d
```

## 停止・更新・バックアップ

一時停止は `docker compose -f docker-compose.local.yml stop` です。`docker compose -f docker-compose.local.yml down` でもデータvolumeは残りますが、
**`docker compose -f docker-compose.local.yml down -v` は実行しないでください**。PostgreSQLの全データが削除されます。原本・アセットのホスト側フォルダはこの操作では消えませんが、手動削除しないでください。

アプリを更新した後は、次でイメージを作り直します。migration はbootstrapコンテナが自動適用します。

```powershell
docker compose -f docker-compose.local.yml up -d --build
```

定期バックアップでは、`backend/data/originals` と `backend/data/assets` を安全なローカルドライブにコピーし、DBも書き出します。PostgreSQL volume（`paperpilot-local-postgres`）だけ、またはファイルだけを戻すと、論文レコードと原本が対応しなくなるため、同じ時点の両方を保管してください。

```powershell
docker compose -f docker-compose.local.yml exec -T postgres pg_dump -U paperpilot -d paperpilot > paperpilot-backup.sql
```

復元は空のローカルDBに対して `Get-Content paperpilot-backup.sql | docker compose -f docker-compose.local.yml exec -T postgres psql -U paperpilot -d paperpilot` を実行します。

## 外部送信を避ける設定

このローカルComposeは既定で `EMBEDDING_PROVIDER=local` であり、OpenAIキーを渡さない限り
OpenAI APIを使用しません。キーを設定すると、回答生成や埋め込みのため選択された本文抜粋が
OpenAI APIへ送信されます。

## これまでの公開デプロイを停止する

ローカル版が動作し、バックアップを作成できたことを確認してから、Render と Cloudflare の
公開サービスを停止してください。データを消す必要はありません。

- Render: 対象の Web Service を **Suspend Service** し、GitHub 連携の Auto Deploy も無効化します。
- Cloudflare Pages: 対象プロジェクトの本番デプロイを無効化またはプロジェクトを削除し、GitHub
  連携の自動ビルドも停止します。
- R2、Neon、Auth0 などの外部データ・認証基盤は、ローカルへの移行とバックアップを確認するまで
  削除しません。
