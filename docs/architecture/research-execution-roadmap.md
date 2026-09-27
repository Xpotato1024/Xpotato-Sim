---
status: canonical
owner: research
last_verified: 2026-09-25
canonical_for:
  - R7-G完了後の研究実行優先順位
  - 実機接触成立までのcross-round dependency roadmap
related:
  - docs/design/adr/0004-prioritize-physical-contact-bringup.md
  - docs/contracts/runtime-input-source-registry.md
  - docs/evaluation/world-tool-frame-comparison-design.md
  - docs/operations/hardware-safety.md
  - https://github.com/Xpotato1024/Xpotato-Sim/issues/410
  - https://github.com/Xpotato1024/Xpotato-Sim/issues/418
  - https://github.com/Xpotato1024/Xpotato-Sim/issues/419
  - https://github.com/Xpotato1024/Xpotato-Sim/issues/420
---

# 研究実行ロードマップ

## 目的と位置付け

この文書は、R7-G完了後にSelfrionette-mujocoで何を先に成立させるかを定めるcurrentな研究実行順序の正本である。
研究目的そのものや卒業論文の主張を置き換える文書ではなく、既存の研究目的を最短で検証可能な状態へ運ぶためのsoftware / experiment execution roadmapを定める。

現在の最優先milestoneは、**共通の速度写像・腕identity・シミュレーション状態を使い、片腕/双腕の操作予備、ばね押圧・推定、協調接触の実験を実行・記録できる状態**である。実機は時間と受入条件が整った場合の追加検証であり、シミュレーション主経路の必須前提にしない。

```text
Gamepad / Selfrionette
  -> Input Source -> common rate Mapping -> arm binding
  -> one MuJoCo scene -> task / evaluation / record
  -> optional physical output (separate manual gate)
```

