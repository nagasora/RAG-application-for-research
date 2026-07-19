# PaperPilot 継続改善台帳

最終レビュー: 2026-07-16  
次回定期レビュー: 2026-07-30  
正本: このファイル。外部Issue管理を導入するまでは、優先順位と状態を他の文書へ複製しない。

## 目的

PaperPilotを「論文について賢く話せるアプリ」から、「既存研究から飛躍し、その飛躍を反証可能な仮説と実験へ変換する研究支援基盤」へ発展させる。

改善では機能数ではなく、研究者が次のループを出所を失わずに完了できることを重視する。

```text
研究問い
  → 既存知識・反証の把握
  → 発散的アイデア生成
  → 競合仮説との比較
  → 反証可能な仮説への構造化
  → 識別力の高い実験設計
  → 結果と判断の記録
  → 信念・研究問いの更新
```

## 非目標

- 文献要約やチャット機能の数だけを増やすこと。
- AI生成物を人間の判断なしに「検証済み」「新規発見」と扱うこと。
- Elicit、scite、Litmaps、OSFなどの外部サービスをそのまま再実装すること。
- 根拠の追跡可能性、反証可能性、データ境界を犠牲にして自律性を高めること。

## 運用ルール

### 開発を再開するとき

1. `AGENTS.md` とこの台帳を読む。
2. `python scripts/improvement_backlog.py check` で台帳を検査する。
3. `python scripts/improvement_backlog.py next` で依存関係を満たす候補を確認する。
4. 対象項目の現コード、未コミット差分、関連テストを確認する。
5. 外部情報が90日以上前、または変化しやすい仕様なら、公式情報を再調査して「調査ログ」を更新する。
6. 一度に着手する項目を絞り、状態を `in_progress` にする。
7. 実装後、受入条件・テスト・API/UI契約・文書を確認して `validating` から `done` へ移す。
8. 新しい課題が見つかったら、現在の項目へ無理に含めず、新しいIDで追加する。

### 状態

- `intake`: 課題候補。調査または受入条件の具体化が必要。
- `ready`: 依存関係と受入条件が明確で、着手可能。
- `in_progress`: 実装中。全体で最大3件。
- `validating`: 実装済みで、回帰テスト・UI・運用確認中。
- `blocked`: 外部判断、権限、前提実装などを待っている。
- `done`: 受入条件、関連テスト、契約、文書が確認済み。
- `retired`: 採用しない。理由をDecision Logまたは完了・保留欄に残す。

### 優先度

- `P0`: 誤った研究判断、再現不能な主張、権限逸脱、データ損失につながり得る。
- `P1`: 研究の継続、検証、共同作業を大きく妨げる。
- `P2`: 効率、UX、拡張性を改善する。P0/P1の品質を悪化させない。

### 完了の定義

- 利用者に起きる変化が受入条件どおり確認できる。
- API変更はバックエンドDTO、OpenAPI、フロントエンド型、エラー、SSE、テストが同期している。
- AI・外部APIの通常テストはモックされ、秘密情報やネットワークを要求しない。
- 根拠を扱う変更は、原典位置、引用内容、source revision、低品質抽出の扱いを確認する。
- `done` へ変更した行は、Next actionに検証結果または関連PR/commitを残す。

## 現在のFocus

Focusは同時に最大3件とする。次回実装では、原則として上から検討する。

<!-- FOCUS_START -->
- CI-023: AIとの壁打ちで発散・反証・実験化し、根拠を失わず次の問いへ進める。
- CI-022: 初回利用者が研究ワークフロー全体をUIで確認できるようにする。
- CI-007: 比較・gap候補の引用と人間判断を再取り込み後も検証する。
<!-- FOCUS_END -->

## 優先バックログ

`Evidence reviewed` は課題の根拠を最後にコード・ユーザー観察・公式情報で確認した日。依存はカンマ区切り、依存なしは `-` とする。

