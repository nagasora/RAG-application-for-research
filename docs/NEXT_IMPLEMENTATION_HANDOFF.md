# PaperPilot 実装レビュー・次担当ハンドオフ

確認日: 2026-07-16

## 結論

PaperPilot は、論文取り込み、workspace認可、引用付き検索、knowledge graph、ResearchRun、HypothesisCard、SourceSet、Discovery Queueなどの基盤を持つ。一方、継続改善台帳の一部は「APIやDTOが存在する」段階で `done` とされており、利用者が画面から一連の研究ワークフローを完了できる状態には達していない。

次担当は、新機能追加より先に以下を行う。

1. 回帰失敗2件とOpenAPI契約差分を解消する。
2. 通常RAGへnegative evidence / graph retrievalを実接続する。
3. Idea Inbox、Experiment Plan、比較EvidenceLinkを監査可能な形に仕上げる。
4. 評価ハーネスを、登録済みだが未測定の指標へ広げる。

## 検証結果

### バックエンド

`python -m pytest tests -q`:

- 156 passed
- 2 failed
- 8 warnings
- 所要時間 272.65秒

失敗:

1. `tests/test_auth_workspace.py::test_library_page_filters_source_sets_and_records_screening_decisions`
   - `source_set_papers` INSERTでSQLite FK違反。
   - SourceSet作成を実際に妨げる回帰であり、CI-002/CI-013を完了扱いにできない。
2. `tests/test_research_workspace.py::test_search_stream_opens_before_running_blocking_generation`
   - テストが `WorkspaceContext` の代わりに `object()` を渡し、追加されたwrite認可で `AttributeError`。
   - 実装の認可方針は妥当だが、ストリームが即時openする契約テストを正しいcontext fixtureへ更新する必要がある。

### フロントエンド

- `corepack pnpm exec tsc --noEmit`: 成功。
- `corepack pnpm run test:unit`: 19 passed。
- `next build`: 依存取得は完了したが、実行ログから最終成功を確認できていない。再実行が必要。

### Migration

`20260716_0013` から `20260716_0022` は直列に接続されている。ただし `0021_ideas` と `0022_experiment_plans` はORMより制約が弱い。後述の追補migrationが必要。

## P0: 次に必ず直す項目

### 1. 通常RAGが矛盾・negative evidenceを使用していない

根拠:

- `backend/app/main.py` の通常検索retrieverはPaper/Chunkだけを検索する。
- `backend/app/graph_rag.py` のgraph retrievalは別endpointで、通常回答経路から呼ばれない。
- `backend/app/rag.py` の `reciprocal_rank_fusion()` は通常回答で未使用。
- `Citation` DTOはpaper/chunk/page/excerpt中心で、graph path、SourceSpan、Evidence roleを表現できない。

影響:

- CI-011は未完。
- 棄却済み仮説、矛盾、negative evidenceが回答生成と引用へ届かない。
- Challenge modeが実データではなく固定文を返し得る。

推奨実装:

1. workspace内KnowledgeNodeをqueryから候補化する。最初はPostgreSQL FTS、将来pgvectorを追加する。
2. paper chunk、graph node、contradiction edgeを別channelとして取得する。
3. RRFで統合し、`source_kind`、`source_span_id`、`evidence_role`、`graph_path`を持つ共通Citation DTOを定義する。
4. `AgenticRAG` のretrieverへ統合結果を渡す。
5. Challenge modeではcontradicting evidenceがない場合にその旨を明示し、固定の反証を根拠付き回答として扱わない。

受入条件:

- 支持文献だけのfixtureより、支持+矛盾fixtureでcontradiction recallが上がる。
- 回答中のgraph claimからSourceSpanとverbatim quoteへ戻れる。
- workspace外node/spanは候補化されない。

### 2. CI-014評価ハーネスが主要指標を測定していない

根拠:

- `backend/app/evaluation.py` が実測するのはRecall@kとcitation precisionだけ。
- claim entailment、contradiction recall、falsifier coverage、hypothesis diversity、expert acceptance、p95、costはfixtureへ名前を登録しただけ。
- graph contradictionテストは人工的な1-edge graphで、通常RAG fusionを通らない。

推奨実装:

