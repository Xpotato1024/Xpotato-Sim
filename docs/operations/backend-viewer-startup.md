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

新しい`sim-gamepad-left-xyz`／`sim-gamepad-right-xyz`と、XY/XZ切替・中立復帰・取得session・表示の規約は
[Gamepad平面操作契約](../contracts/gamepad-plane-control.md)を参照する。現行の片腕へ選択した片側を適用する段階であり、双腕モデル完成ではない。

## 双腕Gamepad Viewer（#574）

```powershell
uv run xpotato-sim profile fast-arm-bimanual-gamepad
uv run xpotato-sim app --profile fast-arm-bimanual-gamepad --check
uv run xpotato-sim app --profile fast-arm-bimanual-gamepad
```

配布profileは原型とY鏡映の2腕・8関節を同じsceneへ表示する。左stickは左arm、右stickは右armに
対応し、同側肩button 4/5でXYからXZへ切り替える。切替後はそのstickを中立に戻すまで速度ゼロ。
反対側は継続操作できる。実Gamepadのbutton/axis配置がstandard想定と一致するかは別途確認する。

既定Web/WS portは5174/8767。約5分相当の18000 tickで終了し、Ctrl+Cでも両workerと一時資源を閉じる。
単腕と同じ`--no-browser`、`--web-port`、`--backend-port`、`--startup-check`を使える。
取付のX軸±30度、左右位置±0.4 mは既存の合成診断配置で、実機校正値ではない。

初回は新鮮な中立を待つ。stale・切断・不正入力・model/joint不整合で全体faultを保持する。
fault後は両腕の姿勢を止め、残りの有限session中に理由を表示する。入力復帰だけでは再開しない。
設定を確認し、Ctrl+Cで終了して新しいsessionを起動する。入力欄の停止表示は実機非常停止ではない。

これは`coordinated_joint_position_kinematic/v1`の運動学診断であり、接触・servo・ばね力評価ではない。
`physical_output`は常にdisabled。OSC/serialは開かず、複数視点UI・GUI task/spawnも追加しない。

双腕launcherはURLへ`inputStartup=scene`を付け、8関節の描画準備ができてから入力取得を開始する。
モデル読込み中に最初の中立入力だけが先行して監視期限切れになることを避けるためである。
中立heartbeatも開始時から有効にし、準備後に取得sessionを作り直さない。
単腕の既存URLは接続成立から入力を開始する従来動作を維持する。