<!-- BACKLOG_TABLE_START -->
| ID | Priority | State | Area | Outcome | Depends on | Evidence reviewed | Decision | Next action |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CI-001 | P0 | done | evidence | 各claimが原本revision・page・span・quoteへ戻れ、再取り込み後も検証できる | - | 2026-07-16 | D-20260716-02 | EvidenceLink DTO/API・migration 0013・旧Citation互換を追加。quote offset不一致は422拒否、migration/API対象テストは7件成功 |
| CI-002 | P0 | done | product | 研究問いと名前付きsource setを中心に作業を再開できる | - | 2026-07-16 | - | SourceSet FK順序とrollbackを修正。関連6件・認証/研究workspace 27件・バックエンド全回帰成功 |
| CI-003 | P0 | done | provenance | 質問・検索範囲・モデル・検索順位・検証結果をimmutable ResearchRunとして再表示できる | CI-001, CI-002 | 2026-07-16 | - | ResearchRun/append-only RunArtifact・SSE run_id・server cancelを実装。migration/API対象テスト8件成功 |
| CI-004 | P0 | done | hypothesis | 仮説がメカニズム・条件・競合理論・反証予測・判定可能な試験を持つ | CI-001, CI-002 | 2026-07-16 | D-20260716-02, D-20260716-03 | HypothesisCard CRUD・状態遷移を実装。競合理論/反証予測なしのreviewable以降は禁止、APIテスト1件成功 |
| CI-005 | P0 | done | agentic-rag | Evidence・Synthesis・Explore・Challenge・Design・Updateを混同せず実行できる | CI-003, CI-004 | 2026-07-16 | D-20260716-02, D-20260716-03 | graph/negative retrievalと接続。Challengeのnegative stance、scope、fallback、Agentic互換を独立レビュー済み |
| CI-006 | P0 | done | frontend | 質問・claim・PDF原文を同時表示し、文脈を失わず根拠判定できる | CI-001, CI-003 | 2026-07-16 | - | PDF・citation span・抽出原文の3ペイン、citation→chunk移動、キーボードページ移動と抽出状態表示を実装。環境依存でUI検証未実行 |
| CI-007 | P0 | validating | analysis | 比較セルとgap候補が引用・条件・confidence・unknown・人間判定を持つ | CI-001, CI-006 | 2026-07-16 | - | chunk snapshotをEvidenceLinkへ接続し、再取り込み後の解決を検証する |
| CI-008 | P1 | done | ideation | 素早く保存した考えがInboxで未検証と分かり、根拠・反証を付けて仮説へ昇格できる | CI-003, CI-004, CI-006 | 2026-07-16 | D-20260716-02, D-20260716-06 | workspace境界・anchor整合性・昇格checklist/二重昇格防止・昇格時snapshot・Research UIまで実装。backend全回帰、生成OpenAPI契約、frontend型検査・単体33件・production buildを確認 |
| CI-009 | P1 | done | memory | 採用・保留・棄却・失効を混ぜず、考えが変わった理由を履歴として追える | CI-003, CI-004 | 2026-07-16 | D-20260716-03 | append-only BeliefEventとrejected除外の正コンテキスト取得を実装。migration chain確認とAPI対象テスト1件成功 |
| CI-010 | P1 | done | experiment | 競合仮説を識別する実験、判定基準、停止規則を保存・事前登録へ渡せる | CI-004, CI-009 | 2026-07-16 | D-20260716-03, D-20260716-06 | 仮説値snapshot付きExperiment Plan/Result、append-only履歴・versioned export・Research UI・生成OpenAPI契約を実装。backend全回帰、frontend型検査・単体33件・production buildを確認 |
| CI-011 | P1 | done | retrieval | 通常RAGが支持だけでなく棄却仮説・矛盾・negative evidenceを検索する | CI-001, CI-004, CI-009 | 2026-07-16 | - | 通常/Agentic RAGへpaper/graph/contradiction RRFと監査provenanceを統合。backend全回帰・frontend単体28件成功 |
| CI-012 | P1 | done | discovery | 新着論文が既存仮説を支持・反証・条件変更する差分としてレビュー待ちになる | CI-001, CI-002, CI-004 | 2026-07-16 | D-20260716-05 | Semantic Scholar provider、snapshot/license/rate limitを持つpending review queueを実装。外部APIはMockTransport、対象テスト1件成功 |
| CI-013 | P1 | done | library | 500件規模でも検索・filter・名前付きcollection・採用除外理由を扱える | CI-002 | 2026-07-16 | - | SourceSet回帰解消、SQL chunk count、OpenAPI生成型へ移行。TypeScript・単体23件・production build成功 |
| CI-014 | P0 | validating | evaluation | 引用精度・反証回収・仮説重複・専門家採用率を継続評価できる | - | 2026-07-16 | D-20260716-08 | v2 offline artifactでRecall/citation/quote/contradiction/semantic gate/query plan/p50-p95/costを版管理。明示opt-in live runと専門家評価を実行し、CI-019のsemantic/index gateを解消する |
| CI-015 | P1 | done | collaboration | claim単位のコメント・担当・レビュー・Decisionと引用付きReportを共有できる | CI-003, CI-006, CI-009 | 2026-07-16 | D-20260716-07 | claim/EvidenceLink排他的anchor、claim immutable snapshot、担当・comment・Decision・引用付き安全なMarkdown report・viewer read-onlyを実装。独立再レビューP0/P1なし、frontend型検査・単体33件・production build成功。backend重点3件成功、全回帰では高負荷下の既存deadline flaky 1件（単独再実行成功）を継続監視 |
| CI-016 | P0 | done | authorization | viewerの検索・LLMコスト・レート制限がUIとAPIで一貫する | - | 2026-07-16 | D-20260716-04 | viewerはローカルpreview検索のみ許可、LLM回答/SSE/要約はeditor以上に限定。OpenAPI確認と認可回帰テストを追加 |
| CI-017 | P0 | done | storage | DB・原本・graph sourceの部分失敗が孤立データや誤った成功失敗表示を残さない | - | 2026-07-16 | - | job作成・paper削除・source importの補償を実装。失敗注入テスト3件とpy_compile成功 |
| CI-018 | P1 | done | documentation | USER_GUIDEとdeployment docsが現行機能・storage adapter・権限と一致する | - | 2026-07-16 | - | メンバー管理・viewer policy・local/S3/R2 adapter・バックアップ手順をUSER_GUIDE/deployment docsへ反映。Markdownリンクとdiff check成功 |
| CI-019 | P2 | validating | performance | requestごとの全chunk走査を避け、検索品質とp95を測定できる | CI-014 | 2026-07-16 | D-20260716-08 | ORM hydrationは最大200 chunk、graphはseed 12/edge 200/evidence 400で制限し原典pageを独立取得。LIKEのDB全走査・semantic-only vector recallは未解消のため、CI-014でquery plan・Recall@k・p95を実測してFTS/pgvector採否を決める |
| CI-020 | P1 | done | operations | queue・retry・quota・cost・backup restoreを本番運用で監視・復旧できる | CI-017 | 2026-07-16 | - | operations status APIとCelery/retry/quota/cost/backup restore runbookを追加。py_compile成功 |
| CI-021 | P1 | validating | multilingual-retrieval | 日本語の研究質問から英語・日本語論文の意味的根拠を同じ検索経路で回収でき、既存論文も安全に再embeddingできる | CI-019 | 2026-07-16 | D-20260716-09 | APIキー時のOpenAI多言語embedding自動選択、workspace scoped再embedding、日→英mock回帰、Analysis/Library導線を実装。P0/P1独立レビュー済み。実providerでの再embeddingと日英検索結果を確認する |
| CI-022 | P1 | in_progress | onboarding | 初回利用者が論文登録から根拠確認、アイデア、仮説、比較、グラフまでの実装済み機能をUIで確認できる | CI-008, CI-021 | 2026-07-16 | - | 実データ状態に連動する再表示可能な初回チュートリアルと各画面への導線を追加する |
| CI-023 | P1 | done | ideation | AIとの対話で統合・発散・反証・実験化・更新を選び、生成案の根拠区分を保ったまま次の問いと人間レビューへ進める | CI-005, CI-008 | 2026-07-19 | D-20260716-02, D-20260716-03, D-20260719-10 | Ask目的選択、ResearchRun自動記録、mode・draft・claims履歴、分類バッジ、真正性検証・冗等保存付きclaim→Idea Inbox、Graph選択ノードからの発散・反証・実験設計導線を実装。backend 198件、frontend 58件・型検査・buildを確認 |
| CI-024 | P1 | intake | discovery-map | 論文間の引用ネットワークを主張・仮説グラフと混同せず探索し、候補を人間レビューへ送れる | CI-012 | 2026-07-19 | - | Semantic Scholar等のcitation edge取得範囲、license、snapshot、外部候補のDiscovery queue接続を設計する |
| CI-025 | P1 | intake | evidence-matrix | 問い、採否基準、比較列、引用付き抽出を再現可能なEvidence Matrixとしてレビューできる | CI-007 | 2026-07-19 | - | 比較セルのEvidenceLink固定完了後、screening基準と列定義をResearchRunへ保存する契約を設計する |
| CI-026 | P1 | intake | claim-debate | claimごとに支持、反証、条件差、未確定を原典spanと人間判断付きで見比べられる | CI-007, CI-011 | 2026-07-19 | - | negative retrievalと比較監査を再利用するread modelとレビューUIを設計する |
<!-- BACKLOG_TABLE_END -->

