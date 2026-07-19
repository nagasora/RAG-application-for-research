# PaperPilot Decision Log

継続改善に関する非自明な判断を短く追記する。通常のバグ修正、文言変更、局所的なリファクタリングは記録しない。

記録対象:

- データモデルまたはAPIの破壊的変更
- security、privacy、認証、データ保持・削除
- LLM、外部provider、評価閾値
- 永続化、migration、監査証跡
- 研究上の「検証済み」状態や人間承認の定義
- 大きな運用コストまたはベンダーロックイン

## D-20260716-01 継続改善台帳をリポジトリ内Markdownで管理する

- Status: accepted
- Linked items: *
- Context: 改善案が会話だけに残ると、次回の実装開始時に優先順位・根拠・受入条件を復元できない。現時点では外部Issue trackerを正本とする運用は確認できない。
- Decision: `docs/CONTINUOUS_IMPROVEMENT.md` を唯一の優先順位・状態の正本にし、このファイルは非自明な判断だけを保持する。
- Alternatives: READMEへの分散記録、外部Issue tracker、JSON/YAML台帳。
- Consequences: Git履歴で変更理由を追える一方、状態更新は手動になる。補助スクリプトで形式と依存関係を検査する。
- Date: 2026-07-16

## D-20260716-02 根拠・推論・仮説を別の研究資産として扱う

- Status: accepted
- Linked items: CI-001, CI-004, CI-005, CI-008, CI-023
- Context: RAG制約だけでは新規アイデアが既存研究に引かれすぎる一方、自由生成を論文由来の事実と混在させると研究判断を誤る。
- Decision: Evidence、Inference、Hypothesisを明示的に分類し、自由発想は許可するがHypothesis Inboxと人間レビューを経て昇格させる。
- Alternatives: 全回答をsource限定にする、全回答でLLM一般知識を無区別に利用する。
- Consequences: DTOとUIは複雑になるが、創造性と監査可能性を同時に保てる。
- Date: 2026-07-16

## D-20260716-03 AI Scientistは人間参加の閉ループとして段階実装する

- Status: accepted
- Linked items: CI-004, CI-005, CI-009, CI-010, CI-023
- Context: 長期像は仮説・実験・結果による知識更新だが、現段階の引用・仮説・実験schemaでは完全自律の科学的妥当性を保証できない。
- Decision: AIは候補生成、反証探索、実験案作成を担当し、採用・棄却・実証済み状態への遷移は人間の理由付き判断を必須とする。
- Alternatives: 単発RAGに留める、完全自律エージェントを先に作る。
- Consequences: 自動化速度より研究上の責任境界を優先する。将来、自動化範囲を広げる場合も評価結果と別Decisionを必要とする。
- Date: 2026-07-16

## D-20260716-04 viewerは根拠検索を行えるが、モデルコストを伴う生成は行えない

- Status: accepted
- Linked items: CI-016
- Context: viewerにも論文・原文根拠を確認する導線は必要だが、検索リクエスト内の埋め込み、LLM回答、SSE生成、会話保存を許可すると、編集権限を持たない利用者が共有ワークスペースのモデル予算を消費できる。
- Decision: viewerにはローカル語句検索だけを返すread-only preview APIを提供する。embedding・LLM・SSE・会話/履歴への保存を伴う回答生成はowner/editorに限定し、APIが最終的な認可境界になる。UIもviewerに生成操作を提示しない。
- Alternatives: viewerの検索を全面禁止する、全検索をviewerにも許可して利用量だけを事後集計する。
- Consequences: viewerの検索品質は語句検索に限定されるが、根拠確認は維持され、モデルコストと状態変更は明確に編集者へ帰属する。
- Date: 2026-07-16

## D-20260716-05 新着文献は取得時点のスナップショットとして人間レビューへ送る

- Status: accepted
- Linked items: CI-012
- Context: Semantic Scholar APIの内容・レート制限・ライセンスは変更され得るため、取得結果をそのまま研究資産として自動採用すると再現性と利用条件の確認ができない。
- Decision: provider、license、rate limit policy、取得時刻、応答snapshot、原文引用をDiscoveryItemに保存し、全候補をpending review queueへ置く。Semantic Scholar API keyの導入レートは1 RPSとして設計し、通常テストはモックする。
- Alternatives: live API結果だけを表示する、自動でLibraryへ採用する。
- Consequences: 新着の取り込みは明示的なレビュー操作を要するが、根拠と外部providerの状態を後から確認できる。
- Date: 2026-07-16

## D-20260716-06 Idea参照の正規化前値と昇格時の原典を監査用に保持する

- Status: accepted
- Linked items: CI-008, CI-010
- Context: 初期Idea schemaにはanchorの外部キーと列挙制約がなく、既存環境に未知のkind/statusや孤立IDが存在し得る。またpaper削除時にはSource VersionとSpanも削除されるため、IDだけの昇格記録では後から原典を検証できない。
- Decision: migration 0023は不正値を制約適合値へ正規化する前に、全original値を`idea_integrity_migration_audit`へ退避する。audit行が存在する環境では、それを失うdowngradeを拒否する。Idea昇格時にはpaperのtitle/hash、Source Versionのlocator/hash、Spanの位置・原文、ResearchRunの目的・質問をHypothesis metadataへ値snapshotとして保存する。Experiment作成時もHypothesisの値snapshotを計画JSONへ保存する。
- Alternatives: 不正なlegacy rowがあればmigrationを全面停止する、孤立IDを無記録でNULL化する、削除対象を永久保持する。
- Consequences: migrationとsnapshotの保存量は増えるが、正規化と原典削除後にも判断根拠を監査できる。audit rowの解消・移管を行うまで0023以前へのdowngradeはできない。
- Date: 2026-07-16

