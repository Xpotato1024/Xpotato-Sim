---
status: supporting
owner: viewer
last_verified: 2026-10-01
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/operations/product-viewer-wasm-scene-renderer.md
  - research/logs/2026-10.md
---

# 操作画面v1.0のsoftware検証と未測定範囲

## 条件とコード検証

#564/#587、基点`38a648f4ab841f357d744903aff6b3e3bd6bc36e`の専用worktreeを使用した。
元lockの正規npm依存で、通常の`npm test`全30 entry（既存29＋追加1）、TypeScript型検査、production buildを確認した。
最終の表示防御修正後は再compileと追加entryのfocused testを実施した。既存testの削除・skip・閾値緩和はない。
`operationPresentation.test.tsx`は角度0/-0/±45/±90/±135/±180/±179/±210と非finite/欠測、
compiled address・Robot subset/freejoint、左右namespace順、別epochの針消去、source切替、
standard否定・session不一致・stale、backend Z符号保持、不正な表示sampleの例外隔離を検証する。
追加entryは通常のnpm testへ登録し、表示componentからCSS import副作用を除いた。

## ブラウザ検証の方法

Windowsの専用Chromium 149.0.7827.55とSwiftShader、task専用profile・loopback portを使用した。
固定production buildを既存Workbenchへ接続し、`dynamic-cube-drop`と
`tests/fixtures/trial_gamepad/short-movement.json`、5 ticksの有限software試行を用いた。
Python環境はinterpreterだけ使用し、今回のworktreeとFastArm coreの実import元を確認した。
物理device、serial、OSC、実機出力は使用していない。browserのsandbox policyも変更していない。

## 確認結果

最終の固定buildで、1440×900・1366×768のSetup/Operate/Single/Assistを実ブラウザで確認した。
両基準サイズでpage縦scrollがなく、停止要求は画面内、canvasは1個、Assistは重ならない3描画矩形である。
文字色・背景・選択状態をcomputed styleでも照合し、旧CSSによる白文字上書きを修正した。
主視点drag、Single/Assist往復のcamera復元、補助pane操作の非干渉を確認した。
camera座標の厳密等値assertは、無操作のOrbitControls更新にも約3.2e-15の丸め差があるため不適切だった。
原因を別の無操作計測で確認後、検証scriptでは全camera/target座標を1e-12以内で比較した。productionの数値処理は変更していない。
同一modelのまま表示切替を行い、WASM module/model/data、OrbitControls、rAF、socket、入力timerの数が増えないことを確認した。
5 ticksの有限試行、結果保存済み表示、Setupへの復帰まで成功した。

既存ProductViewerの`transport-payload-v0-input`へ妥当なsoftware payloadを順次読み込み、DOMの値をassertした。
Gamepad generic index、KeyboardのKeyW押下/KeyS非押下、Selfrionette CH1..CH7=[1,2,3,-2,0,4,5]、
未知fixture、最後のGamepad staleでactive表示が消えることを確認した。
標準Gamepadの物理名称とZ符号ラッチはunit/markup検証であり、今回のブラウザpayloadは実device接続の証拠ではない。
最初のsource検証scriptはfile inputを誤選択し全値未取得だったため無効とした。正しいdata-testidと値のassertを加えた再検証だけを採用した。

680×600、720×450とDPR=2の画像も取得した。720×450は縮小viewportの検査であり、実際のブラウザ200%zoom検査ではない。
実200%zoom、全障害・readonly遷移、実Gamepad/Selfrionette/Keyboardの統合操作、利用者評価は未完了である。

## 性能の限定測定

準備済み静止scene、1440×900、DPR=1、SwiftShaderで各180 sampleのrAF間隔を測定した。
Singleはp50=66.7 ms/p95=66.8 ms、Assistはp50=133.3 ms/p95=150.0 msだった。
これはsoftware描画環境のフレーム間隔であり、native GPUの処理時間、live入力遅延、device-to-photonではない。
この条件のreceive-to-apply counterはnullで、入力から表示までの遅延は未測定である。実運用性能の保証には使用しない。
Quad、研究camera/layout/policy freeze、feedback非公開policyの全表示への適用も今回の完成範囲に含めない。

証拠はtaskの`parent-final-acceptance`にJSONとPNG、型検査・通常npm test・focused test・buildのログとして保持した。
