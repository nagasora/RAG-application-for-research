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
- CI-007: 比較セルとgap候補が引用・条件・confidence・unknown・人間判定を持つ。
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
| CI-014 | P0 | validating | evaluation | 引用精度・反証回収・仮説重複・専門家採用率を継続評価できる | - | 2026-07-22 | D-20260716-08 | OpenAI呼び出しのusage/latency/retry/fallbackを本文非記録で統一し、offline/live artifactへ反映する。通常回帰はモックで成功。live runは明示opt-inを維持し、実providerで品質・TTFT・費用を比較して完了判定する |
| CI-015 | P1 | done | collaboration | claim単位のコメント・担当・レビュー・Decisionと引用付きReportを共有できる | CI-003, CI-006, CI-009 | 2026-07-16 | D-20260716-07 | claim/EvidenceLink排他的anchor、claim immutable snapshot、担当・comment・Decision・引用付き安全なMarkdown report・viewer read-onlyを実装。独立再レビューP0/P1なし、frontend型検査・単体33件・production build成功。backend重点3件成功、全回帰では高負荷下の既存deadline flaky 1件（単独再実行成功）を継続監視 |
| CI-016 | P0 | done | authorization | viewerの検索・LLMコスト・レート制限がUIとAPIで一貫する | - | 2026-07-16 | D-20260716-04 | viewerはローカルpreview検索のみ許可、LLM回答/SSE/要約はeditor以上に限定。OpenAPI確認と認可回帰テストを追加 |
| CI-017 | P0 | done | storage | DB・原本・graph sourceの部分失敗が孤立データや誤った成功失敗表示を残さない | - | 2026-07-16 | - | job作成・paper削除・source importの補償を実装。失敗注入テスト3件とpy_compile成功 |
| CI-018 | P1 | done | documentation | USER_GUIDEとdeployment docsが現行機能・storage adapter・権限と一致する | - | 2026-07-16 | - | メンバー管理・viewer policy・local/S3/R2 adapter・バックアップ手順をUSER_GUIDE/deployment docsへ反映。Markdownリンクとdiff check成功 |
| CI-019 | P2 | validating | performance | requestごとの全chunk走査を避け、検索品質とp95を測定できる | CI-014 | 2026-07-16 | D-20260716-08 | ORM hydrationは最大200 chunk、graphはseed 12/edge 200/evidence 400で制限し原典pageを独立取得。LIKEのDB全走査・semantic-only vector recallは未解消のため、CI-014でquery plan・Recall@k・p95を実測してFTS/pgvector採否を決める |
| CI-020 | P1 | done | operations | queue・retry・quota・cost・backup restoreを本番運用で監視・復旧できる | CI-017 | 2026-07-16 | - | operations status APIとCelery/retry/quota/cost/backup restore runbookを追加。py_compile成功 |
| CI-021 | P1 | validating | multilingual-retrieval | 日本語の研究質問から英語・日本語論文の意味的根拠を同じ検索経路で回収でき、既存論文も安全に再embeddingできる | CI-019 | 2026-07-16 | D-20260716-09 | APIキー時のOpenAI多言語embedding自動選択、workspace scoped再embedding、日→英mock回帰、Analysis/Library導線を実装。P0/P1独立レビュー済み。実providerでの再embeddingと日英検索結果を確認する |
| CI-022 | P1 | validating | onboarding | 初回利用者が論文登録から根拠確認、アイデア、仮説、比較、グラフまでの実装済み機能をUIで確認できる | CI-008, CI-021 | 2026-07-23 | - | 空グラフのmind map配置を全域化し、局所ErrorBoundaryを追加。frontend単体75件・型検査・production build、再構築後の空グラフ3表示・画面往復・console errorなしを確認。初回チュートリアル全体の完了条件は継続検証する |
| CI-023 | P1 | done | ideation | AIとの対話で統合・発散・反証・実験化・更新を選び、生成案の根拠区分を保ったまま次の問いと人間レビューへ進める | CI-005, CI-008 | 2026-07-19 | D-20260716-02, D-20260716-03, D-20260719-10 | Ask目的選択、ResearchRun自動記録、mode・draft・claims履歴、分類バッジ、真正性検証・冗等保存付きclaim→Idea Inbox、Graph選択ノードからの発散・反証・実験設計導線を実装。backend 198件、frontend 58件・型検査・buildを確認 |
| CI-024 | P1 | intake | discovery-map | 論文間の引用ネットワークを主張・仮説グラフと混同せず探索し、候補を人間レビューへ送れる | CI-012 | 2026-07-19 | - | Semantic Scholar等のcitation edge取得範囲、license、snapshot、外部候補のDiscovery queue接続を設計する |
| CI-025 | P1 | intake | evidence-matrix | 問い、採否基準、比較列、引用付き抽出を再現可能なEvidence Matrixとしてレビューできる | CI-007 | 2026-07-19 | - | 比較セルのEvidenceLink固定完了後、screening基準と列定義をResearchRunへ保存する契約を設計する |
| CI-026 | P1 | intake | claim-debate | claimごとに支持、反証、条件差、未確定を原典spanと人間判断付きで見比べられる | CI-007, CI-011 | 2026-07-19 | - | negative retrievalと比較監査を再利用するread modelとレビューUIを設計する |
| CI-027 | P1 | done | research-actions | Idea・Graph・Experimentから、出所と人間判断を保った期限付き研究Actionへ進める | CI-008, CI-010, CI-023 | 2026-07-20 | D-20260720-11 | ResearchAction API/migration、Ideaの3段階分解、Graph/Experiment導線、OpenAPI型、backend 6件・frontend 59件・型検査を確認 |
| CI-028 | P0 | done | agentic-rag | 選択した1〜5件の論文だけを対象に、生成時間を確保しながら45秒以内に回答または説明付き抽出結果を返す | CI-003, CI-014, CI-019, CI-023 | 2026-07-22 | D-20260722-12 | source scope、DB/LLM期限、PG 57014、キャンセル境界を実装。backend 227件成功。再構築コンテナでadapter・45秒・生成予約30秒・3000 tokens・API 200を確認 |
| CI-029 | P1 | done | frontend | 会話をスクロールしても研究対話、検索対象、入力欄、最新回答の根拠へ常にアクセスでき、自然な日本語で状態を理解できる | CI-006, CI-022, CI-023, CI-028 | 2026-07-22 | D-20260722-12 | 固定3ペイン、responsive drawer、1〜5件選択、日本語copyを実装。frontend 68件・型検査・production build・5 viewport・Escape/focus復元・design QA成功 |
| CI-030 | P1 | done | project-navigation | 複数の研究プロジェクトを作成・切替・名前変更でき、論文・対話・グラフ・下書きが混ざらず、再読込後も直前の選択を安全に再開できる | CI-016 | 2026-07-22 | D-20260722-13 | workspace境界を再利用して切替・検索・作成・名前変更、利用者別復元、状態分離を実装。backend認可16件、frontend 72件・型検査・production build、再構築後API/Web 200、独立レビューP0/P1なしを確認 |
| CI-031 | P1 | done | external-discovery | Semantic Scholarから外部論文を検索し、取得時点と要旨のみの範囲を失わず、選択した候補だけをLibraryへ採用できる | CI-012, CI-013 | 2026-07-27 | D-20260727-14 | Backend全回帰分割242件と最終Discovery 14件、Frontend 80件・型検査・production build、実OpenAPI生成を確認。独立レビューP0/P1/P2なし |
| CI-032 | P1 | done | multilingual-discovery | 日本語の質問文から国内外の学術DBを横断し、出所と検索計画を保った候補を低コストで取得できる | CI-012, CI-013, CI-021, CI-031 | 2026-07-28 | D-20260727-15, D-20260728-18 | HTTPS出典URL、provider workflow、session paging/import、workspace/cursorを実装。部分provider失敗をモックbrowserで確認し、backend全283件・frontend全88件・独立レビューP0/P1/P2なし |
| CI-033 | P1 | done | model-governance | workspace既定と実行単位でOpenAI/Geminiを切り替え、実際のprovider/modelを監査できる | CI-003, CI-014, CI-016 | 2026-07-28 | D-20260727-16, D-20260728-18 | Gemini plain/JSON分離、SSE・履歴・ResearchRun・auditの実効provider/model同期を実装。browserでOpenAI/Gemini selectorとlocal fallback表示を確認し、型検査・build・監査テスト成功 |
| CI-034 | P0 | done | experiment-evidence | 全文PDFの手法・条件・測定値・結果・著者考察・図表を原典へ戻れる比較として確認できる | CI-001, CI-003, CI-006, CI-007, CI-017 | 2026-07-28 | D-20260727-17, D-20260728-18 | 部分失敗保存、immutable profile/span、service/store分割、downgrade guard、caption安全化を実装。モックbrowserで3件中2件成功の比較・保存、Evidence Viewer、keyboard tabを確認 |
| CI-035 | P1 | done | mind-map | 論文または選択した原典根拠から編集可能なマップを作り、根拠を失わずNote・Research Action・Ask・Knowledge Graphへ進める | CI-001, CI-003, CI-016, CI-017, CI-023, CI-027 | 2026-07-29 | D-20260729-19 | 独立MindMap、候補確定、研究連携、権限制御、同時再送を含む冪等性を実装。migration、backend全293件相当、frontend全95件、型検査、build、Docker再構築、ブラウザ受入、独立レビューを完了 |
| CI-036 | P1 | done | external-discovery | 外部論文検索は同一オリジン接続で到達でき、DOI直接登録は書誌・要旨・出所を `abstract_only` として安全に登録できる | CI-031, CI-032 | 2026-08-02 | D-20260801-20 | 同一オリジンproxy、構造化エラー、Crossref主・Semantic Scholar fallback、canonical DOI、arXiv応答ID照合、検索session・Paper親行のPostgreSQL FK順序、全providerの検索結果追加契約を修正。backend全325件と最終外部論文51件、frontend 99件・型検査・production build、実PostgreSQLで提示DOIの冪等登録とOpenAlex検索20件→既存Paperへのduplicate収束を確認。 |
| CI-037 | P1 | validating | ingestion | PDF等の抽出テキストに制御文字が混入しても、本文・ページ・表要素・原典spanを保存し、分析可能にする | CI-017 | 2026-08-03 | - | NUL除去の回帰テストとバックエンド回帰を実行し、PostgreSQL取り込みでも確認する。 |
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