## 主要項目の受入条件と評価指標

### CI-001 EvidenceLink

- `source_version_id`、`source_span_id`、offset、verbatim quote、target claim、役割、抽出品質を保持する。
- quoteとsource spanの指定範囲が一致しなければ保存を拒否する。
- 再取り込み後も過去の会話・比較・graphから当時の原典位置を解決できる。
- 指標: quote exact match 100%、再取り込みcitation survival 100%。

### CI-003 ResearchRun / RunArtifact

- 研究問い、source set、除外source、目的、成功条件、計画、検索候補と順位、モデル、prompt version、検証結果、開始終了時刻を保存する。
- 同じrunを後日開き、同一source snapshotで再実行または派生runを作れる。
- 実行中はstatusとserver-side cancelをrun IDで扱う。

### CI-004 HypothesisCard

- claim、mechanism、対象、条件、操作・曝露、outcome、方向、前提、競合理論、prediction、falsifier、testを持つ。
- 少なくとも一つの反証予測と競合理論がなければ、reviewableな状態へ進めない。
- `human_reviewed` と `empirically_supported` を別状態にする。
- 指標: schema completeness、専門家による反証可能性評価、重複仮説率。

### CI-005 Interaction modes

- Evidenceは選択sourceだけ、Exploreは会話とLLM一般知識を許可する。
- 全claimを `evidence_backed`、`inference`、`general_knowledge`、`hypothesis`、`unverified` に分類する。
- Exploreはメカニズムが異なる3件以上を返し、Criticは競合仮説と最強の反証を作る。
- audit不能な論文claimはdraftまたはextractive evidenceへ落とす。

