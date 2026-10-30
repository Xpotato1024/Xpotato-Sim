---
status: supporting
owner: runtime
last_verified: 2026-10-26
canonical_for: []
related:
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/workbench.md
  - docs/contracts/object-scene-contact-diagnostic.md
  - tests/runtime/test_research_spring_demo.py
  - profiles/research-comparison/README.md
---

# ばね比較基盤と無記録デモのソフト検証

## 対象と条件

baseは `9cad6902a424d0841f0847ccca61ba6346539778`、専用ローカルブランチでの変更である。
S/Gの共通モデルを `single_original/v1` とし、同一 `compression_spring` と観測Taskを使う。
表示は既存の `no_force_cue/force_cue`。4条件のimport用JSONは
`profiles/research-comparison/` に置く。S identityは既存の明示software fixtureを保持し、
正規Device profile・実機値として扱わない。実験目的、人数、学術的閾値は未決定である。

ばねは自然長0、軸world -Y、剛性100 N/m、減衰2 N s/m、範囲0〜0.05 m、初期変位/速度0。
初期手先siteは約 `(0.24, -0.245951, 0.284308)` m、box基準位置は `(0.24, -0.36, 0.28)` m。
手先から物体へ押す方向と正の押込変位を合わせた。値はソフト操作例であり、実機同定ではない。

## 確認できたこと

`test_research_spring_demo.py` は、native passive forceと `-k*q-c*v` の一致、一定軸荷重1 Nで
変位0.01 mへの収束、reset、新epoch、G/S各入力で表示ON/OFFの物理・Task・raw一致、
通常Gamepad入力による接触/圧縮、保存JSONLからの指標再計算、破損hash拒否、
デモ無保存、デモ→実験隔離、active条件変更拒否、旧Device session拒否を検証する。
比較時に除くのは別試行のtrial/epoch/条件identityだけで、物理数値やTaskの判定を丸めない。

独立レビュー後、S側にも第2channelの負Y入力360tickで接触・圧縮を起こす系列を追加し、
ON/OFFのqpos/qvel、接触原力・ばね状態、Taskが完全一致することを確認した。
Gの180tick連続押込では最大変位0.05143781279462453 m、接触原力ノルム13.201417295517862 N。
公称50 mmを超える値をそのまま記録・再計算し、Taskはsimulation予算によるfailureとして残る。
範囲内や課題成功に補完しない。設定画面にもsoft制約であることと、記録値を丸めないことを明記する。

Ruffは宣言済みdev依存であることを確認し、専用checkoutで正規の
`uv sync --frozen --group dev` を実行した。Ruffおよびmypyが成功し、manifest/lockは変更していない。
既存checkoutの共有環境とglobal設定は変更していない。

既存のDevice、試行開始gate、中断、入力batch、通信、scene、表示比較、architecture試験も実行する。
従来の保存file期待へ `samples.jsonl` と再計算検査を追加し、既存試験の削除・skip・閾値緩和をしない。

専用Chromiumと明示合成GamepadでローカルWorkbenchを操作した。デモ開始・運動・停止で
実験fileは0、Device接続設定なしは準備拒否、入力選び直し後の非中立ではtick 0のまま待機、
新しい中立で再開、デモ→実験は新しい1試行だけ保存、実験active中は切替不可を確認した。
スクリーンショットとDOM、実行log、機械判定JSONはローカル作業の `evidence` directoryへ保存した。
これはブラウザー描画を伴うソフト確認であり、物理Gamepad/Deviceや参加者の操作検証ではない。

## 残る境界

空圧提示装置、M、3課題同時実装、研究の実施、実機I/O、firmware/設定・資格情報の変更は行っていない。
「Sだけ疑似反力が出る」、知覚的同等性、実機の力・校正、長時間運転性能は未検証。
Sに既存の固定面向けcontact gainを適用してばね力と呼ぶ変更はない。
観測Taskは押圧再現成績や抵抗判断成績を定義しない。連続記録は有界な同期disk書込であり、
実際の試行時間・保存先条件での周期性能と容量は使用前に別途確認する必要がある。
