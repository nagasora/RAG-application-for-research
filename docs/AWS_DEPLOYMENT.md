# AWS デプロイ準備

## 結論

AWSだけでこのアプリを**恒久的に完全無料**で運用する方法はありません。現在のAWS Free Tierは新規アカウントではクレジット型・期限付きであり、常時起動するコンテナ、PostgreSQL、Redis、ファイル保存、OpenAI APIには上限超過後の料金が発生します。まずは支出アラートを設定し、公開検証環境と本番環境を分けます。

最初の公開検証には、単一EC2でフロントエンドと軽量バックエンドを動かす方式が最も実装差分が小さく、推奨です。対象は少人数・OCRなし・`INGESTION_MODE=inline` の検証用途です。Celery worker、Redis、RDS、S3を分離する本番構成は次段階にします。

## 無料に近い選択肢

| 方式 | 無料性 | このアプリへの適合 | 判断 |
| --- | --- | --- | --- |
| EC2 1台 + Docker Compose | 新規AWSアカウントのクレジット／Free Tierの範囲内のみ | 高い。現行コンテナをほぼそのまま使える | **公開検証に推奨** |
| Amplify Hosting + App Runner + RDS | クレジット・期限付き。App Runnerはアイドル時もメモリ課金 | フロントは適合、API・worker・共有保存の設計変更が必要 | 本番化の次段階 |
| Lightsail | Linux/Containerの一部バンドルが3か月無料 | 単一VMとしては簡単。ただし現行フル構成にはメモリ不足 | 短期デモ向け |
| Lambda + API Gateway | 一部の無料枠あり | SSE、PDF/OCR、Celeryを分離・再設計する必要 | 今回は非推奨 |

AWSの新規アカウントFree Planは最大6か月、またはクレジット消費までです。RDS・Amplify・Lightsail・App Runnerの適用条件はアカウント作成日とリージョンで変わるため、作成前にコンソールの **Explore AWS** で確認してください。

- [AWS Free Tier](https://aws.amazon.com/free/)
- [RDS Free Tier](https://aws.amazon.com/rds/free/)
- [Amplify pricing](https://aws.amazon.com/amplify/pricing/)
- [App Runner pricing](https://aws.amazon.com/apprunner/pricing/)
- [Lightsail pricing](https://aws.amazon.com/lightsail/pricing/)

## 今回追加した準備

- `frontend/Dockerfile`: Next.js standalone 出力を実行する本番用コンテナ
- `frontend/.dockerignore`: ローカルの依存関係・秘密ファイルをイメージから除外
- `next.config.ts`: standalone 出力を有効化

フロントエンドの `NEXT_PUBLIC_API_URL` は**ビルド時**に埋め込まれます。公開APIのHTTPS URLが決まってから、次のように再ビルドします。

```bash
docker build \
  --build-arg NEXT_PUBLIC_API_URL=https://api.example.com \
  --build-arg NEXT_PUBLIC_AUTH_MODE=oidc \
  -t paperpilot-frontend:latest frontend
```

`NEXT_PUBLIC_AUTH_MODE=dev` や開発ユーザー名を公開イメージへ入れてはいけません。

## 公開検証用の推奨構成

```text
利用者
  │ HTTPS
  ▼
EC2 (Docker Compose / Caddy or Nginx)
  ├─ Next.js frontend
  ├─ FastAPI API
  ├─ PostgreSQL
  └─ ローカル永続ボリューム（原本PDF・抽出アセット）
```

公開検証では OCR を無効にし、`INGESTION_MODE=inline` にします。小型インスタンスで Celery worker / embedding worker / beat を同時に動かすことはしません。PDF処理は同期で遅くなるため、用途を少人数の試験に限定します。

EC2を破棄すると、インスタンス内のローカル保存データは失われ得ます。EBSスナップショットを取り、論文原本をバックアップしてください。

## 本番化する場合の目標構成

```text
CloudFront / Amplify ── HTTPS ── Frontend
                                  │
ALB ── API (EC2/ECS) ── RDS PostgreSQL (private subnet)
              │
              ├─ ElastiCache Redis ── Celery worker / beat
              └─ S3（原本PDF・抽出アセット）
```

本番構成へ進む前に、次を確認します。S3互換ストレージアダプタは実装済みですが、AWSでの運用・復元を確認したことはまだありません。

1. `PAPER_STORAGE_BACKEND=s3`、`S3_ENDPOINT_URL`、`S3_BUCKET`、`S3_ACCESS_KEY_ID`、`S3_SECRET_ACCESS_KEY`、必要に応じて `S3_REGION` / `S3_PREFIX` をSecrets ManagerまたはSSMから設定する。バケットは非公開にし、原本・アセットは認可済みAPI経由でだけ提供する。
2. S3 versioning、ライフサイクル、暗号化、RDSバックアップと復元テスト
3. OIDC必須化（`AUTH_MODE=oidc`）、Secrets Manager または SSM Parameter Store で秘密情報を注入
4. API用・worker用の最小権限DBロール、TLS終端、レート制限、監視・ログ・アラーム
5. `FRONTEND_ORIGIN` を公開ドメインと完全一致させる。現状は単一Originのみ対応

## 必ず最初に行うコスト対策

1. Billing console で **Zero spend budget** と月額コスト予算を作成し、メール通知を設定する。
2. AWSリージョンを1つに固定する。
3. 不要なEC2、RDS、Elastic IP、NAT Gateway、Load Balancerを削除する。特にNAT Gatewayは無料枠向きではない。
4. OpenAI APIにはAWSとは別に利用料が発生するため、OpenAI側にも利用上限・アラートを設定する。

AWS BudgetsにはFree Tier超過を通知する Zero spend budget テンプレートがあります。通知は請求反映の遅れがあるため、課金を完全に停止する仕組みではありません。

- [Zero spend budget](https://docs.aws.amazon.com/cost-management/latest/userguide/budget-templates.html)
- [AWS Budgets cost budget](https://docs.aws.amazon.com/cost-management/latest/userguide/create-cost-budget.html)
