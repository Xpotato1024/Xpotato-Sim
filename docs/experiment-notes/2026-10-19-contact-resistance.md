---
status: supporting
owner: runtime
last_verified: 2026-10-19
canonical_for: []
related:
  - docs/contracts/continuous-endpoint-velocity-input.md
  - docs/contracts/finite-trial-runtime.md
  - tests/runtime/test_contact_resistance.py
---

# 接近速度を変えるcommand Mappingのsoftware比較

## 固定条件と実行範囲

#630のlocal commit `e7f99d18298347e76c381a75ea747c215b14e0f6`へ#632差分を加え、
Python 3.12.14、同じ`uv.lock`依存のnative MuJoCoで3条件を各1回、120 commit実行した。
`dynamic-fixed-contact`のbimanual、fixed cube、physics 1/600 s、control 1/60 sを維持し、
同じ初期状態から左world +Xの入力を与えた。既存servo、solver、gain、joint制限は変更していない。

比較条件の作成は共通resolverを使用した。`command_mapping.mode`だけを変え、
表示は`no_force_cue`へ固定した。次の例をJSONへexportするとWorkbenchへimportでき、
Advanced「Mapping」でmodeと数値を変更できる。実機値や人の知覚に対する推奨値ではない。

```python
from xpotato_sim.runtime.experiment.edited_condition import preset_condition, resolve_condition
from xpotato_sim.runtime.experiment.trial_condition import TrialLimits

document = preset_condition("dynamic-fixed-contact", TrialLimits(120))
document["command_mapping"] = {
    "schema_version": "approach-resistance/v1",
    "mode": "contact_dependent",  # baseline / fixed_gain と比較
    "frame": "mujoco_world",
    "targets": {
        "left": {"object_id": "cube", "approach_normal_world": [1, 0, 0]},
        "right": {"object_id": "cube", "approach_normal_world": [-1, 0, 0]},
    },
    "fixed_gain": 0.5,
    "half_gain_load_n": 10.0,
    "minimum_gain": 0.1,
    "normal_match_cos_min": 0.99,
    "max_sample_age_s": 0.2,
}
profile, limits, document = resolve_condition(document)
```

測定は`ModelExecution`の入力→tick→sampleを呼ぶ計算区間の比較で、Web/実入力の遅延や
有限試行の終端保存RTFを測っていない。clockは初期10 sから各commit前にdtを加える明示software時計。
`tests/fixtures/trial_gamepad/short-movement.json`の第1messageのwire shapeを使い、
中立でarmingした後、sequence=1〜120、timestamp=sequence/60、raw axesとaxesを
`[0, -0.64, 0, 0]`、zero_state=falseへ設定して毎tick入力した。buttonsとsource identityは元fixtureと共通。
sampleではTaskの観測も進め、120 commitで明示停止した。Taskは60秒観測の途中であり、成功判定を行っていない。
表示sampleと同じnative snapshot/原力をcontrol側で共有し、追加のphysics stepやqpos書換えはない。

## 観測

| 条件 | 確定commit数 | 最初のleft–cube接触tick | 最初の速度減衰command tick | 最小係数 | 観測された最大法線荷重N |
|---|---:|---:|---:|---:|---:|
| baseline | 120 | 58 | なし | 1.0000 | 30.57698 |
| fixed_gain | 120 | 116 | 1 | 0.5000 | 2.56996 |
| contact_dependent | 120 | 58 | 59 | 0.3347 | 20.03444 |

各runは途中faultなく120 commitへ到達した。contact条件では58の確定接触を59のcommandへ使い、
未確定stepからの力を要求していない。最初の接触はworld +X法線で、作用反転後の原力は
(-2.90249, -1.16359, 0.63895) Nだった。左右のgainや反力を合算して相殺する処理はない。

法線荷重の最大は各control commitの最後のsubstep観測に限る。substep間peakや正式metricではない。
fixed gainでは到達そのものが遅く、同じ2 simulation秒内の接触時間も異なるため、
値の大小を「力覚による改善」や最適係数の証明と解釈しない。実機・参加者・描画の受入は未実施である。

## identityと再検証入口

表のcondition SHA-256は`canonical(semantic_parameters(profile, limits))`のbuild前digestであり、
model数値条件を追加した有限TrialRunnerの最終condition digestとは区別する。

| mode | build前のcondition SHA-256 |
|---|---|
| baseline | `108d7a48964d2c5bb7024e629cf60861687492bcedb1653143bd4d8e30e2cde1` |
| fixed_gain | `e4b3267e6315b367a88ae8af0ff646a201401b28cec582d0715f010beec71828` |
| contact_dependent | `230f57558861b841775c56d235273c0b2b10e035dcb46dcb910cef78f1327f0f` |

計測したprincipal sourceのSHA-256は次の通り。

| owner | SHA-256 |
|---|---|
| Mapping primitive | `c8099bbb70256d8968363b90ebb55a259648b6cd7a07a1af93ebea3be442dec9` |
| scene command Mapping | `743ed5065c2257d796fddd344fa07bbaee5a8b4106dae66c76859180aa8038ab` |
| ModelExecution | `fd87dd3f283d6e176c45d9024350fa3065dfcffbbf8b9df42d52249ce8ef8c56` |
| CoordinatedRuntime | `599c7413c1c67a5f7a5116cf1e1f73974b8935f54fedbef070f395459a0ce493` |

