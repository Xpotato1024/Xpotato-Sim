# runtime

## 責務

唯一のcomposition root。複数層を結線してよい唯一の場所であり、内部責務を次へ分ける。

| owner | 責務 |
|---|---|
| `composition/` | config、Robot Profile / Plugin / Bundle、typed provider assembly、pipeline builder |
| `execution/` | pipeline lifecycle、input step loop、timing / pacing |
| `contact/` | contact manifest、raw evidence、derived reaction force、task log / presentation |
| `control/` | input selection/state、endpoint target、viewer control ingress、step diagnostics |
| `safety/` | stale input policy、qpos feasibility、physical safety・operator validation |
| `output/` | 実評価candidate / endpoint routeのsafety binding、typed physical output permission decision、recording / dry-run trace、lifecycle / bounded stop。transport / hardware送信は所有しない |
| `experiment/` | versioned experiment contract、registry、readiness composition、software-only trial lifecycle |
| `evaluation/` | FK / endpoint evaluation、progress、manifest / freeze readiness |
| `runners/` | dry-run、live / offline smoke、WebSocket publisher、experimentのthin entry point |

#406で成立したexperiment lifecycle / runnerは`experiment/`が所有する。#407のexecution trace / motion-log
recorderも同じownerへ置く。
`runners/`はthin entry pointだけを追加でき、Task判定、metric、record projection、artifact責務を持たない。
entry pointはmanifest revisionとstartup側が独立に取得したactual execution revisionを別引数で受け、
readinessのexact-match gateを通す。reset後はactual qpos / measured tool orientationをfrozen manifestへ照合する。
recorderはfreeze identityへconfigurationをbindし、explicit protocol contextからtrial identityを決定的に作り、
validationとstrict read-backを通過したcomplete trialだけをatomic JSONLとして保存する。
validated `experiment-motion-log/v1`からのTask evidence再構成、ordered production evaluatorへのmetric委譲、
deterministic `evaluation-artifact/v1`のstrict JSON / atomic emissionは`evaluation/artifact.py`が所有する。
artifact writerはtargetごとのsame-directory persistent sidecar
(`.<target-name>.lock`)をkernel advisory lockとしてcritical section全体で保持する。sidecarの
existence/contentはinertで、PID probe、JSON owner metadata、stale cleanup、sidecar unlinkを行わない。
sidecarは`os.open(..., 0o600)`で作成し、POSIXではowner-only modeを要求する。Windowsのnumeric modeは保証せず、
current OS security semantics / inherited ACLに従う。empty sidecarもcontent・inodeを変更せず保持する。
Windowsは`msvcrt.locking`、POSIXは`fcntl.flock`、process-local path lockを併用し、handle closeまたは
process crashでkernelがlockを解放する。#407のJSONL writerとは責務を共有しない。

## 入力

config、選択されたInput Source / Control Mapping / command semantics route /
Robot Bundle、MuJoCo backend、transport。

## 出力

実行ループと結線済み pipeline。

## 依存してよい層

すべての層。

## 依存してはいけない層

なし。ただし各層が runtime に依存してはいけない。

## 禁止事項

層の中身の責務を runtime に吸収しない。runtime は結線と lifecycle 管理に限定する。

runtime package rootは`RuntimeConfig`とRobot catalog resolver 5件だけをlazy exportする。
各contract、control、safety、evaluation、runner APIは上表のcanonical ownerからimportする。

## canonical routing

- [runtime composition](../../../docs/architecture/runtime-composition.md)
- [dependency boundary](../../../docs/architecture/dependency-boundaries.md)
- [plugin system](../plugins/README.md)
- [unified CLI](../../../docs/operations/unified-cli.md)
