# Design QA — 研究対話の固定3ペイン

## 比較対象

- source visual truth path: `C:\Users\長松　蒼空\OneDrive\Pictures\Screenshots\スクリーンショット 2026-07-22 213102.png`
- implementation screenshot path: `C:\Users\長松　蒼空\.codex\visualizations\2026\07\22\019f88e0-f693-78d2-be61-06429aabfafd\paperpilot-ask-1920-final-v2.png`
- local implementation: `http://localhost:3000/`
- viewport: 1920 × 894 CSS px
- source pixels: 1920 × 894
- implementation pixels: 1920 × 894
- CSS size: 1920 × 894
- device density: 1 CSS px = 1 image px; density normalization was not required
- state: ライトテーマ、解析済み論文9件、検索対象5件、保存済み研究対話1件、最新回答の根拠5件、会話の途中を表示

## Full-view comparison evidence

添付画面と最終ブラウザ画面を、同じ1920 × 894の比較入力で確認した。グローバルナビ、研究対話ペイン、中央会話、最新回答の根拠という横方向の情報階層、緑系の配色、本文と小さな状態ラベルのタイポグラフィ、引用カードの密度は既存のデザイン言語を維持している。

意図した差分として、左ペイン下部を「検索対象」の1〜5件選択へ変更し、中央下部に固定入力欄を表示した。元画像で発生していたページ全体の縦スクロールはなくなり、中央の会話だけがスクロールする。右の根拠と左の研究対話は1920px表示中に動かない。

## Focused region comparison evidence

追加の切り抜き比較は行っていない。両画像が同一ピクセル寸法・同一密度で、左の検索対象、中央の回答・入力欄、右の引用カードの文字と境界線を原寸で判読できたためである。細部はブラウザのDOM計測と操作検証で補完した。

## 必須の品質面

- fonts and typography: 既存の和文UIフォントとserif見出しを維持。状態ラベル、本文、引用カードの階層と行間に崩れなし。
- spacing and layout rhythm: 1920pxでは 272px / 280px / 可変中央 / 300px の構成。中央会話は高さ469px、固定入力欄は325.5pxで、初回比較時の会話189.5pxから改善。
- colors and visual tokens: 既存の深緑、生成り、淡い緑、amberの注意状態を継承。ライト／ダークの既存テーマトークンを壊していない。
- image quality and asset fidelity: この画面の主要UIにラスター画像はなく、既存のアイコンライブラリをそのまま使用。代替画像やCSSアートは追加していない。
- copy and content: `Evidence`、`sources`、`Research cockpit`等を「最新回答の根拠」「検索対象」「研究対話」へ統一し、タイムアウト・未検証状態を断定しすぎない日本語にした。
- accessibility and interaction: `role="log"`、`aria-live`、Escape、focus trap、focus restorationを維持。drawer表示中だけbody scrollを止める。

## Responsive and interaction evidence

- 1920 × 894: 3ペイン表示。bodyは894/894でページスクロールなし。中央ログだけ `scrollTop` が変化し、左右ペインと入力欄の矩形は不変。
- 1536 × 864: 3ペイン表示、左右drawerボタンは非表示。
- 1280 × 800: 左ペイン固定、根拠はdrawer。根拠ボタンを表示。
- 1024 × 768: 左ペインと根拠をdrawer化。両方のボタンを表示。
- 390 × 844: 両drawer、固定入力、下部ナビを表示。bodyは844/844でページスクロールなし。
- 両drawerでEscape閉鎖、呼び出しボタンへのfocus復元、body scroll lock解除を確認。
- drawerを開いたまま1280px／1536pxへ拡大した場合も、modal state・focus trap・body lockが解除された。
- browser console: error 0件、warning 0件。

## Findings

現時点で、対応が必要なP0/P1/P2差分はない。

## Comparison history

1. Iteration 1 — blocked
   - [P1] 「今回の目的」が5枚の説明カードとして常時展開され、1920pxでも入力欄が605pxを占め、中央会話が189.5pxまで縮んでいた。
   - Fix: 目的選択を横スクロール可能なコンパクトなpill切替へ変更し、選択中の説明だけを1行表示した。
   - Post-fix evidence: 中央会話469px、入力欄325.5px。添付画面の会話中心の密度を保ちつつ、要求された固定入力を維持した。
2. Iteration 2 — blocked
   - [P1] 小さい画面でdrawerを開いたままブレークポイントを跨ぐと、見えないmodal stateとfocus trapが残る可能性があった。
   - Fix: `xl` / `2xl`到達時に対応drawer stateを閉じ、body lockとfocus trapを解除した。
   - Post-fix evidence: 390→1280、1280→1536の実ブラウザ操作でdialog 0件、body overflow復帰を確認した。
3. Iteration 3 — passed
   - 同じ1920 × 894のsource/implementation比較と5 viewport操作を再実施。対応が必要なP0/P1/P2は残っていない。

## Follow-up polish

- P3: モバイルの目的選択と定型質問は横スクロールで利用できるが、利用者テストで発見性が低ければ端のフェード表示を追加できる。

final result: passed
