---
status: canonical
owner: operations
last_verified: 2026-09-28
canonical_for:
  - backend / viewer startup guide
  - browser WebSocket connection guide
  - backend / viewer startup procedure
related:
  - README.md
  - apps/mujoco-viewer/README.md
  - docs/contracts/launch-profile.md
  - docs/operations/browser-visual-smoke.md
  - docs/operations/live-viewer-smoke.md
  - docs/operations/runtime-dry-run.md
  - docs/operations/unified-cli.md
  - docs/operations/websocket-host-port-contract.md
---

# Backend / viewer 起動手順

MuJoCo backendがsimulation stateを所有し、browserは受信qposの描画と入力取得を担当する。
実機の測定値や操作許可はこの起動手順では生成しない。

## 初回セットアップ

リポジトリrootで実行する。依存のinstallと毎回の起動を分ける。

基盤名の移行後は `xpotato-sim` CLIを使う。旧形式のprofile JSONも引き続き検証して読み込める。

```powershell
uv sync --frozen --group dev
npm --prefix apps/mujoco-viewer ci
```

## 通常の起動と終了

同じrootから一つのコマンドで起動する。別terminalや別directoryへの移動は不要。

```powershell
uv run xpotato-sim app --profile sim-gamepad
uv run xpotato-sim app --profile sim-keyboard
uv run xpotato-sim app --profile replay-sweep
```

上のコマンドは用途に応じて一つを選ぶ。Webとbackendの起動完了後に、接続先と入力providerを
含む正しいURLを一度だけ開く。simulationの配布profileは約5分、replayは約10秒の有限実行で、
backend完了に伴いWebも終了する。終了後の画面に残った姿勢は最終受信値であり、live stateではない。
途中で終了する場合は起動terminalの**Ctrl+C**を使う。ブラウザtabを閉じただけではsession終了とは
ならず、入力のstale処理は既存backendの契約に従う。

起動時にprofile名・実行モード・設定digest・有限実行時間・URLを表示する。
子processのログは終了時に末尾を表示する。ログは一時directoryに限定し、研究実験のlossless記録や
永続ログではない。通常終了は0、エラーは非0、operator interruptionは130を返す。

## 設定検査と起動確認

```powershell
uv run xpotato-sim profile sim-gamepad
uv run xpotato-sim app --profile sim-gamepad --check
uv run xpotato-sim app --profile sim-gamepad --startup-check
uv run xpotato-sim app --profile sim-gamepad --no-browser --web-port 5178 --backend-port 8768
```

`profile`はJSONと既存resolverの検査・表示だけを行う。`app --check`はさらにsource checkoutとの
一致、Nodeとviewer依存fileを検査するが、port probe、Source、server、browserを開始しない。
`--startup-check`は両serverを実際にloopbackで起動して終了するが、WebSocket viewerへ接続しない。
設定ファイルの形式と上書き規則は`docs/contracts/launch-profile.md`を正本とする。

## 障害とprocess所有権

port競合は開始前に拒否する。自動的なport変更や既存processの終了はしない。
競合が開始前検査後に発生した場合も、ViteのstrictPortとbackend bind errorで失敗させる。
片側の起動失敗・予期しないWeb終了・Ctrl+Cでは、自分が起動したworkerだけを後始末する。
Windowsはjob objectでworkerと子孫を束ねる。venv redirectorと実PythonのPIDは同じとは仮定せず、
実workerも外部処理の開始前に同じjobへ所属する。parent handleの消失でも子孫を残さない。
POSIXでは独立process groupへ終了signalを送り、有限待機後に強制終了する。

これはforegroundの開発用launcherであり、daemon、service、長時間watchdog、実機非常停止ではない。
実機出力、serial、OSC、operator permissionは追加しない。profile v1はnumeric loopbackに限定する。
LAN/TLS/auth/deploymentは対象外で、必要な手動配信は次の低位手順を参照する。

## 低位CLIと個別開発

backendだけを使う既存CLIは維持する。