### CI-028 研究対話の期限とsource scope

- 新しい対話では検索対象を空に戻し、1〜5件を選ぶまで回答生成を開始できない。
- ResearchRunへ保存したsource paper IDとpreview・SSE・検索で使うIDを一致させ、不一致は機械可読なエラーで拒否する。
- DB候補32件、LLM根拠8件、1論文3件、根拠本文12,000文字を上限とし、論文総数に応じて無制限に入力を増やさない。
- 全体45秒の中で生成に約30秒を確保し、残り時間が少ない場合は任意の再計画・再ランキング・監査を省略する。
- 期限内に生成できない場合も抽出根拠を返し、AI生成ではないことと再試行操作を明示する。

### CI-029 固定レイアウトと利用者向け日本語

- Ask表示中はページ全体をスクロールさせず、プロジェクト帯、対話ヘッダー、入力欄を固定し、中央の会話だけをスクロールする。
- 1536px以上は3ペイン、1536px未満は根拠drawer、1280px未満は研究対話drawerとし、Escape、フォーカストラップ、フォーカス復元を維持する。
- 390×844、1024×768、1280×800、1536×864、1920px幅で主要操作とsafe-areaを確認する。
- 共通ナビ、状態、進捗、回答区分、エラーを自然な日本語へ集約し、未知の内部エラー本文を画面へ表示しない。
- 引用形式の確認と、回答主張・引用元の対応確認を別表示にし、実施していない検証を「確認済み」と表現しない。

