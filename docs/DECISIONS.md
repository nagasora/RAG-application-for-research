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

## D-20260720-11 研究ActionはIdeaを置き換えず、作成時点の出所を保持する

- Status: accepted
- Linked items: CI-027
- Context: Ideaを単純なTODOへ書き換えると、発散した仮説・推測と実行作業を区別できず、親Idea・ResearchRun・claim・原典根拠の追跡も失われる。Knowledge GraphとExperiment Planから作る作業も同じ監査境界で扱う必要がある。
- Decision: `ResearchAction`を独立レコードとして追加し、親IdeaからResearchRun・claim・claim snapshot・SourceSpanを継承する。必要に応じてEvidenceRef、Graph node、Experiment Planをworkspace内で検証して接続する。Idea分解は「根拠確認・反証検索・識別可能な試験設計」の3件を`inference`として生成し、初期の人間判断を`unreviewed`に固定する。採用・保留・却下、期限、進捗はAction側で更新する。
- Alternatives: Ideaを`todo`へ直接変換する、未追跡のタスク管理機能を追加する、AI生成Actionを自動採用する。
- Consequences: データとAPIは増えるが、研究タスクを根拠・仮説・実験に戻して監査できる。Action生成は研究判断を確定せず、人間による判断が必須になる。
- Date: 2026-07-20

## D-20260722-12 研究対話は明示したsource scopeと生成時間を優先する

- Status: accepted
- Linked items: CI-028, CI-029
- Context: 論文未選択が「解析済み全件」と解釈され、ResearchRunに保存する範囲と検索APIへ渡す範囲が一致していなかった。また稼働Dockerの16秒生成上限と25秒全体期限がホスト側の45秒設計より短く、回答生成が時間切れになっていた。長い会話ではページ全体のスクロールによりsource選択と根拠確認も画面外へ消えていた。
- Decision: 新しい対話では1〜5件の論文を明示選択し、Run・preview・SSE・検索のsource IDを一致させる。検索候補とLLM入力にhard boundを設け、45秒の全体期限から約30秒を生成へ予約する。Askは固定シェルとresponsive drawerを使い、会話だけを独立スクロールさせる。API識別子は維持し、利用者向け表示とエラーだけを共通の自然な日本語へ変換する。
- Alternatives: 未選択を全論文のまま維持する、モデルを変更する、全工程を単一タイムアウトで競合させる、通常のページスクロールを維持する。
- Consequences: 質問前にsource選択が一操作増えるが、検索範囲と監査記録が一致し、論文数が増えても入力サイズと生成時間を予測できる。既存クライアントはpaper IDを明示する必要があり、Run scope不一致は409で再作成を促す。
- Date: 2026-07-22

## D-20260722-13 既存workspaceを研究プロジェクトとして再利用する

- Status: accepted
- Linked items: CI-030
- Context: DB、membership、APIには複数workspaceの認可境界がすでにあるが、切替操作が小さく見つけにくく、ライブラリ検索やアイデア下書きなど一部のクライアント状態が切替後にも残り得た。別のProjectモデルを追加すると、論文・対話・根拠の所属境界が二重になる。
- Decision: workspaceを利用者向けには「研究プロジェクト」と表示し、既存の作成・一覧・名前変更APIと`X-Workspace-ID`境界を使う。最後に開いたIDは端末のlocalStorageへ保存するが、サーバーのmembershipを常に正とし、アクセス不能なら個人プロジェクトへ戻す。切替時は進行中要求を中止し、プロジェクト固有の画面を再マウントして一時状態を破棄する。
- Alternatives: 新しいProject tableとmigrationを追加する、サーバーに利用者ごとの「現在のプロジェクト」を保存する、切替時に既存データを新プロジェクトへ複製する。
- Consequences: DB migrationやAPI契約変更なしで複数研究を分離できる。選択状態は端末ごとなので別端末では個人プロジェクトから始まる場合があり、共有プロジェクトの作成・メンバー管理権限は既存workspace roleに従う。
- Date: 2026-07-22

## D-20260727-14 外部検索結果は明示採用時に要旨の出所と範囲を固定する

