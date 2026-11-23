---
status: supporting
owner: research
last_verified: 2026-11-12
canonical_for: []
related:
  - docs/contracts/finite-trial-runtime.md
  - docs/contracts/launch-profile.md
  - docs/experiment-notes/2026-11-11-generic-scene-mujoco4.md
---

# 投機的接触と細かな時間刻みのRTF比較

## 対象と測定範囲

MuJoCo 4.0.0、Simの物理実装 `2526419cced6749780e5e295957f6e5e8a8f4e45` を固定し、登録済みの受動Sceneを既存profile decoderから構成した。実機、入力装置、GUI、通信を使用していない。Python 3.12.13、NumPy 2.4.6、Windows、Intel Core i7-9700K（8コア）。

RTF = 計算したシミュレーション時間 / 経過した実時間。ここで測った分母はnative `mujoco.mj_step(model, data, nstep=N)` の呼出し開始から終了までであり、model compile、reset、状態保存、画像描画、外部制御を除く。Pythonとnativeの呼出し境界の負荷は含む。全アプリのRTFや実デバイスの制御性能ではない。

## 共通条件

球は半径10 mm、質量50 g、初期中心x=-180 mm、初速+6 m/s。壁の中心x=0、厚さ10 mm。重力ゼロ、Euler積分、CG、iterations=150、tolerance=1e-5。接触は全条件で `solref=[0.005,1]` と同じsolimp/frictionを使用。変えた設定はtimestepとspeculative flagのみ。添付JSONのMJCFをこの2項目を除いて正規化すると完全一致する。

従来図で使用した `solref=[0.02,1]` のままでは、細かな刻みでも接触の柔らかさによる通過が残ったため、**計時前に全3条件のtime constantを同じ5 msへ変更**した。小さな刻みなら常に通過しない、と仮定していない。この変更後も粗い20 msのOFF/ONの0・20・40・60 ms状態は既存図と数値一致した。

MuJoCoの通常のreference-safety clampは全ケースで有効のまま。timestepに応じた有効time constantのclampもエンジン既定挙動として含む。等しい積分誤差・同じ接触軌跡の比較ではない。

## 方法

1試行は0.1秒。200試行のwarmup後、各バッチで2,000試行を同じ初期状態から繰り返す。resetは計時区間外。各試行のnative step実時間を `perf_counter_ns` で測定して合計し、200秒 / 合計実時間をバッチRTFとする。各条件18バッチ、計36,000試行。3条件の全6通りの実行順を3周し、順序を均等化した。表示値は18バッチのRTF中央値を10単位に丸めた。

通過判定は各物理stepの球中心が壁反対側の面＋球半径（x>15 mm）へ出たか。補間した中間姿勢を使わない。選択した細かな2 ms条件は初期位置を0〜11 mmの12段階ずらして通過しないことも別途確認したが、一般的な非貫通保証ではない。最小の有効刻みの探索や全モデルの優越比較ではない。

| 条件 | 刻み | 壁を通過 | RTF中央値 | バッチの四分位範囲 |
|---|---:|---|---:|---:|
| 予測OFF | 20 ms | した | 8,933.14 | 8,915.02–8,981.76 |
| 投機的接触ON | 20 ms | しない | 4,787.39 | 4,782.45–4,827.87 |
| 細かな刻み・予測OFF | 2 ms | しない | 1,060.17 | 1,058.33–1,061.87 |

ON20 msはOFF2 msよりRTFが約4.52倍。ただしOFF20 msより遅く、投機的接触自体の追加計算は存在する。最速のOFF20 msは通過するため成功した対策とは扱わない。球1個と壁だけの小さなモデルの値を、通常のロボット制御や画像描画込みのRTFへ一般化しない。

## 根拠と再現

[測定値・条件・各バッチの生データ・ロード可能なモデルXML](2026-11-12-contact-rtf.json)に、実版、元のSim commit、依存lock hash、同じ物理モデル、全54バッチの分子/分母、通過判定の位置系列、初期位相確認を保存した。private path/hostnameや認証情報は含めない。

以下はJSON内のモデルから同じ計測対象を呼び出す入口。正式測定のwarmup/順序/バッチ数はJSONのmethod fieldsに従う。

```python
import json
from pathlib import Path
from time import perf_counter_ns
import mujoco

report = json.loads(Path('2026-11-12-contact-rtf.json').read_text())
assert mujoco.__version__ == report['mujoco_version']
name = 'coarse_on'  # coarse_off / fine_offも同じ入口
model = mujoco.MjModel.from_xml_string(report['models_xml'][name])
data = mujoco.MjData(model)
home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, 'home')
mujoco.mj_resetDataKeyframe(model, data, home)
mujoco.mj_forward(model, data)
steps = report['identity'][name]['steps_per_episode']
start = perf_counter_ns()
mujoco.mj_step(model, data, nstep=steps)
elapsed_s = (perf_counter_ns() - start) / 1e9
rtf = report['duration_s_per_episode'] / elapsed_s
print(rtf)
```

本測定と別に、同一profileを通常TrialRunnerで0.1秒実行し、完全な積分状態の保存・hash-chain再解析を確認した。記事のnative GIFはその記録の0〜80 msを復元して描画し、画像生成時の物理再積分や補間はしていない。描画用camera/colourとラベルは計時対象にも物理モデルにも変更を加えない。

数値はraw timingから別の検査コードで再計算し、中央値・比率・通過条件・XMLの条件差を照合した。新しいsolverや記事専用runtimeを追加せず、既存モデル/実行APIを利用した測定である。
