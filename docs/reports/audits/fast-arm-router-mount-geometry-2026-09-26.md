---
status: historical
owner: robot
last_verified: 2026-09-26
canonical_for: []
related:
  - docs/contracts/fast-arm-assembly.md
  - docs/contracts/physical-output.md
  - https://github.com/Xpotato1024/Xpotato-Sim/issues/575
---

# fast-arm-router shoulder mount geometry audit (2026-09-26)

## 目的と対象

肩の取付板が正面視で `<arm>━/  \━<arm>` となる30 degree開きを、
routerの角度補正とMuJoCoの固定mount geometryのどちらが所有すべきか切り分けた記録である。
hardware、serial、OSC送信、robot actuationは行わず、sourceとconfigだけを静的に確認した。

監査した外部`fast-arm-router` working copyのGit revisionは `8d8c3a6` で、
branchは `main...origin/main` のclean stateだった。

## 確認した実装

- `src/mapper.rs`: 4DOFだけ `(j0-j1, j0+j1, j2, j3)` をdegreeからradianへ変換する。
- `config/router.toml`: armL / armRのDOF、motor ID、gain、Pi endpointを宣言する。
- configとmapperのどちらにも30 degreeのmount角、3D base transform、target別mount offsetはない。
- control protocolにはencoder zero操作があるが、これは固定base geometryとは別の状態・校正操作である。

## 判定

30 degreeの開きはjoint-spaceからmotor-spaceへの差動変換ではない。
arm全体のbase frameをworldへ配置する固定変換なので、Robot assembly / MuJoCo model compositionが所有する。
routerやphysical-outputのwire offsetで補償すると、simulationのFK、Jacobian、workspace、接触幾何が実機配置と一致しない。

現行FastArm assemblyはlocal modelを鏡映した後に各instanceの
`position_m` / `quaternion_wxyz`をmount bodyへ適用するため、追加algorithmなしで表現できる。
双腕diagnosticでは左をX軸 `+30 degree`、右を `-30 degree` とする。

## 未確定のまま残す項目

- 実機の左右mount間隔と高さ
- profile jointとrouter semantic jointの対応
- motor signとencoder zero
- motor / actuator limitと実機のaccepted envelope
- 実機でのFK / tip位置一致
- collision、停止、contactを含むphysical safety

これらは30 degree geometryから推論しない。実機に関わる項目は既存#509 / #516等のmanual gateを維持する。

## 反映先

- `docs/contracts/fast-arm-assembly.md`: mount geometryの責務と30 degree姿勢
- `docs/contracts/physical-output.md`: routerがshoulder-mount補正を持つという誤解を除去
- bimanual diagnostic fixture / regression tests: 左右のmount quaternionとMuJoCo反映
- `research/logs/2026-09.md`: 研究条件に影響するmodel geometry変更を記録