2026-09-25の利用者追認済み作業基準は、論文repositoryの
[RM004](https://github.com/Xpotato1024/Selfrionette-Mujoco-paper/blob/721c119537f51f582094fcb610b4b6bfa4349e67/research-memos/RM004_SimulationBimanualExperimentDesign.md)
である。Gamepad対Selfrionetteの共通rate比較、双腕とばね課題を改訂可能な作業方針として扱う。
以前の「実機優先・入力装置間比較は対象外」を現在の主経路へ適用しない。これは新規性確定・数値条件凍結・参加者実験開始許可ではない。
判断の来歴は [ADR 0011](../design/adr/0011-simulation-first-bimanual-scope.md) に保存し、過去ADRは変更しない。

## 現在成立している前提

R7-G / #404までに、versioned manifest / readiness、production six-axis composition、world / tool MuJoCo execution、`experiment-motion-log/v1`、canonical Task evidence reconstruction、production Evaluation Plugin、`evaluation-artifact/v1`、deterministic software-only E2Eが成立した。

既存入力取得は再利用し、双腕の個体binding・二台取得は明示的な追加課題として扱う。

- `selfrionette/v1`はlive production Input Sourceである。
- `viewer/v1`はbrowser keyboard / gamepad bridgeであり、gamepad入力は`viewer_gamepad` subtypeとしてruntimeへ到達する。
- Input Sourceはacquisitionとhealthを所有し、Control Mappingはsampleからcontrol intentへの変換を所有する。

したがって主経路は、左右独立入力、片腕/双腕model・複数手先route、同一worldの接触と記録を接続することにある。
実機出力は左右の共通codec/要求とgateを維持して整備するが、実機受入待ちでsimulation作業を停止しない。

## シミュレーション主経路の優先順位

1. 左右独立の1スティックXYZと明示的な入力座標を成立させる（#563/#567、PR #568）。
2. coreの左右共通assembly、arm/joint identity、片腕/双腕の複数手先provider・routeを接続する（#569と後続）。
3. 同一sceneの左右指令・衝突/制限・接触を処理し、必要な二台Selfrionette bindingを実装する。
4. 複数視点、GUI設定、有限試行のreset/retry、Taskと物体配置を既存ownerへ接続する（#564/#565/#486）。
5. 片腕到達・左右協調・ばねの技術予備と操作予備を経て、条件・表示・主指標・解析を固定する。

実装順の依存と研究プロトコルの正式採択は別である。コード・通常testの成功を参加者実験として数えない。
入力装置への力、モデル接触力、実機測定力を分け、現行delta/sampleを速度と呼び替えない。
片腕/双腕と左右は、入力・表示だけでなくOSCのtarget/関節順/応答/停止まで同じ完了matrixで扱う。
[assembly契約](../contracts/fast-arm-assembly.md)の残存項目を片側未実装のまま完了扱いにしない。

## 実機オプション内の優先順位

以下は実機を追加実施する場合の順序であり、上のsimulation主経路のhard dependencyではない。
既存の実機Issueと安全gateのscopeはこの記述で緩和・closeしない。

### 1. R7-H contact-coreを先に成立させる

R7-H / #410の最初のcritical pathは次とする。

```text
#411 task / object manifest
  -> #412 MuJoCo scene / spawn / reset
  -> #413 measured contact evidence
  -> #415 contact task lifecycle / measured outcome
```

#415はvirtual reaction-force signalを必要条件にしない。接触開始、押込み、保持、success / failure / technical-invalidをmeasured contact evidenceから判定できることをcontact-core completion gateとする。

#414 virtual reaction-force signalは#413から分岐する後続trackとし、最初のphysical contact milestoneのhard blockerにしない。

#416 logging / transport / viewerと#417 full software-only E2EはR7-H全体のtraceabilityとsoftware-only completionには必要だが、最初のmanual physical contact smokeのhard blockerにはしない。ただし、formal pilotまたは研究data collectionを開始する前には、contact trialのlossless logging / provenance / outcome再構成が成立していなければならない。

### 2. R7-J physical safety coreをR7-Hと並行して開始する

R7-J / #419はR7-H全完了を待たない。

R7-Gで成立したRobot Profile / Runtime Plugin、joint-limit configuration、qpos feasibility、MuJoCo modelと、実機のauthoritative資料・観測を入力として、次をphysical safety coreとして先行させる。

- authoritative joint / motor / actuator range
- MuJoCo modelと実機rangeの差分
- self-interference pairとcollision filtering policy
- robot-environment clearance / collision proxy
- velocity / acceleration / numerical feasibility boundary
- configuration / trajectory feasibility
- operator-visible hold / reject / stop / recover reason

contact-specificなself-contact / target-object contact / environment contact分類だけは、R7-H #413のcontact identityを利用できる時点で接続する。

physical safety coreは「安全である」という一般主張ではなく、**physical outputを許可してよいbounded envelopeと停止条件を明示するgate**である。

### 3. R7-Kはminimal physical output gateをpersistent runtimeより先にする

R7-K / #420では、daemon / service / container / long-duration soakを最初の実機bring-upの前提にしない。

最初に成立させる範囲は次とする。

```text
foreground CLI / explicit operator action
  -> versioned physical command boundary
  -> recording / dry-run sink
  -> stale / disconnect / stop semantics
  -> R7-J safety-core gate
  -> explicitly enabled transmission
  -> bounded physical robot output
```

actual physical actuationはR7-J safety-core acceptance後にのみ行う。service / container、restart policy、unattended operation、long-duration soakはinitial physical contact milestone後の後続scopeとする。

### 4. physical bring-upはfree-spaceから接触へ進める

physical output pathが成立しても、最初から対象物へ接触させない。

順序は次とする。

```text
A. gamepad -> physical fast_arm / free-space
B. Selfrionette -> physical fast_arm / free-space
C. gamepad -> physical contact task
D. Selfrionette -> physical contact task
```

A / Bでは入力経路、control semantics、workspace / collision / stale / stop、operator gateを切り分ける。C / Dで初めてcontact-coreをphysical operationへ接続する。

GamepadとSelfrionetteで可能な限り同じRobot、Environment、Mapping semantics、Task、safety / output pathを共有し、入力device固有差分と実機output差分を混同しない。

### 5. 実機を使うR7-I participant studyはphysical feasibilityの後に行う

R7-I / #418は、少なくとも次がmanual operator smokeで成立した後にstudy designを具体化する。

- gamepad free-space physical operation
- Selfrionette free-space physical operation
- contact-coreを使ったbounded physical contact operation
- technical-invalid / stop / abort / retryの運用境界
- formal data collectionに必要なlogging / provenance

participant studyをphysical bring-upの代替にせず、操作可能性と安全運用を先に確認する。

## 実機オプション内のcross-round critical path

```text
R7-G completed
  |
  +--> R7-H contact core: #411 -> #412 -> #413 -> #415
  |
  +--> R7-J physical safety core -----------------------+
                                                        |
                                                        v
                                      R7-K minimal physical output gate
                                                        |
                                                        v
                                      gamepad physical free-space smoke
                                                        |
                                                        v
                                   Selfrionette physical free-space smoke
                                                        |
                                                        v
                                      bounded physical contact smoke
                                                        |
                                                        v
                                      R7-I pilot / participant design
```

並行して進められる非critical track:

```text
#413 -> #414 virtual reaction-force signal
#413 / #414 / #415 -> #416 logging / transport / viewer
#411-#416 -> #417 full R7-H software-only E2E
R7-K persistent service / container / long-duration operation
```

これらは価値があるが、最初のphysical contact milestoneを不要に遅らせない。

## 実機オプションのmilestone gate

| Milestone | 必須成立条件 | まだ要求しないもの |
|---|---|---|
| contact-core software gate | #411 / #412 / #413 / #415 | virtual reaction force、participant study |
| physical safety gate | R7-J safety-coreのbounded envelope、stop / reject条件 | universal physical safety claim |
| minimal output gate | foreground / manual gate、dry-run、stale / stop、R7-J gate | daemon、container、unattended runtime |
| physical free-space smoke | gamepadとSelfrionetteのbounded movement、stop / stale / collision gate | object contact、formal experiment |
| physical contact smoke | contact-core + physical safety + minimal output gate | participant study、statistical conclusion |
| formal pilot readiness | contact logging / provenance、protocol freeze、exclusion / retry rule | universal superiority claim |

## Safety / permission boundary

実機オプションを残すことは、hardware accessを通常taskへ自動許可することを意味しない。

実機作動を行うIssueでは、`docs/operations/hardware-safety.md`に従い、少なくとも次を明示する。

- target device / host / port
- command / rate / bounded envelope
- operator gate
- physical clearance
- stop / emergency stop procedure
- rollback / recovery
- expected / observed output

serial open、OSC send、network transmission、physical robot outputは、該当する専用Issueと明示許可がある場合だけ実行する。

## Research claim boundary

この優先順位変更から次を推論しない。

- RM004の装置間比較は作業基準であり、新規性の確定や参加者実験開始の許可ではない。
- physical smokeの成立だけで写像方式の優越は言えない。
- software-only contactの成立だけで実機contact stabilityは言えない。
- bounded physical operationだけでauthoritative physical safety全体を証明したとは言えない。
- operator smokeをparticipant experimentとして扱わない。

formal evaluationでは入力装置、写像、補助、反力条件を同時に増やしすぎず、解釈可能な最小比較を維持する。

## Roadmap更新ルール

この文書はcurrent execution priorityの正本である。Round番号・Issue本文はこの文書と矛盾させない。

優先順位またはdependencyのmaterialな変更を行う場合は、current canonical documentを更新し、判断理由が将来必要な場合は新しいADRとして残す。既存ADRは現在方針に合わせて書き換えない。

## 実機前のsoftware gate

実機へ進む前にR7-L #539（#540-#543）の信号エミュレーションを実施する。
詳細は`docs/contracts/pre-hardware-signal-emulation.md`を正本とする。
対象は連続入力、bounded取得、protocol/response、contactを含む統合検証であり、
既存#509のphysical acceptanceと#516-#519の実機確認を代替しない。
R7-Lにsoftware未完が残っている間は「残りは実機検証だけ」と判定しない。