### CI-006 Evidence Workbench

- 引用から2操作以内に正確な原文spanを開ける。
- 原文閲覧中も質問、claim、他のcitationを同時に確認できる。
- キーボードだけでcitation移動、ペイン切替、ノート作成ができる。
- source消失、低品質OCR、抽出失敗を理由付きで表示する。

### CI-007 Claim-aware comparison

- 全AI生成セルに根拠または「未判定」を表示する。
- p.1固定ではなく該当spanへ移動する。
- 保存比較はsource set、引用snapshot、人間の採用・保留・棄却理由を保持する。
- 「Research gap」は、著者記載の限界、矛盾、外的妥当性、方法限界、未接続概念を区別した候補として出す。

### CI-008 Idea Inbox

- 30秒以内に保存でき、現在のrun、claim、paper、spanをanchor候補として付ける。
- Inboxでは観察、解釈、仮説、反証、TODOと未検証状態を区別する。
- 根拠接続、反証検索、実験化、研究者確認を経てHypothesisへ昇格する。

### CI-009 Belief Ledger

- proposed、supported、disputed、rejected、supersededを上書きせずイベントとして残す。
- 回答で使用したmemory item IDと当時の状態をRunへ記録する。
- 棄却仮説は新規案の正の前提ではなく、反証・重複回避コンテキストへ渡す。

### CI-010 Experiment Plan