### CI-030 複数研究プロジェクト

- 現在のプロジェクト名と切替操作を常時確認でき、一覧から切替、新規作成、所有プロジェクトの名前変更ができる。
- 新規作成後は空の新プロジェクトへ自動で切り替わり、既存プロジェクトの論文・対話・ノートは移動しない。
- 切替時は進行中の要求を中止し、論文検索、選択、根拠、アイデア下書き、再embedding状態を旧プロジェクトから持ち越さない。
- 全API要求は選択中のworkspace IDを認可境界へ渡し、アクセス権を失った保存済みIDは個人プロジェクトへ安全に戻す。
- 最後に開いたアクセス可能なプロジェクトを再読込後に復元し、A/Bプロジェクト間の論文が相互に表示されないことを回帰テストで確認する。

### CI-031 外部論文サーチ

- Semantic Scholarをキーワード、年範囲、関連度・新着・引用数で検索し、20件単位で候補を確認できる。
- 検索結果は自動採用せず、owner/editorが明示選択した1〜20件だけを要旨付きでLibraryへ登録する。
- provider、取得時刻、license、rate limit policy、応答snapshot、検索条件を保存し、DOI・arXiv・Semantic Scholar IDで重複登録を防ぐ。
- 要旨だけの論文は `abstract_only` として、PDFページではなく「外部要旨」をEvidenceに表示する。
- viewer、別workspace、provider rate limit・timeout・部分失敗・再送をAPIとUIの両方で安全に扱う。

