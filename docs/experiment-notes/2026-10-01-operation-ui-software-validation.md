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
この静止条件のreceive-to-apply counterはnullで、入力遅延の測定には使用できない。後述の合成入力試験とは測定対象が異なる。実運用性能の保証には使用しない。
Quad、研究camera/layout/policy freeze、feedback非公開policyの全表示への適用も今回の完成範囲に含めない。

証拠はtaskの`parent-final-acceptance`にJSONとPNG、型検査・通常npm test・focused test・buildのログとして保持した。

## 最終補修と合成Gamepadから描画提出までの確認

初期PR head `2c454b7c3243584f9ec3e40e0ec5d5ceb42a9b86`に対する最終補修を実施した。
既存ViewerInputSourceは中立sampleにも`gamepad_inactive`を付けるため、最初の実経路試験で
正常な中立時の計器が消える反例を得た。表示だけの分類を修正し、接続・stale=false・zero_stateの
明示情報が揃った中立を表示する。切断、timeout、blurred、根拠不足を例外扱いしない回帰を追加した。
最初の1366×768では入力帯のclientHeight=94、scrollHeight=100だった。compact帯も104pxとして
再測定し、凡例・Z量・符号を欠かさず内部scrollとpage縦scrollがないことを確認した。
失敗した試験と修正後の成功を別のJSON/PNGとして保持した。productionの入力timeout、Mapping、physicsは変更していない。

最終試験はWindows / Chromium 149 / Intel UHD Graphics 630 / ANGLE Direct3D11、1440×900 / DPR=1。
GPU名は実WebGL contextから取得した。SwiftShaderの以前の試験と同じ測定環境ではない。
`dynamic-cube-drop`、通常のbrowser入力経路、ticks=6000、input-wait=15s、wall=120sの有限上限を使った。
実Gamepadではなく、試験専用browserにstandard配置の合成Gamepadを供給した。
開始前の未取得・明示開始・中立待ち・左右のstick/LT/RT・LB/RB押下中の符号保持・trigger解放時の
両側-Zへの更新・切断後の表示消去・明示retry・再度の中立待ちからのSTOPを確認した。
1366×768と1440×900で文字色、選択強調、三面、左右rail、入力帯と停止要求が共存する実画像を取得した。

| 測定 | Single | Assist |
|---|---:|---:|
| rAF間隔 p50 / p95（89間隔） | 16.7 / 16.7 ms | 16.7 / 16.8 ms |
| 合成入力変更→対応frame描画提出 p50 / p95（45件） | 66.6 / 67.0 ms | 66.4 / 67.1 ms |

入力計測は速度を発生させないAボタンを切り替え、既存40ms pollとbackendを経由した
sequence/frameをecho payloadとrenderer counterで照合した。各条件を順番に1回測った短期測定で、
誤差比較や性能優位性の検定ではない。CPUの全pane描画提出までで、GPU完了、表示走査、物理deviceの
遅延は含まない。rAF間隔もGPU frame処理時間ではない。90回のrAFから最初の時刻を除く89間隔を採用した。
両条件でWASM module/model/data、renderer用資産、OrbitControls、rAF、socket、入力timerの数を維持し、
renderedPaneCountだけが1から3になることを照合した。長期リーク不在や全GPUの性能は主張しない。

証拠は当該taskの`finish/input-live-v3`、表示修正の対照は`finish/accepted-browser`に保持する。
`finish/viewer-dist`は検証した固定buildであり、ソース・buildのhashをmanifestへ保存する。
既存の実device・実200%zoom・研究feedback freeze等の未達範囲は、このsoftware受入で置換しない。

## v1.1: Assist拡大・集中した入力帯・geometry framing

利用者の実操作スクリーンショットと追加指示に基づき、初期head `1ca3bb9613309ec2d23aed6a8b9bc1d796961fc5`
から表示だけを修正した。通常のnpm test全30 entry、型検査、production buildが成功した。
Z符号は同一backend sampleのtrigger量と確定符号を使用し、元のボタン押下表示の検証は診断componentへ移した。
そのうえで、主入力帯にボタンが存在しないこと、正負のZ縦バー、未割当・waiting・失効の欠測を追加検査した。
geometry framingは実mesh境界・画角・aspectを使い、並進、正負方向、狭いaspect、非finite/不正境界をpure testで確認した。

同じ固定buildをWindows / Chromium 149 / Intel UHD Graphics 630（ANGLE D3D11）で検証した。
1440×900、1366×768、1920×1080で、主:補助幅2:1、補助二面の同高（1px以内）、
境界1px、railと入力帯の下端一致、入力4計器の中央位置（中心差1px未満）、page/入力帯scrollなしをDOMでassertした。
画像で補助viewとRobotの拡大、関節/入力計器がsceneを覆わないことも確認した。
「全体」より「フォーカス」でカメラ距離が短くなり、どちらも観察方向を保持すること、
主paneのdrag、補助paneの非干渉、Single/Assist往復のcamera保持を実pointerで確認した。

合成standard Gamepadを既存40ms poll→backend→MuJoCo→rendererへ通した。
中立0、両側+0.2、trigger保持中のLB/RB押下後も+0.2、trigger解放時0、再押下時-0.2を、
Z計器の同一表示値で検査した。現bumperを直接Z符号へ読み替えていない。ボタン列は主入力帯にない。
切断時はXY点/Z塗りを消し、同条件の明示retryと中立待ちからのSTOPも成功した。
WASM module/model/data、描画資産、OrbitControls、rAF、socket、入力timerの所有数は表示変更で増えていない。

別の同build検証で、従来Viewerの既存payload consumerへGamepad generic、Keyboard、Selfrionette、unknown、staleを投入し、
KeyW押下/KeyS非押下、CH1..CH7=[1,2,3,-2,0,4,5]の実値を再確認した。Gamepad未標準時に物理名やZ値を補完しない。
さらに左単腕、右単腕、双腕、dynamic-cube-pushのprofileを同じWorkbenchで準備し、関節rail数1/1/2/2、
角度計数4/4/8/8、scene準備、焦点合わせを確認した。モデルごとの別rendererは追加していない。

証拠は当該taskの`v11/browser`と`v11/sources`のJSON/PNG、`v11/unit-build.log`、source/build manifestである。
これは合成入力によるsoftware受入であり、実device/参加者の操作性、全障害状態、実200%zoomの受入ではない。
v1.0の遅延測定値をv1.1の測定値として再掲しない。カメラはfit時の境界を保持し、移動して範囲外へ出た物体は自動追従しない。
Quad・研究表示freeze・非公開feedback policyの未達範囲は維持する。
