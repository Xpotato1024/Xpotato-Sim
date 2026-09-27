---
status: historical
owner: implementation
last_verified: 2026-09-25
related:
  - docs/contracts/fast-arm-assembly.md
---

# FastArm左右共通assembly・出力bindingの実装記録

## 基点とscope

Issue #569。main `833c3fe3c71f33ff675c2eb4dc1e7708a70be07b`を基点とする。
PR #568は未マージのため変更せず、入力変更と独立にモデル・指令対応の土台を実装した。
現在のAPIと全体完了matrixは [assembly契約](../../contracts/fast-arm-assembly.md) を正本とする。

## 調査で確認した既存制約

既存RobotProfile/Runtimeは4関節・単一endpointを要求する。OSC codecは右固定ではなく、
明示target/joint mappingを受け取るため、左右ごとの別encoderを作る必要はない。
元のrobot meshはcontype/conaffinityが0。幾何を反転しただけで接触・自己干渉対応とはしない。
上記はソース監査であり、ユーザー実機を動かした観測ではない。

## 実装

core原本のXMLと5個のSTLから、1/2個の名前付きinstanceを生成する。位置・回転軸・姿勢・
慣性・meshを同じ鏡映規約で変換し、配置は明示する。原本・既存profileは変更しない。
qpos/dof/actuator/siteは名前から解決し、object freejointの前置に対応する。
adapterは全armの出力bindingと全関節名を検証し、左右の既存PhysicalOutputRequestへ投影する。
片側欠落や同じOSC targetの二重使用は拒否する。許可・送信・safety判断は実装していない。

## 検証方法

既存Windows project環境、MuJoCo 3.9.0を用い、隔離worktreeのsourceを検査する。
原型単腕・鏡映単腕・両腕・順序反転、32組の合成姿勢についてFK/Jacobian/慣性/重力を照合する。
compiled meshのworld境界、同一dtによる両actuatorの時間発展、freejointのqpos7/dof6によるずれを検査する。
左右で異なる合成sign/offsetと既存OSC byte列の独立oracleを照合し、逆腕応答・期限切れ・停止を検査する。
新規output試験はsocket/DNSを禁止し、実送信・実機ACKを生成しない。
両armの生成requestを、既存FastArmPhysicalOutputSessionのP5・二重permission・既存encoderへ
in-memory senderで通し、未arming時の拒否と、合成evidence/gateでの送信試行・疑似応答を検査する。
これは実機evidenceを取得した意味ではなく、左右に同じproduction gateが適用される回帰試験である。

対象command、件数、終了コード、最終commitとの対応はPR本文とrepository外ログへ記録する。
この通常testは参加者実験や実機の鏡映形状測定ではない。

## 未実装・未確認

- GUIからの双腕選択と両手先同時操作、multiple-endpoint typed route、入力PR #568との実接続。
- 接触用collision形状、全sceneを使う腕間/自己/物体の評価、共通作業域の実験採択。
- 二台Selfrionette取得、GUIタスク/物体生成、ばねの接触評価。
- 両側preflight・部分送信失敗・協調停止を含むphysical runtime、actual router互換、左右実機のcalibration。
- 別AIによる独立レビューと人の操作受入。

左右どちらかだけを未実装として完成扱いにしない。今回のAPIにも左右共通の受入を課し、
上記の未完項目は両側を含む次の実装単位として扱う。実機・serial/OSC送信は行っていない。