## D-20260716-07 共同レビューのanchorと判断履歴を削除から保護する

- Status: accepted
- Linked items: CI-015
- Context: claim IDだけでは複数のvalidation artifact間で対象が曖昧になり、ResearchRunやEvidenceLinkの削除・migration downgradeでコメントと判断が消えるとレビュー監査を再現できない。Markdownへの生文字列展開は、見出しや偽の判断を注入してreport構造を壊し得る。
- Decision: claim thread作成時は唯一のimmutable validation artifact内に実在するclaimだけを許可し、artifact IDとclaim値snapshotを保存する。ResearchRun、RunArtifact、EvidenceLinkからreview threadへのanchor削除は`RESTRICT`し、review audit行がある0024 downgradeを拒否する。明示的なworkspace削除に伴うthread配下のcomment/decision削除は`CASCADE`とする。Markdown reportの動的値はHTML-safeなJSON literalまたは`pre` blockとして直列化する。
- Alternatives: 任意claim IDを許可する、anchor削除時にthreadを連鎖削除する、Markdownをraw interpolationする。
- Consequences: anchorを削除する前にレビュー記録の明示的な移管が必要になる。reportは装飾性より構造と監査安全性を優先する。
- Date: 2026-07-16

## D-20260716-08 request-time検索はportableな上限付きDB候補を先に作る

- Status: accepted
- Linked items: CI-014, CI-019
- Context: 回答ごとにworkspaceの全Paperと全ChunkをORMへhydrateし、JSON embeddingをPythonで全走査すると、データ量に比例してmemory・latencyが増える。一方、現行のSQLite開発環境とPostgreSQL本番の双方を通常テストで検証でき、pgvector未導入環境でも検索を停止させない必要がある。
- Decision: request-timeの粗候補はworkspace、ready状態、選択paper、yearをDBで適用し、語句LIKE順位とdeterministic fallbackで `min(max(4*k, 32), 200)` chunkに制限する。embeddingとPython lexical scoreは候補内だけで計算し、関連度`Citation.score`とRRFの`fusion_score`は分離する。query本文は計測ログへ含めず段階、時間、件数だけを記録する。graph evidenceのSource Version/Spanは一括取得する。PostgreSQL FTS/pgvectorはCI-014の品質・p95比較で優位性を確認してから追加する。
- Alternatives: 直ちにpgvectorを必須化する、workspace全chunkをPythonで走査し続ける、RRF scoreを引用の関連度scoreとして上書きする。
- Consequences: ORMへhydrateする行とgraph traversal/material IDにはhard boundを設けられるが、LIKEのDB scan/sort量とsemantic-only vector recallはまだ保証しない。CI-019はvalidatingに留め、CI-014でquery plan・Recall@k・p95を実測する。将来FTS/pgvectorを追加しても候補APIの契約とscore分離は維持する。
- Date: 2026-07-16

## D-20260716-09 APIキーがあるローカル環境では多言語embeddingを既定にする

- Status: accepted
- Linked items: CI-021
- Context: ローカルComposeが`local-hash-v1`を固定していたため、日本語の質問と英語論文本文の語彙が一致せず、APIキーを設定しても意味検索が働かなかった。既存embeddingを切り替える間は古いjobが新しいベクトルを上書きしてはならない。
- Decision: provider未指定時はAPIキーの有無で`auto`選択し、キーありではOpenAIの`text-embedding-3-small`、なしではローカルhashを使う。再embeddingはworkspaceとowner/editorに限定し、論文ごとにPaper→jobの順で排他する。running jobは409、queued旧jobはsupersedeする。
- Alternatives: local embeddingを固定する、利用者に環境変数を毎回手設定させる、provider切替時に並列jobを許す。
- Consequences: APIキー利用時はembeddingコストと外部送信が発生するが、日本語・英語間の意味検索を可能にする。既存論文は一度再embeddingが必要になる。
- Date: 2026-07-16

## D-20260719-10 AI壁打ち回答の区分を会話メッセージへ不変保存する

- Status: accepted
- Linked items: CI-023
- Context: SSE中はinteraction mode、draft、claim classificationを確認できても、会話履歴へ本文と引用だけを保存すると、再読時にAI生成案と論文根拠の区別が失われる。またmode別appendix生成前に保存していたため、ライブ回答と履歴本文も一致しなかった。
- Decision: assistant messageへ完成後の回答本文と、mode・draft・分類済みclaims・ResearchRun IDの型付きsnapshotを不変保存する。既存messageとuser messageは値を推測せず区分不明として扱う。AskはResearchRun作成後だけLLM streamを開始する。Idea Inbox保存時は、run IDとclaim IDが同一workspaceの一意な不変validation artifactに実在することをサーバーで検証し、artifact IDとclaim snapshotを保存する。同じworkspace・run・claimの保存はサーバー側で冗等にする。Graph起点の派生対話はnode IDとintentを受け取り、サーバーがworkspace内の正規ノードからcontentを再生成してResearchRun planへ保存する。
- Alternatives: UI選択状態だけを表示する、既存履歴をsynthesis/non-draftとしてbackfillする、claimをanchorなしで保存する。
- Consequences: migration 0025・0026とmessage API項目が増える。監査metadataを持つ環境では情報を失うdowngradeを拒否する一方、ライブ回答・履歴・Ideaの出所を一貫して追跡でき、通信再送で重複Ideaを作らない。
- Date: 2026-07-19

## 追記テンプレート

```text
## D-YYYYMMDD-XX タイトル

- Status: proposed / accepted / superseded / rejected
- Linked items: CI-xxx
- Context:
- Decision:
- Alternatives:
- Consequences:
- Date: YYYY-MM-DD
```