`tests/runtime/test_contact_resistance.py`は独立な符号/離脱/接線/ゼロ/可変dt/荷重急変fixture、
表示側の1 N clampに影響されず10 Nの原力を制御へ使う例、欠測/partial/複数面/時刻/対象の拒否を検査する。
実native 80 commitでも、前tickの原力と次commandのinput refが一致し、最初の減衰が接触の次tickになる。
欠測を注入した場合はnative prepare前にfaultし、STOP/resetでsampleとtraceを失効する。
無接触で元写像・baseline・contact条件のqpos/qvelが完全一致し、snapshot連打で取得数/制御時計が増えない。
有限TrialRunnerで3modeのcondition freezeとmodel再利用も検査する。

本Issueは最新traceを提供する。連続evidence、正式Task/Evaluationと研究用metricは#583/#584へ残る。
動く対象面、任意把持、多数の非平行面、実Selfrionette入力での知覚評価は今回の比較範囲に含めない。

## Device入力との統合後の確認

#631のlocal commit `9b70861f95c8b7fc1c266ba87e6bda5f1ad84122`へ積み直し、
`tests/runtime/test_device_command_mapping.py`で単手・両手の合成Device入力を検証した。
上表のGamepad比較値とprincipal source hashは統合前に取得した計測identityとして維持し、
統合後の別buildで再計測した値へ読み替えない。

条件と入力は[Device統合note](2026-10-19-device-input-integration.md)のsoftware配置・低gain・元eventを共有し、
固定面のcommand条件を追加した。3modeと表示切替を順にprepareしてもmodel build数1、
同じ個体・profile・role bindingを保持し、新epoch・新sourceと空の制御cacheを得ることを確認した。
接触依存runでは実native接触後の次commandから減衰し、全tickで前commitの原力参照と元eventを照合した。
STOP/retryでcacheと準備状態を引き継がないことも検査する。

traceの`received_at_s`はSim時計上のage補正済み最古receipt、`ordering_receipt_s`は
Device producerの順序時計として別fieldに残す。後者がないGamepad traceの既存field集合は変えない。
これはsoftwareの経路・因果順序の検証であり、実serial・実個体二台・参加者評価は行っていない。

## 公開baseへの最終同期

公開済み[Draft PR #636](https://github.com/Xpotato1024/Xpotato-Sim/pull/636)の
head `e0b674f16df94fa43ae7a51ebca97ae634151499`へ積み直した。
旧baseからの追加はDevice bridgeの公開revisionとCLI/実binary証拠を同期した文書2件であり、
前回検証したlocal head `7a54c8160b3770560c10c830d7cc9451cdd7201c`と
製品コード・tests・profiles等の非文書ファイルが完全一致することをGit差分で確認した。
関連110件、architecture 229件、Viewerのworkbench/typecheck/buildの成立は同じコードの証拠として保持する。
最終同期では単手・両手のDevice command境界4件とoptional順序時計2件を再確認し、6 passedだった。

## CI互換修正後のbaseとTask終端trace

上記の非文書ファイル一致は`e0b674f`へ同期した時点の記録である。
その後、同じDraft PR #636の公開head `f245bbe6b37fafc3c8c9afe39f1b4178d5feed9b`へ積み直した。
Sourceが所有する初期待機理由によるGamepad診断の互換修正、Device待機理由とcatalog検証、
Device bridge `21e85ac58c90913da7096966df6a0d8cbb80d57c`のpinと実binary証拠を継承した。
接近速度写像、原力、Device binding、二つの入力時計を保持し、以下の終端projectionだけを補修した。

独立レビューでは旧head `f58081ef628a6e8cc79fec9c7a20eb567750f722`で、
Task終端後のruntimeとlive traceは停止・失効しているのに、返却frameのtraceが`mapped`のまま残るP2を再現した。
`ModelExecution.sample()`のTask終端branchで、STOP後の最新traceを深い複製としてframeへ反映した。
停止理由とTaskの終了理由はそれぞれの既存fieldに保持し、入力・写像式・physicsの変更は加えない。

自然終了と予算終端の2回帰は、fresh中立→1 native commit→Task終端sampleの順序を使用する。
自然終了は既存観測Taskの期間をcontrol dtの半分とし、観測窓完了が接触成功を意味しない既存理由を確認した。
予算終端は元の60秒Taskに明示予算終端を与え、`execution_budget_exhausted`を確認した。
両回帰は修正前の古い`mapped`表示で失敗し、修正後は返却frameとlive traceがともに`invalidated`となった。
終端中のprovider `sample` / `prepare` / `commit`を禁止し、tick 1とqpos / qvel / timeの不変、
cache解放、以前の公開frameと元traceの保持、返却traceの入れ子を変更しても内部へ伝わらないことを検査した。

最終関連検証はcontact resistance、Device command/force統合、Source待機、TrialRunner、
元CI失敗2件、専用SourceのCLI除外、runtime ownershipの119件が通過した。
別担当も自然終了・予算終端の元再現手順を独立に実行し、両方の失効と停止・cache解放を確認した。
確認対象のModelExecution SHA-256は`8bcfa71fb26d16eb738f43b337a9e862e1ec2fa8d9a1a39877d639663a579d25`、
回帰testのSHA-256は`b2651b478391e22e6eab21c3192e343c670388f3fda8180fab34a85cc3d570bf`である。
Viewerの全ファイルは`f58081e`時点とbyte一致するため、既存workbench/typecheck/buildの検証を保持した。
上表の120 commit比較とsource hashは元の計測identityを維持し、この診断修正後に再測定した値とは扱わない。
