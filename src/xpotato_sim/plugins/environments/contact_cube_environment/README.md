# contact_cube_environment Environment Plugin

## 責務

`contact_cube_environment/v1`はmanifestからMuJoCo task sceneを構成し、cubeをbackendの
`body` / `geom`としてspawnする。viewer-onlyの装飾objectや独自のcontact計算は持たない。

## composition / reset

`ContactSceneBuildRequest`を受け取った`compose_scene()`が、Robot-owned base model resourceへ
決定的なcube body、freejoint、geom、materialを追加してMuJoCo model/dataをloadする。`reset_scene()`
はmanifestのqpos、qvel、`ctrl`（`data.ctrl`）、`act`（`data.act`）、object pose、time、warm-startを
それぞれのMuJoCo ownerへ再適用し、`mj_forward`後にobjectの初期contact / penetrationを検証する。
contact parameterと`initial_penetration_tolerance_m`はmanifest digestへbindされ、scene側の別defaultで
readinessを変更しない。

## measured contact evidence

scene instanceの`observe_contact_evidence()`は`runtime/contact/evidence.py`へ委譲し、
同じMuJoCo `mjData.contact`と公式`mj_contactForce`からtarget-object、self、environmentの
raw contact recordを取得する。ここではforce filter、clamp、reaction-force、task outcome、
viewer用のcontact再計算を行わない。

## identityとfail-closed

sceneはmanifest digest、object body / geom名、Environment / Robot / viewer identity、MuJoCo
settingを保持する。base modelのname衝突、未知のsolver / integrator、reset dimension mismatch、
scene identity mismatch、初期object contact / penetrationは成功へ変換せず拒否する。

## parametersとside effect

typed `request`だけを受け付ける。importはdeclarationを公開するだけで、scene load、physics step、
external I/Oを開始しない。`compose_scene()`のmodel loadと`reset_scene()`のMuJoCo mutationは
runtimeのbackend-owned lifecycleである。hardware、serial、Arduino、OSC、robot outputは扱わない。

## canonical references

- [contact task / object manifest](../../../../../docs/contracts/contact-task-manifest.md)
- [experiment plugin composition](../../../../../docs/contracts/experiment-plugin-composition.md)
- [runtime composition](../../../../../docs/architecture/runtime-composition.md)
