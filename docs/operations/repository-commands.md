---
status: canonical
owner: operations
last_verified: 2026-10-10
canonical_for:
  - repository operation command routing
related:
  - docs/operations/backend-viewer-startup.md
  - docs/operations/validation.md
  - docs/contracts/workbench.md
---

# repository操作入口

初回準備と通常Workbench起動は[root README](../../README.md)の
`just init/setup/build/run/dev/doctor`を使う。以下は準備済みrepository rootで実行する。
justの導入方法もroot READMEへ集約する。

## 操作の対応

追加recipeは`scripts/repository/commands.py`へargvを渡し、既存CLI/script/npm commandを呼ぶ。
option、default、検証、physics、保存形式の正本は既存実装である。
`just --list`で一覧、各recipeの`--help`で既存実装のoptionを確認する。

| just入口 | 委譲先 |
| --- | --- |
| `just profile` | `xpotato-sim profile` |
| `just app --profile sim-keyboard --no-browser` | `xpotato-sim app` |
| `just replay` | `xpotato-sim replay` |
| `just viewer-publisher` | `xpotato-sim viewer` |
| `just trial` | `xpotato-sim trial` |
| `just test` / `just test-python` | `pytest tests` |
| `just lint` | repositoryのRuff correctness検査 |
| `just typecheck` / `just launcher-typecheck` | 既存mypy対象 / local launcher |
| `just compile` | `compileall src tests scripts` |
| `just docs-check` | `validate_markdown_docs.py` |
| `just github-body-check` | `validate_github_body_structure.py` |
| `just viewer-test` / `just viewer-typecheck` / `just viewer-build` | Viewerのnpm test/typecheck/build |
| `just viewer-smoke` | `run_live_viewer_smoke.py` |
| `just browser-smoke --no-browser` | 一時replay profileと既存appのloopback startup/cleanup |
| `just loadcell-plot --input-path logs/vectors.txt` | 記録済みvectorのoffline CSV/PNG |
| `just selfrionette-dry-run` | 記録済みserial fixtureのoffline検証 |
| `just selfrionette-monitor` / `just selfrionette-measure` | 明示したDevice native CLIへの互換転送。Device先行採用が必須 |
| `just selfrionette-live` | 既存Python runtime runner。明示fixtureはoffline、port指定はoperator-gated live acquisition |
| `just fast-arm-motion-sanity` | 既存FastArm software診断 |
| `just check` | lint、2つのtypecheck、compile |
| `just check-all` | check、Python全件test、Viewer test/typecheck/build |

`check`と`check-all`は必須の順序だけを組み立てる。変更Markdownのbase指定は
`just docs-check --base-ref origin/main --strict-map --strict-links`を別に実行する。
CIは既存job/stepを保持し、失敗箇所を単一recipeへ隠さない。

## 引数と環境

追加recipeはjustのPython script recipeとpositional argumentsを使う。
引数をshell文字列へ埋め込まず、空白、日本語、引用符、空文字、shell記号をargvのまま転送する。
root/親のdotenvは自動読込みしない。uvは`--no-sync --no-env-file --offline`で準備済みprojectを使い、
recipeの実行で依存同期やPythonダウンロードを行わない。
不足環境は先にREADMEの明示setupで準備する。

同等の直接入口は次のとおり。justも共通scriptもruntimeの必須依存にしない。

```powershell
uv run --no-sync --no-env-file --offline xpotato-sim replay --robot fast_arm --steps 2
uv run --no-sync --no-env-file --offline python scripts/repository/commands.py replay --robot fast_arm --steps 2
just replay --robot fast_arm --steps 2
```

child終了codeはWindows/Unixで保持し、operator interruptionは130とする。
Python childはUTF-8 modeを明示し、Windows CP932環境でもUnicode回収ログを出力できる。
npmの実行fileはPATHから解決する。Windowsのnpm.cmdはnative Nodeとnpm-cli.jsへ
解決し、batchの環境変数展開やコマンド分割を避ける。global npmの選択はnpm-prefix.jsへ委譲する。
必要なnative実装がない場合や他toolがbatchの場合は明示失敗し、shellへfallbackしない。`viewer-build`は既存npm buildであり、
Workbenchの所有済み外部固定buildを更新する`just build`とは用途が異なる。

## 存続する入口

Workbenchの通常入力はGamepadだけである。Keyboard、旧v1 profile、replay、単独Viewer診断は
`app`/`viewer`を維持し、古いprofileをWorkbenchへ暗黙変換しない。
Selfrionette live、serial monitor/measure、Arduino、OSC、実機操作は既存operator gateを維持する。
`selfrionette-live`は既存Python ownerへ転送するだけで、option/defaultや検証をrecipeへ複製しない。
portまたはfixtureを明示しない実行は既存parserが拒否する。fixtureはserialを開かず、
port指定は[manual live runner](r7-b-manual-live-selfrionette-runtime-runner.md)のoperator gateを必要とする。
monitor/measureはDevice先行採用を含む#456の残件であり、Draft実装だけでは全体完了とは扱わない。
追加recipeはserial、校正、OSC、実機操作を自動実行しない。

