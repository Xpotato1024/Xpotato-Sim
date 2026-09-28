# Object Scene Environment

`object_scene_environment/v1`は、固定box群をRobotが供給するbase sceneへ合成するEnvironment Plugin。
固定discoveryは`plugin.py::ENVIRONMENT_PLUGIN`。importやpreset列挙ではmodel構築・I/Oを開始しない。

## 設定と責務

`resources/objects/`に寸法・質量/均質慣性・摩擦・外観・値の来歴を置き、`resources/scenes/`に
instance ID・定義参照・world poseを置く。presetはpackage-local名で選び、任意path/importは受理しない。
`parameters`は`{"preset":"two_cubes"}`または展開済み`{"scene":{...}}`のどちらか一つ。
参照は起動前にstrictな`ObjectSceneManifest`へ展開し、resolved値とdigestを保存する。

scene compositionは`runtime/scene/composition.py`へ委譲する。Robotが供給した名前付きcolliderに対し
物体×手先のcontact pairを明示する。Robotのsite/joint名を推測せず、Taskの対象選択は所有しない。
旧`contact_cube_environment/v1`のmanifest・freejoint・force条件は変更しない。
共通のnative geometry読取りだけを旧contact evidenceと共有し、多物体を偽の旧manifestへ変換しない。

## 現在の制限

box、均質質量、fixed、mujoco_world frameだけを実装する。dynamic、他frame、未知field、重複ID、
未知definition/versionは拒否する。床やRobotを複製しない。固定box同士は正の隙間を要求し、native distance
の0を非貫通の証明にしない。床への支持接触とtoolの初期touchは食い込み許容値内なら許容する。

詳細契約は`docs/contracts/object-scene-contact-diagnostic.md`、起動は`docs/operations/backend-viewer-startup.md`。
診断数値は実物の材料特性・ばね定数・実機安全根拠ではない。力学反応は#582へ分離する。