- 操作変数、測定変数、対照、交絡、予測、判定閾値、停止規則、必要データ、コストを持つ。
- 競合仮説をどの程度区別できるかを明示する。
- 仮説・分析計画・変更履歴・引用をOSF等へ渡せるsnapshotにする。

### CI-012 Discovery monitor

- 新着候補は自動採用せずreview queueへ入れる。
- provider、取得日時、license、coverage、内容hashを保存する。
- 通知を「支持」「反証」「境界条件変更」「方法代替」「重複」に分類し、原文引用を表示する。

### CI-014 Evaluation harness

- fixtureに日本語・英語、因果、否定、数値、矛盾、低品質OCR、再取り込みを含める。
- Recall@k、citation precision、claim entailment、contradiction recall、falsifier coverage、仮説多様性、専門家採用率、p95、costを版管理する。
- 外部APIとLLMは通常テストでモックし、評価用の実モデル実行は明示的に分離する。
- offline artifactは `python scripts/run_ci014_evaluation.py --output <path>` で生成する。`--measure-latency` はin-memory component診断だけで、100/500/5000件のproduction DB p95とは扱わない。
- live model probeは `CI014_LIVE_BENCHMARK=1` とAPI keyを設定した上で `--live-model` を指定した場合だけ実行し、通常テスト・offline artifactからは呼ばない。
- semantic-only recallまたはindexed query plan gateがfalseの間、CI-019をdoneへ昇格させない。

### CI-019 Retrieval performance

- 回答経路はworkspace全論文・全chunkをhydrateせず、DBで認可scopeとready状態を適用した上限付き候補だけを読む。
- 候補poolは `min(max(4 * k, 32), 200)` とし、SQLite/PostgreSQLの双方で動く語句一致fallbackを持つ。
- Citationの関連度scoreとRRF fusion scoreを混同せず、graph provenanceはhit数に比例するDB queryを発生させない。
- query本文をログへ保存せず、DB候補・embedding cache・rank/graph段階の時間と件数だけを計測する。
- 指標: ORM候補chunk最大200、graph seed最大12・edge最大200・Evidence ID最大400・原典chunk最大200。CI-014負荷fixtureでquery plan、semantic-only Recall@k、p95を版管理する。
- 未完了ゲート: portable `LIKE` は返却行を制限してもDB scan/sort自体を索引化しない。PostgreSQL FTS/GINまたはpgvectorを本番必須にする前に、PostgreSQL実環境とSQLite fallbackの双方で負荷計測する。

### CI-023 対話型・発散キャンバス

- Askで統合、発散、反証、実験化、考えの更新を目的として選べ、選択が実際のinteraction modeへ渡る。
- 発散・反証・実験案は未検証のdraftであり、論文根拠と同一視しないことを送信前後に表示する。
- 発散では異なる機構の案を3件以上提示し、反証ではnegative evidenceがなければ未検証と明示する。
- AI生成案は人間が選択するまでreview pendingとし、会話由来の根拠と論文の原典EvidenceLinkを混同しない。
- 次段ではGraphの選択ノードから「広げる」「対立仮説」「検証案」の派生対話を開始し、選択ノードと意図をResearchRunへ残す。
- LLMが使えない場合は決定的な質問テンプレートまたは抽出根拠へフォールバックし、生成済み仮説のように表示しない。

## 調査ログ

### 2026-07-16 研究支援製品レビュー

