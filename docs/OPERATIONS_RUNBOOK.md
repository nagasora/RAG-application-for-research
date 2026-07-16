# 運用Runbook

## Celery必須条件

本番の非同期取り込みは `INGESTION_MODE=celery`、Redis broker/result backend、API/worker/beat の同一DB設定、lease reaper の稼働を必須とする。inlineはローカル開発だけに使う。

## Retry・quota・cost

- ingestion/embedding は `.env.example` の最大試行回数とleaseを超えたらfailedにし、無限retryしない。
- upload上限、ページ数、OCR/asset上限、request deadlineを変更する際は負荷試験と費用見積りを記録する。
- provider 429/5xxは指数backoff、認証/入力失敗はretryしない。

## Backup restore

1. DB snapshot とimmutable original/assetのversionを同一時点へ復元する。
2. 読み取り専用で件数・hash・workspace境界を照合する。
3. stagingでingestion reaperと検索のsmoke test後、書込みを再開する。

## CI-017 部分失敗

原本削除、job enqueue、source importはDBだけの成功を信用しない。補償処理、outbox相当の再実行、lease reaperで孤立状態を検出し、手作業で消去する前に監査記録を残す。