- golden caseへpaper relevance、graph path、期待Evidence role、exact SourceSpanを追加する。
- RRF fusion、quote exact match、contradiction recall、p95を自動測定する。
- LLM/専門家評価はoffline通常テストと分離し、明示コマンドだけで実行する。
- 評価結果をJSON artifactとして版管理する。

## P1: 機能を完成させる項目

### 3. SourceSet作成のFK回帰

現象:

- `PaperStore.create_source_set()` で `source_set_papers` INSERTがFK違反になる。
- 単独テストでも再現する。

次の調査:

- transaction内でsource set、paper、linkの存在をflush前後に確認する。
- `session.flush()` 後にlinkを追加する回帰テストを作り、FKのどちらが欠けるかSQLite `foreign_key_check` で特定する。
- PostgreSQLでも同じ作成・更新・削除をmigration適用DBで確認する。

### 4. Idea Inboxは専用APIと画面が分離している

根拠:

- backendには `/api/ideas` とpromote APIがある。
- `frontend/components/idea-capture.tsx` は専用Idea APIではなくKnowledgeNode metadataへ保存する。
- Idea PATCHがなく、作成後にchecklistを更新できない。
- `0021_ideas` はORMに存在するrun/paper/span/hypothesis FK、kind/status checkを作成しない。
- promote時、anchorをHypothesisCardへ監査metadataとして引き継がない。

推奨実装:

1. 追補migrationでFK、index、kind/status checkを追加する。
2. Idea create/update/list/promote clientをOpenAPIから生成する。
3. UIを専用Inbox APIへ切り替える。
4. evidence/falsifier/test/researcher reviewをPATCH可能にする。
5. promote時にrun/claim/paper/span anchorをHypothesisCardへsnapshot保存する。
6. 不足checklist、二重昇格、別workspace anchor、削除済みanchorをテストする。

### 5. Experiment Planは部分実装

現状:

- `0022_experiment_plans`、作成、result追記、snapshot取得APIは存在する。
- UI、対象pytest、履歴のappend-only DB保証、OSF等へ渡せる固定形式exportはない。
- migrationはHypothesisCard FK、plan/status制約を作成していない。

推奨実装:

- plan/result/historyを別のappend-only recordへ分離する。
- HypothesisCard FKとworkspace整合をDB/API双方で検証する。
- snapshotへschema version、EvidenceLink、変更履歴、判定基準を含める。
- 作成・結果追記・変更履歴・cross-workspace拒否・export再現性をテストする。
- Research画面へ一覧、編集、result記録、snapshot exportを追加する。

### 6. Interaction modeは固定文を含む

根拠:

- Explore/Challenge/Design/Updateの一部は `backend/app/main.py` の `_mode_claims()` が固定の日本語文を返す。
- modeごとのsource scopeやcritic契約がAgenticRAG本体へ十分統合されていない。

推奨実装:

- mode policyを単一テーブル化し、allowed sources、required output、fallbackを定義する。
- Exploreは異なる機構を実際の生成結果で3件以上返す。
- Challengeは競合仮説と最強反証にcontradicting citationを要求する。
- Design/UpdateをExperimentPlan/BeliefEventへ接続する。

### 7. 比較・Research GapはEvidenceLinkへ未接続

根拠:

- comparison rowのevidence statusが実質 `unresolved` のまま。
- gap snapshotはpaper/chunk/page/excerptで、SourceVersion/SourceSpan/verbatim quoteではない。
- CI-007の `done` 表記は受入条件より強い。

推奨実装:

- comparison生成時にchunkからSourceSpanを解決し、EvidenceLink snapshotを付与する。
- 解決不能時だけ理由付き `unresolved` にする。
- 保存比較のcitation snapshotがsource再取り込み後も解決できることをテストする。

### 8. OpenAPIとフロント型が未同期

現状:

- library page、paper decision、bulk tags、ideas、experimentsなどの新規APIが保存済みOpenAPIへ揃っていない。
- Library clientは独自fetch型で契約差分を回避している。

推奨実装:

```powershell
cd frontend
corepack pnpm run openapi:generate
corepack pnpm exec tsc --noEmit
corepack pnpm run test:unit
corepack pnpm run build
```

- 生成後、手書きLibrary型/clientを生成型へ置換する。
- OpenAPI driftをCIで検出する。

### 9. 共同レビュー機能は未実装

