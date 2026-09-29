---
status: supporting
owner: runtime
last_verified: 2026-09-29
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/contracts/finite-trial-runtime.md
---

# Workbenchの反復・入力・資源検証

## 範囲と条件

#592のsoftware受入検証。参加者実験、実Gamepadの操作受入、実機性能の証拠ではない。
基点は#594の`29cc7db25dbbdd24fbdaf7cf2a63786a8d1326a9`。sourceを固定してVite production buildを生成し、
同じbuildを`workbench --web-dist`から配信した。検証buildのentryは`index-BokLzWEq.js`。
Windows上のheadless Edge、ANGLE/SwiftShader、1440×1000を使用。HMRは使用しない。
通常動作のallocation profilingは無効で、Python heapと未exportのWASM heapはnullとして扱う。
JS heapはCDPでGC後に取得し、renderer/native資源の所有数と併記した。

## 再試行とモデル切替

`dynamic-cube-drop`、明示fixture `tests/fixtures/trial_gamepad/short-movement.json`、5tickで30試行を実行した。
GUIのprofile選択、準備、開始、結果、再試行を実際のcontrol service/worker/TrialRunnerへ通した。
同条件ではmodel/native build数は1のまま。trial IDは毎回別で、結果は上書きしない。

続けて`contact-debug-single`と`dynamic-cube-drop`を20往復（40切替）した。
最終的なmodel build/deleteは41/40、live model/dataは1/1。さらにfixed-contactとpushも同じ入口で実行した。
scene切替後のasset・joint/address・同一epoch描画を確認し、ページreloadは行っていない。
結果一覧は32件、command履歴は128件の上限で止まり、rAF、resize listener/observer、OrbitControls、socket、入力timerは各1。
WASM moduleは1、VFSの残存fileは0。GPU geometryは切替warm-up後22、textureは1、programは7で増殖しなかった。
最後のpushは台を追加する別sceneのため、geometry/material数が1増えることとリークを区別する。

| 測定 | 標本数 | p50 | p95 |
| --- | ---: | ---: | ---: |
| 新規アプリ・browser起動から未選択状態の受領 | 5 | 3.4299 s | 4.0519 s |
| 新規起動で初めてのモデル準備 | 5 | 2.5951 s | 2.6146 s |
| 同条件retryの要求から初期scene準備完了 | 29 | 0.0270 s | 0.0771 s |
| 異なるモデル/sceneの準備 | 42 | 2.3878 s | 2.6111 s |

同条件の20→30試行でJS used heapは7,699,536→7,967,036 bytes。
モデル切替5→20往復では8,183,472→8,651,640 bytes、Python RSSは207,093,760→250,093,568 bytes。
履歴・測定用参照、native allocatorやbrowserの保持領域を含むため、RSS単独でリークの有無を断定しない。
所有数・GPU資源の安定性と、小規模な有界反復の観測であり、無期限や全条件のリーク不在の証明ではない。

## 入力と停止

fixtureを指定しない別起動で、browserの`navigator.getGamepads`へ合成deviceを供給し、
実際のfrontend poll→control message→backend経路を通した。端末に接続した実Gamepadの証拠ではない。
drop/fixed-contact/pushで開始前と入力欠測中にtickが進まないこと、新しい中立入力後にのみ進むことを確認。
raw axes変更も同じ経路へ送り、dropのdevice消失はtechnical_invalid、他2構成の明示停止はoperator_abortとして保存された。
終端後のworld時間・qposは固定され、retryは同じ条件digestと新しいepochで初期状態へ戻った。
接続を閉じて再接続した場合は閲覧へ戻り、明示claimまで操作しない。再接続だけでは試行を再開しない。

新規起動測定は別process/browserで5回実行し、OSのdisk cache消去までは行っていない。
Mapping単独のGUI parameter変更は#593のeditor対象であり、本preset選択経路の計測へ混在させない。
listener/timer数はowner側の管理数であり、browser全体の全listenerを列挙した値ではない。

## 修正と検証の境界

固定buildの再試行で、既存asset directoryへwrite_public_treeを再実行して失敗する経路を再現した。
通常終端後と明示STOP後のretryは、検証済みの同一viewer resource集合を再公開し、fileを再生成しないよう修正。
実worker回帰ではfile内容とmtime不変、モデル生成1回、新epoch、resetを確認した。
通常WebSocket closeは所有権解除・停止監督へ反映し、正常切断をhandler例外として出力しない。

6-sol指定の独立レビューは、CodexがChatGPT認証でモデル非対応の400を返して実行できなかった。
Astraへの代替は行っていない。実装/自動検証の完了と、指定レビューの未充足を区別してPRへ記録する。