- [Elicit](https://elicit.com/solutions/systematic-reviews): 検索、screening、構造化抽出、supporting quote、systematic reviewの監査可能な中間工程を参考にする。
- [Consensus](https://consensus.app/home/features/research-agent/): multi-step検索と統合を参考にするが、単純な合意表示で条件差を潰さない。
- [scite](https://scite.ai/): supporting、contrasting、mentioningの引用文脈をEvidenceLinkと新着差分へ活用する。
- [NotebookLM](https://support.google.com/notebooklm/answer/16179559?hl=en): source scope、引用から原文への移動、回答保存の短い導線を参考にする。
- [ChatGPT Projects](https://help.openai.com/en/articles/10169521-using-projects-in-chatgpt): 長期プロジェクト内の会話・ファイル・指示・保存回答を参考にする。
- [ChatGPT Deep Research](https://help.openai.com/en/articles/10500283-deep-research-faq): 実行前のplan確認、進捗、source制御、activity historyをResearchRunへ応用する。
- [Litmaps](https://www.litmaps.com/features): citation network、seed collection、monitorを外部発見へ応用する。
- [ResearchRabbit](https://www.researchrabbit.ai/features): collectionと逐次的な関連文献発見を参考にする。
- [Semantic Scholar API](https://www.semanticscholar.org/product/api): 最初の外部発見provider候補。metadata・citation・recommendationをparagraph-level evidenceの代用にはしない。
- 2026-07-16再確認: Paper endpointは`/graph/v1/paper/{paper_id}`、`fields`で返却項目を指定する。API key導入時は1 RPSであり、無認証の共有上限も繁忙時にthrottleされ得るため、CI-012では取得日時・provider応答snapshot・license・rate limit policyを保存し、候補を自動採用しない。ライセンス条件は[API License](https://www.semanticscholar.org/product/api/license)を継続確認する。
- [OSF Registrations](https://help.osf.io/article/330-welcome-to-registrations): preregistrationのexport先として連携し、PaperPilot内で再実装しない。
- [FutureHouse AI Scientist](https://www.futurehouse.org/ai-scientist): world model、hypothesis、experimentationの更新ループを長期像として参照する。完全自律ではなく人間の採否を境界にする。

### 2026-07-19 発散・探索体験の再レビュー

- [NotebookLM chat](https://support.google.com/notebooklm/answer/16179559?hl=en) と [Mind Maps](https://support.google.com/notebooklm/answer/16212283?hl=en): source-grounded chatに加え、map nodeを起点に掘り下げる短い往復をCI-023のGraph→Ask導線へ応用する。
- [ResearchRabbit Features](https://www.researchrabbit.ai/features) と [Litmaps Features](https://www.litmaps.com/features): 論文・著者・引用の発見ネットワークを参考にする。ただしPaperPilotのclaim・hypothesis graphとは別レイヤーに保つ。
- [Elicit Systematic Reviews](https://pro.elicit.com/solutions/systematic-reviews): screening、構造化抽出、supporting quoteを一連の監査可能な工程として扱う点をCI-025へ反映する。
- [scite](https://scite.ai/): supporting / contrasting citation contextの見通しを参考にするが、PaperPilotでは原典spanと人間判断を優先する。
- [Consensus product changelog](https://help.consensus.app/en/articles/11954907-consensus-product-changelog): Deep SearchやCitation Graphを参考にしつつ、条件差と反証可能性を単一の合意指標へ潰さない。

## 新しい課題を追加するテンプレート

バックログ表へ一行追加し、必要なら下に受入条件を追記する。

```text
| CI-xxx | P0/P1/P2 | intake | area | 利用者に起きる検証可能な変化 | CI-yyy または - | YYYY-MM-DD | D-YYYYMMDD-XX または - | 次に行う一つの具体的作業 |
```

追加時には以下を確認する。

- 既存IDと重複していないか。
- 単なる実装手段ではなく、利用者または研究品質のOutcomeになっているか。
- 根拠がコード、ユーザー観察、テスト、公式資料のどれかで説明できるか。
- 受入条件がテストまたは人間の確認で判定できるか。
- 依存関係と、今やらない場合のリスクが明確か。

## 完了・保留・廃止

完了項目は月次でここへ圧縮する。詳細な判断は `docs/DECISIONS.md` を参照する。

| ID | Final state | Date | Result / reason | Decision / PR |
| --- | --- | --- | --- | --- |

## 定期レビュー

- 毎週15分: Focus、`in_progress`、`blocked`、Next actionを更新する。
- 隔週30分: P0/P1、指標、調査情報の鮮度を確認する。
- リリース前: 関連CI-ID、受入条件、Decision、USER_GUIDE、運用文書を確認する。
- 90日更新がない項目: 根拠を再調査し、優先順位を更新するか`retired`へ移す。
- 月次: `done`を完了欄へ圧縮し、Focusを最大3件へ戻す。