- Status: accepted
- Linked items: CI-031
- Context: 外部検索結果をそのまま全文論文としてLibraryへ入れると、providerのlive応答が変化した際に取得内容を再現できず、要旨をPDF本文やページ根拠のように誤認させる。既存のarXiv/DOI登録はprovider失敗時にも空のPaperを作成し得て、識別子表記差による重複も防げない。
- Decision: 初期providerをSemantic Scholarに限定し、検索結果は一時表示だけにする。owner/editorが明示選択した候補だけを再取得してLibraryへ採用し、provider、取得時刻、license、rate limit policy、検索条件、応答snapshotをaccepted DiscoveryItemとしてPaperへ接続する。DOI・arXiv・Semantic Scholar IDをworkspace内で正規化して重複を防ぎ、要旨だけのPaperとCitationは`abstract_only` / `abstract`としてPDFページと区別する。provider失敗時はPaperを作らない。
- Alternatives: 検索結果を全件pending queueへ保存する、検索結果を即時自動採用する、公開PDFも自動取得する、OpenAIで検索語を翻訳する。
- Consequences: 採用操作時にproviderを再照会するため失敗や部分成功が起こり得るが、利用者が選んだ内容だけが監査可能な研究資産になる。全文根拠が必要な論文は別途原本ファイルを登録する。
- Date: 2026-07-27

## D-20260727-15 Deep Researchではなく学術provider横断検索を既定にする

- Status: accepted
- Linked items: CI-032
- Context: 長時間・高額な自律調査を日常の論文発見に使うと、検索のたびに費用と待ち時間が増える。一方、質問文を日英の学術検索語へ展開し、複数の学術metadata providerを統合すれば、候補発見に必要なrecallと出所を低コストで確保できる。
- Decision: 初期版はDeep Researchを使用せず、最大4件の日英query planをSemantic Scholar、OpenAlex、CiNii、J-STAGEへ並列送信する。CrossrefはDOI・訂正・撤回metadataの検証に限定し、provider順位はRRFで統合する。検索候補は短期Search Sessionにだけ保存し、明示採用時だけCI-031のPaper・DiscoveryItem来歴へ固定する。
- Alternatives: Gemini Deep Researchを毎回実行する、単一providerだけを使う、検索結果を自動でLibraryへ登録する。
- Consequences: 全Web調査ほどの網羅的レポートは作らないが、国内外候補を高速・低コスト・監査可能に取得できる。CiNii資格情報や一部providerがない環境では、利用可能providerだけで部分結果を返す。
- Date: 2026-07-27

## D-20260727-16 生成モデルとembeddingを分離しworkspace既定と実行時選択を監査する

- Status: accepted
- Linked items: CI-033
- Context: OpenAI固定の生成経路ではコストや用途に応じたGemini利用ができない。生成モデルの変更をembeddingへ連動させると、既存vectorとの互換性が失われ再indexが必要になる。
- Decision: API keyはサーバー環境変数だけで管理し、allowlist内のOpenAI/Gemini生成モデルをworkspace既定としてownerが設定する。editorはAsk・Discovery・Analysisの実行時だけ上書きできる。解決済みprovider/modelをResearchRun等へ不変保存し、embedding provider/modelは別設定として維持する。
- Alternatives: ブラウザへAPI keyを保存する、利用者が任意モデルIDを送る、生成モデル変更時にembeddingも切り替える。
- Consequences: providerごとのadapter・エラー分類・usage計測が必要になるが、秘密情報を露出せず費用と再現性を管理できる。Gemini未設定時もOpenAIまたは既存local fallbackを維持できる。
- Date: 2026-07-27

## D-20260727-17 実験結果と著者考察を分離し図表と原文へ固定する

- Status: accepted
- Linked items: CI-034
- Context: 現行比較は本文先頭付近の一文をheuristicに拾うだけで、実験条件・測定値・結果・考察の区別やセル単位の根拠がない。図とcaptionもページ内の近接推定だけで、モデル解釈を科学的根拠として誤表示する危険がある。
- Decision: 全文PDFだけを対象に、原本hash・抽出器版・model・prompt versionで版管理したExperimentProfileを比較実行時に生成する。観測結果、著者解釈、限界を別型にし、exact quote SourceSpanまたは検証可能なtable cellを必須にする。図はpage・bbox・caption・DocumentElementへ接続し、厳密に対応しない図は同一ページの参考として区別する。
- Alternatives: 要旨だけから結果を推測する、図の見た目だけで数値を生成する、結果と考察を一つの要約欄へ混ぜる。
- Consequences: 抽出できない項目は未報告となり、候補はreview_pendingから始まる。モデル費用は比較を明示実行した論文だけに発生し、同一版の結果はcacheされる。
- Date: 2026-07-27

