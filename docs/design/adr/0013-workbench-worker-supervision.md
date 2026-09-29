---
status: supporting
owner: architecture
last_verified: 2026-09-29
canonical_for: []
related:
  - docs/contracts/workbench.md
  - docs/design/adr/0012-finite-trial-runtime.md
---

# ADR 0013: Workbenchの単一workerと停止監督

## 背景と判断

#592は#591のTrialRunnerに待機型GUIを接続する。重いmodel準備・記録を通信event loopに置くと、
状態照会やSTOPが同時に停止するため、MuJoCoの全mutatorを専用processのmain threadへ分離する。
親は制御revision、所有権、bounded queue、期限監督を担当し、workerはTrialRunnerだけでphysicsを進める。
STOPは通常要求と別slot・ID・deadlineを持つ。旧prepare完了や通常statusは停止確認に使わない。
期限超過はprocess/jobを閉じ、結果未確定の可能性をfaultとして残す。

## 寿命と帰結

trial終端でアプリを閉じず、同modelのretryはnative/WASM/GPU資源を再利用する。
同一runnerのfaultを無条件resetしない。明示prepareが旧runnerをcloseし新しいownerを生成する。
worker自体が死んだ場合はアプリの明示再起動を要求する。再接続はclaim/Startを自動化しない。
static buildは明示`--web-dist`だけ、通常dev経路の失敗を別GUIへ置き換えない。
追加physics、hardware permission、#593のeditorは導入しない。

現行の上限、起動option、resource table、検証境界は[Workbench契約](../../contracts/workbench.md)を正本とする。