### monitor/measureのDevice採用条件

physical calibrationとsensor検査のownerはDeviceである。Sim側の2互換PowerShellと
`run_device_serial_tool.py`はargv/root/UTF-8/stdin/終了codeだけを転送する。
option/default・旧PS case/prefix/colon/switch/position binding・serial・表示・集計はDevice Rust CLIが所有する。
Device側の[monitor/measure移行PR](https://github.com/Xpotato1024/Selfrionette-Device/pull/8)を先に採用し、
そのrevisionでbuildした`legacy-monitor` / `legacy-measure`対応native CLIが
利用可能になってからこのSim変更を採用する。Draft binaryでのsoftware検証だけでは先行採用完了としない。

呼出processの`SELFRIONETTECTL`に採用済みCLIの絶対pathを明示する。自動探索、build/download、
旧Sim実装へのfallbackはしない。helpによる能力probeに失敗すればlive呼出前に止める。
通常Sim起動・import・offline fixtureはDevice cloneもこの環境変数も要求しない。

| just入口 | Device owner | 保持する能力 |
| --- | --- | --- |
| `selfrionette-monitor` | `selfrionettectl legacy-monitor` | status/warn/vector表示、normal/paused filter、p/r/c/q（大文字可）、有限/無期限、明示SendText/Calibrate、校正round通知、close |
| `selfrionette-measure` | `selfrionettectl legacy-measure` | baseline/press、7ch平均/差分/絶対差/上位3/最強channel、sensor選択・反復・全sensor sweep、Enter、最終表、close |

承認済み互換変更として、Port省略時のCOM5自動openを廃止し、`--port` / `-Port`明示を必須にする。
baudや測定windowなどのdefaultはDevice ownerに保持する。以下は実機操作例であり、
[hardware safety](hardware-safety.md)のoperator gateを満たす場合だけ実行する。

```powershell
just selfrionette-monitor --port COM9 --duration-seconds 10
just selfrionette-measure --port COM9 --sensor 4 --repeats 3
.\scripts\hardware\selfrionette\monitor_selfrionette_serial.ps1 -Port COM9 -DisplayLevel warn
```

`just selfrionette-monitor --help` / `selfrionette-measure --help`はDevice CLIのhelpでありportを開かない。
Deviceのbounded `info/query_info`は旧工具の代替ではない。旧`c`をv2 tareへ変換しない。
`[calibration complete]`は旧round終了の通知であり、全channel成功や永続校正の保証ではない。
robotのchannel→XYZ/符号/gainはSim Mappingに保持する。

全対象wrapperにbusiness logic/default重複がないこと、direct debug入口、Windows/LinuxのCI、
Device先行採用とoperator gateを確認して#456全体の完了を判断する。
実機のDTR/RTS・校正・荷重応答と#617のLaptop受入はsoftware testで代替しない。
#607/#617/#486の実装・完了判定をこの変更に含めない。

## offline plotとbrowser smoke

option/defaultは`plot_loadcell_vectors.py`と`run_browser_viewer_smoke.py`が所有する。
旧PowerShellは引数・stdin・終了codeだけを転送する。PS5.1で空文字/引用符が失われないよう
UTF-8 JSONをbase64として共通OS adapterへ渡し、Pythonで元のargvへ戻す。
旧PSの大小文字非区別・一意prefix・colon/switch値は対象Pythonの登録parserから正規化する。
wrapperはASCIIだけのUTF-8 without BOMで、機能の分岐やprofile/chart作成を持たない。
`-InputPath`/`-Channels`/`-OpenBrowser`/`-NoBrowser`等の既存option名を維持する。

plotはUTF-8/UTF-16/UTF-32 BOMとWindowsのBOMなし既定encodingを識別する。
file/clipboard/stdinの優先順、sample index、全7chのCSV、欠損timestampとNaN、
1600×900 PNG、channel色と選択を維持する。CSVは既存Export-Csvと同じUTF-8 BOM/quoted fieldsで、
正本の文書/PythonのBOMなし方針とは用途が異なる。描画engineは既存依存matplotlib/Aggへ移し、
pixelの完全一致は要求しない。InputPath/OutputPathの相対pathは旧wrapperの呼出しcwdを基準とする。
Clipboardは明示した場合だけWindows OS adapterが読む。通常のoffline入力では読まない。

browser smokeは旧replay profileの既定値を維持し、NoBrowserがOpenBrowserに優先する。
startup-check、loopback検査、process所有/回収は既存appへ委譲する。
`just browser-smoke --no-browser`はserver起動/回収のsoftware確認で、browserの描画確認ではない。
明示`--open-browser`のmanual確認は[Browser Visual Smoke](browser-visual-smoke.md)に従う。
