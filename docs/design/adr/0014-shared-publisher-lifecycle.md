---
status: historical
owner: architecture
last_verified: 2026-10-07
canonical_for: []
related:
  - docs/architecture/runtime-composition.md
  - docs/architecture/dependency-boundaries.md
  - docs/operations/repository-commands.md
---

# ADR 0014: 現役consumerを保持した起動寿命の集約

## 背景

#456と#607をmain `b233e3813977ebcc512aca56f69086c80bca2718`で照合した。
#615/#616/#619は既にmerge済みで、性能修正・型境界・Workbenchの初回準備/通常起動は再実装しない。
#613/#614はclosedであり、そのbranchを新mainへ再統合しない。

WorkbenchはGamepadのみで、既存app/publisherの全consumerをまだ置換できない。
旧v1 profileとnamed-modelのinput/Mapping/Task意味も異なる。
名前だけを理由に削除したり、新modelへ暗黙fallbackしたりすると互換性を失う。

## consumerと判断

| 現役consumer | 必要な能力 | 今回の判断 |
| --- | --- | --- |
| CLI `profile`の依存検査と`app` | 旧v1/v2 profile解決、digest拘束、Web/backend所有、有限終了 | appを維持。process所有を共通applicationへ移す |
| appの旧v1 Keyboard/Gamepad/replay backend | 選択済みMapping/provider、既存入力step loop、旧payload metadata | input-source publisherを維持。接続寿命だけ共有する |
| appのnamed-model backend | 同一ModelExecutionの入力、MuJoCo state、Task観測、資産配信 | model publisherを維持。physics/Taskを複製しない |
| CLI `viewer`のdefault replay/`sweep_x` | 旧replay frame/annotation、単独Viewerの有限診断 | replay publisherを維持。別input-sourceへ黙って置換しない |
| CLI `viewer --input-source` | 明示Input Sourceのselection/Mapping、Viewer ingress、最新state配信 | 共通input step loopとlive deliveryを維持 |
| browser/manual smoke、直接publisher API | 単独WebSocket、grace period、ready通知、元の終了/ログ | 同じCLI/APIを維持し、共通接続sessionへ委譲する |
| Selfrionette live/recorded fixture診断 | operator gate、serial contract、有限offline replay | 独立診断として存続。今回serial/DeviceInfo/鮮度/Mappingを変更しない |
| 有限trialとWorkbench | 共通TrialRunner、epoch、結果、ready前入力拒否、STOP監督 | 既存ownerを維持。appとprocess所有primitiveを共有する |

production consumerはCLI、appのworkerと明示scriptである。
testsはconsumerの契約を検査する。過去のaudit/experiment noteに残るcommandは当時のevidenceとして保持する。
新版Workbenchへ移すにはKeyboard/replay等のInput Source能力を先に実装・受入する必要がある。

## 決定

- `application/owned_processes.py`がappとWorkbenchのprocess/job所有と有限回収を所有する。
  旧`runners/application_process.py`は退役し、内部pathの互換facadeを追加しない。
  start gate、Windows job、Unix process group、自分の子だけの回収は維持する。
- `application/publisher_session.py`が3経路のbind後ready通知、grace待機、未接続拒否、server回収を共有する。
  実行例外やcancelの原例外を維持し、model executionのstopは既存finallyが所有する。
  Workbench control serverは認証・STOP監督を持つ別契約なのでpublisherへ統合しない。
- 人間向けの既存操作はjustへ追加する。argvの転送とchild終了codeだけを共通scriptが所有し、
  option/defaultを複製しない。直接CLI/scriptはdebug/downstream用に維持する。
- #617のrecorder import/spawn/readyと#618のViewer lock auditは今回触れる責務と独立しており、
  timeout拡大、依存更新、警告抑制を混ぜない。

## 影響と検証境界

接続・processの所有場所と操作案内を更新する。physics、input Mapping、Task、保存format、
#581寸法、#583評価定義、#584研究条件/人数/metricは変えない。
Device側のbounded monitor、DeviceInfo/vector関連付け、device u32 timestampとhost freshnessも別責務である。

既存publisher/モデル/scene/起動回帰、architecture、新接続sessionの異常/取消、
justのliteral argvと0/17/130、全Python、Viewer test/typecheck/build、docs/encoding、現行HEAD CIを検証する。
実機、serial、OSC、実験端末のAC接続下反復、browser実操作は実行しない。
本変更はproduction behaviorと研究能力を変えないrefactor/操作入口追加で、
research logとexperiment noteの新規更新は不要と判定する。

## 残範囲

#607のapp/publisher全面退役と#456のPowerShell monitor/measure/plot/browser smoke移行は完了していない。
production削除量、新共通化code、移動code、操作launcherの追加量はPRで分けて実測する。
LOCのみを目的に互換性を削らず、Issueの全受入を満たしたとは宣言しない。

## Windows文字コードの補完

新just appの回収ログはPython childのUTF-8 modeを明示する。
既存browser smokeはPS5.1 ParseFileでCP932誤読のUnexpectedTokenを再現し、
同一本文のUTF-8 ParseInputでは成功した。本文bytesを保ったBOM追加だけで
4つの既存PowerShell scriptのPS5.1 parserが成功することを確認した。
汎用encoding checkerのBOM指摘はこの.ps1に限る互換性要件として記録し、MarkdownのBOMを許容しない。

## 独立レビューによるWindows npm境界の補完

最終headの独立レビューで、shell=FalseでもWindowsのnpm.cmdはcmd.exeを経由し、
a&verの分割実行、%COMSPEC%の展開、child失敗codeの消失が再現された。
native Node/npm CLIへ解決し、npmのglobal prefix選択だけ既存helperへ委譲する。
未対応batchへfallbackせず明示失敗とする。
本物のNodeで空文字・引用符・日本語・shell記号と0/17/130を検証し、
bundled/global両CLI選択を回帰で拘束する。batchの実行そのものはfixtureの無害な失敗で検出する。