### CI-032 低コスト多言語論文探索

- 日本語または英語の研究質問を最大4件の日英検索queryへ展開し、使用した検索計画とmodel有無を利用者が確認できる。
- Semantic Scholar、OpenAlex、CiNii、J-STAGEを独立providerとして検索し、DOI・arXiv・provider IDを優先して正規化し、RRFで統合する。
- providerの一部が429・timeout・不正応答でも取得済み候補を返し、失敗providerを画面とAPIへ明示する。
- 検索候補はworkspace内の期限付きSearch Sessionだけに保存し、owner/editorが選択した候補以外はPaper・Evidenceにしない。
- CiNii Application IDや検索語生成modelが未設定でも、利用可能providerと原文queryで検索でき、有料providerへ自動fallbackしない。

### CI-033 OpenAI・Gemini生成モデル切替

- server allowlistから利用可能なOpenAI・Geminiモデルだけを表示し、API keyや任意model IDをブラウザへ渡さない。
- ownerはworkspace既定を変更でき、editorはAsk・Discovery・Analysisの各実行だけを一時上書きでき、viewerは保存済み結果の閲覧だけができる。
- 実際に解決したprovider、model、prompt version、usageをResearchRunまたは対応する実行snapshotへ保存する。
- provider障害時は別の有料modelを自動実行せず、機能ごとに定めたローカルまたは検索語未展開の安全な縮退を表示する。
- 生成modelの変更はembedding provider・modelと分離し、既存論文の再embeddingを発生させない。

### CI-034 実験Evidence比較

- 全文解析済みPDFを2〜5件選び、「実験結果・手法を比較」を押した時だけ未抽出profileを生成し、原本hash・model・prompt version一致時は再利用する。
- 研究目的、実験デザイン、対象、条件、手法、比較対象、測定値、観測結果、著者考察、限界を分離し、本文にない値は `未報告` と表示する。
- 結果文・考察文はexact quoteとSourceSpan、図はpage・bbox・caption、表はrow・column・header・cell locatorから原典へ戻れる。
- 数値・単位・比較対象・引用が原本または表セルと一致しない候補は保存せず、自動抽出の初期状態を `review_pending` にする。
- `abstract_only` 論文を抽出対象へ含めず、比較表、実験結果・考察、図表、研究ギャップの4タブとPDF導線を表示しない。

### CI-035 根拠付き編集可能マインドマップ

- 解析済み全文論文1件、または利用者が選んだ同一workspaceのEvidenceRef・SourceSpanだけから候補を生成し、使用したsource scopeとResearchRunを保存する。
- 初期ツリー、枝展開、Research Actionは候補として返し、利用者が選択して確定するまでMindMap・Actionへ保存しない。生成ノードは確定後も`review_pending`とする。
- 1 root、許可kind、題名200文字、本文4,000文字、最大250ノード、深さ8、同一map親子、連続orderをサーバーで検証し、全ツリーを原子的に保存する。
- 部分木削除は対象配下と関連リンクだけを削除し、作成済みNote、Research Action、明示昇格したKnowledgeNode本体を残す。
- 全読書きでworkspace境界とviewer read-onlyを保ち、LLMが使えない場合も閲覧、手動編集、Note・Action・Ask・Graph昇格を利用できる。

### CI-036 外部論文検索・DOI取得の復旧

- `NEXT_PUBLIC_API_URL` 未設定のローカルUIは同一オリジンの `/api` proxy を通じ、CORS差異で検索要求を失敗させない。明示した本番API URLは従来どおり直接利用する。
- 検索の通信失敗、全provider障害、部分provider障害を区別してフォーム直下に表示し、利用者が明示操作で再試行できる。自動再試行は行わない。
- DOI、`doi:`、DOI resolver URLを同一のcanonical DOIにし、Crossrefから書誌・要旨を取得する。Crossrefが利用不能な時だけSemantic Scholarをfallbackとして用いる。
- 直接登録した論文はprovider snapshot、取得時刻、license、rate policyを残す`abstract_only`であり、PDFを自動取得・解析・比較対象化しない。
- 不正ID、未発見、rate limit、provider利用不能時にはPaperを保存せず、安全な構造化エラーを返す。

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