```powershell
uv run xpotato-sim replay --robot fast_arm --steps 3 --preset sweep_x
uv run xpotato-sim viewer --robot fast_arm --input-source viewer --steps 18000 --interval-s 0.016667 --grace-period-s 60
npm --prefix apps/mujoco-viewer run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

最後の2つは別terminalで起動する低位の開発手順であり、日常起動の必須操作ではない。
WebSocket endpoint付きURLを開く。bind addressとbrowser-visible hostの区別は
`docs/operations/websocket-host-port-contract.md`を参照する。

```text
http://127.0.0.1:5173/apps/mujoco-viewer/?websocketUrl=ws://127.0.0.1:8766
```

既存PowerShell `scripts/viewer/run-browser-viewer-smoke.ps1`は、引数から一時replay profileを作り
同じlauncherへ委譲する。独自process管理は行わない。`-NoBrowser`はstartup-check、`-OpenBrowser`は
明示openに対応する。v1のloopback、正の時間値などの検査に従い、旧版の広いhost/zero間隔を
無検証で通さない。LAN配信は上記低位CLIへ明示的に分ける。

## 左右独立の1スティックXYZ操作

`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`とFastArmのGamepad profileはstick XY + analog trigger Zを使用する。
起動時はFastArmの`+X`前方と操作方向を合わせた`operator` camera presetを使い、stick上を前進、右を胴体右へ対応させる。
cameraを手動回転しても入力Mappingはcamera-relativeに変更しない。
操作と表示は[Gamepad trigger操作契約](../contracts/gamepad-trigger-control.md)を参照する。
旧XY/XZ切替は[Gamepad平面操作契約](../contracts/gamepad-plane-control.md)の互換経路である。

## 単腕・双腕のモデル選択（#574 / #580）

```powershell
uv run xpotato-sim app --profile fast-arm-single-gamepad
uv run xpotato-sim app --profile fast-arm-left-gamepad
uv run xpotato-sim app --profile fast-arm-right-gamepad
uv run xpotato-sim app --profile fast-arm-bimanual-gamepad
```

用途に応じて一つを選ぶ。原型単腕・左単腕・右単腕・双腕を同じLaunchProfile/v2で選択し、
同じprovider構築・WebSocket loop・描画経路へ渡す。`profile`で解決結果とモデルconfiguration digestを
表示できる。位置・角度はprofileへ複写せずRobot側のモデル定義に保持する。

左/右は胴体座標の+Y/-Yであり、画面や操作者から見た左右とは別である。
各profileの`coordination.side_to_endpoint`がスティックから手先への対応を明示し、表示にも適用先IDを出す。
左単腕を右stickに割り当てることもモデルを変更せず明示設定できる。未使用stickは診断のみ。
同側肩button 4/5でXYからXZへ切り替え、中立確認まで対象側だけ速度ゼロとする。
実Gamepadのbutton/axis配置がstandard想定と一致するかは別途確認する。

既定portは5174/8767、18000 stepの有限実行。`--check`、`--startup-check`、`--no-browser`、port overrideは共通。
単腕・双腕どちらも生成モデルは`inputStartup=scene`で描画準備後に入力を開始し、中立heartbeatを有効にする。
旧v1の単腕keyboard/replay等は既存の開始順序・設定digestを維持する。

左・右肩の取付姿勢と回転中心は[FastArm assembly契約](../contracts/fast-arm-assembly.md)を参照する。
肩中心は(0,+/-0.145,0.7) m。間隔0.290 mはCAD画像の穴間寸法を縮尺として推定した暫定値で、高さ0.7 mは合成値のままである。98 mmを肩間隔へ直用せず、plate/shoulder基準点の違いを補正した。実機寸法の確定値ではない。従来のsource原点へのRx(+/-30度)だけの取付は誤りである。

初回は新鮮な中立を待つ。stale・切断・不正入力・model/joint不整合では選択モデル全体のfaultを保持し、
残りの有限session中は固定姿勢と理由を表示する。正常入力復帰だけで再開しない。
再試行はCtrl+Cで終了し、新しいsessionで起動する。停止表示は実機非常停止ではない。

本経路は運動学診断で、接触・servo・ばね力評価は別である。physical outputはdisabled。
OSC/serial、複数視点同時UI、GUI task/spawnは追加しない。


カメラの固定方向は全モデル共通に`XZ面`・`YZ面`・`XY面`と表示する。モデルごとに曖昧な「正面」を
自動推測しない。今回の胴体座標では`YZ面`が胴体正面に対応する。表示labelだけの明確化で、物理座標や
入力方向をcameraに追従させる補正は行わない。


肩取付は取付板の上端が内側・下端が外側になる向きへ訂正した。モデル更新後はsessionを終了して再起動し、
生成resource/model digestを更新する。Web画面だけの再読込みでは既存backendのモデルを作り直さない。
新しい全4モデルには旧単腕と同じz=0の床をbase sceneから合成する。床は両腕分重複させない。
取付角の変更でhomeのworld姿勢は変わるが、既存joint homeを写真に合わせて変更していない。