CI-015のclaim単位comment、担当、review verdict、Decision、EvidenceLink anchor、引用付きReport exportに対応する専用model/API/UIがない。

推奨最小範囲:

- ReviewThread、ReviewComment、ReviewDecisionをworkspace scopedで追加。
- anchorはResearchRun claim IDまたはEvidenceLink IDとする。
- owner/editorのみdecision確定、viewerはread-only。
- Markdown reportにclaim、verdict、理由、verbatim quote、source locatorを含める。

## P2: 性能・運用品質

### 10. 通常検索がrequestごとに全chunkを走査する

`backend/app/main.py` の `_answer()` は対象paperをロードし、全chunkをPython listへ展開する。lexical search、hybrid fallbackもrequest pathで全候補を走査する。

推奨実装:

- PostgreSQL FTSでtop-N候補を先に絞る。
- embeddingがある場合はpgvector候補とRRF統合する。
- SQLiteはテスト/開発用のbounded fallbackに限定する。
- 100/500/5000 paper fixtureでp50/p95、Recall@kを記録する。

### 11. Operations statusの設定異常が500になり得る

環境変数を直接 `int()` へ渡している箇所があり、非数値設定を警告ではなく500へする可能性がある。安全なparserとinvalid-config testを追加する。

## UIから利用できない、または導線が弱い機能

以下はbackend資産があるが、主要UIから一連の操作を完了できない。

- ResearchQuestion / SourceSetの十分な編集管理。
- ResearchRunの作成、実行履歴、cancel、再表示。
- HypothesisCardの構造編集とreview状態遷移。
- Discovery review queueの採否。
- Belief Ledgerの履歴表示。
- Idea Inbox専用一覧とHypothesis昇格。
- Experiment Plan / Result / export。
- Operations status。
- claim単位共同レビュー。

次担当は、backend APIの追加数ではなく、「研究問い→run→根拠確認→仮説→実験→結果→belief更新」をUI上で完了できる縦切りを1本ずつ仕上げる。

## 推奨実装順

### Work package A: 回帰と契約を正常化

- SourceSet FK失敗を修正。
- stream testを正しいWorkspaceContextへ更新。
- OpenAPI再生成、型検査、build。
- backend全テストをgreenにする。

完了条件: backend全テスト成功、frontend type/unit/build成功、OpenAPI driftなし。

### Work package B: 反証可能な通常RAG

- query→graph seed検索。
- paper/graph/contradiction RRF。
- 共通Citation/EvidenceLink DTO。
- CI-014 fusion/contradiction評価。

完了条件: contradiction recall、quote exact match、workspace boundaryを自動テストで確認。

### Work package C: Idea→Hypothesis→Experimentの縦切り

- Idea migration補強、専用UI、PATCH、promote anchor snapshot。
- Experiment Plan migration補強、result/history、versioned export。
- Design/Update modeとの接続。

完了条件: UI操作だけでIdea保存から実験結果記録まで完了し、全資産がResearchRunとEvidenceLinkへ戻れる。

### Work package D: ReviewとReport

- claim comment、assignment、verdict、Decision。
- EvidenceLink引用付きMarkdown report。
- viewer/editor/owner認可テスト。

## 台帳状態の推奨修正

- CI-002: `done` → `validating`（SourceSet回帰失敗）。
- CI-005: `done` → `validating`（mode本体統合不足）。
- CI-007: `done` → `validating`（EvidenceLink未接続）。
- CI-008: `in_progress`のまま。
- CI-010: `intake` → `validating`ではなく `in_progress`（部分実装、テスト/UIなし）。
- CI-011: `intake` → `in_progress`（RRF helperのみ）。
- CI-013: `done` → `validating`（回帰失敗、OpenAPI未同期）。
- CI-014: `done` → `validating`（主要指標未測定）。
- CI-015: `intake`のまま。
- CI-019: `in_progress`のまま。

## 注意事項

- 作業treeには多数の未コミット・未追跡変更がある。既存変更を保持し、機能単位にレビュー可能なcommitへ分割する。
- `.env`、`frontend/.env.local`、`backend/data/` は閲覧・commitしない。
- OpenAI、Semantic Scholar、OIDCを使う通常テストは必ずmockする。
- PostgreSQL migration、Celery worker、OIDC、OCR、S3/R2を未検証のまま本番確認済みと報告しない。