## D-20260728-18 外部URL・実効モデル・部分比較を不変な監査境界で扱う

- Status: accepted
- Linked items: CI-032, CI-033, CI-034
- Context: 学術provider由来のURLを未検証でリンク化すると危険schemeを実行し得る。選択した生成provider/modelだけを記録するとlocal fallback後の実体を識別できず、実験profileの一部生成失敗時に選択全件を保存しようとすると成功結果も残せない。また、監査・ExperimentProfileを持つmigration downgradeが黙示的にデータを破棄し得る。
- Decision: provider URLはbackendで絶対HTTPSへ正規化し、frontendでも同じ条件を満たす場合だけリンク化する。ResearchRunとgeneration auditには要求したprovider/model、回答・SSE・会話messageには実際に本文を生成したprovider/modelを保存し、local fallbackは`local` / `extractive`（検索語未展開は`local` / `raw-query-v1`）として明示する。実験比較は成功profileが2件以上なら成功分だけを保存し、除外した論文と安定した失敗codeを比較snapshotへ残す。保存要求は画面で比較したimmutable profile IDを明示し、各evidence locatorを対応するSourceSpan IDへ固定する。監査、検索session、workspace生成設定、ExperimentProfile、ResearchRun生成情報が非空のdowngradeは変更前に拒否する。
- Alternatives: provider URLをそのまま表示する、fallback時のmodelを空にする、全論文成功まで比較保存を禁止する、downgradeで監査データを無条件削除する。
- Consequences: 一部providerの不正・非HTTPS URLはリンクとして表示されず、旧回答では生成情報が不明のまま残る。部分比較は失敗論文を除外したことが明示され、downgrade前には監査データの退避または明示解決が必要になる。
- Date: 2026-07-28

## D-20260729-19 編集可能なMindMapをKnowledge Graphと分離し明示確定で接続する

- Status: accepted
- Linked items: CI-035
- Context: 現行のMind mapはKnowledge Graphの表示配置であり、単一root、親子順序、折りたたみ、部分木削除、生成候補の確定前レビューを表現できない。KnowledgeNodeとKnowledgeEdgeへ編集ツリーを混在させると、検証済み関係と構成用の親子関係が区別できず、削除時に研究資産を失う危険がある。
- Decision: MindMap、MindMapNode、ノード根拠をworkspace scopedな独立集約として保存する。論文・選択根拠からの初期生成、枝展開、Research Action生成はResearchRunへ候補を記録するだけとし、利用者が選んだ候補だけを原子的に保存する。生成ノードは`review_pending`を維持し、Knowledge Graphへは明示操作で根拠付きKnowledgeNodeを作成してリンクする。MindMapの部分木削除はNote、Research Action、KnowledgeNode本体を削除しない。
- Alternatives: Knowledge Graphのnode/edgeをMindMapの保存先として共用する、生成結果を自動保存する、MindMapとKnowledge Graphを常時双方向同期する。
- Consequences: 専用schemaとAPIが増えるが、構成上の枝と研究上の根拠関係を混同せず、LLM停止時も手動編集と既存研究フローを維持できる。Graph昇格後の内容は自動同期しないため、利用者はそれぞれの成果物を明示的に更新する。
- Date: 2026-07-29

## D-20260801-20 DOI直接登録はCrossrefを主取得元に限定して可用性を上げる

- Status: accepted
- Linked items: CI-036
- Context: 直接DOI登録はSemantic Scholar単独照会に失敗すると利用不能になり、利用者には取得中か失敗かが判別しづらかった。CrossrefはDOIの書誌情報を扱えるが、横断検索の順位へ加えるとCI-032のprovider設計と検索結果の性質を変えてしまう。
- Decision: 明示入力されたDOIの書誌・要旨登録だけはCrossrefを主取得元にし、Crossrefが利用不能な場合に限りSemantic Scholarをfallbackとして利用する。両者の自動retryは行わず、実際に使用したproviderのsnapshot、license、rate policy、取得時刻を`abstract_only` Paperのprovenanceに固定する。CrossrefはCI-032の検索RRFへ加えない。
- Alternatives: Semantic Scholar単独を維持する、DOIを全文PDFとして自動取得する、Crossrefを通常の検索providerにも追加する。
- Consequences: DOI直接登録の可用性は上がるが、取得結果は書誌・要旨の範囲に留まる。提供元が両方利用不能または識別不能ならPaperを作成せず、利用者が再試行できる構造化エラーを返す。
- Date: 2026-08-01

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
