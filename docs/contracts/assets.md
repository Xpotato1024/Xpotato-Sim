---
status: canonical
owner: architecture
last_verified: 2026-07-19
canonical_for:
  - model asset contract
related:
  - docs/contracts/robot-profile-runtime-viewer-profile.md
---

# asset契約

この文書は、MJCF、XML、STL、scale、axis、origin、mesh配置の前提に関する
canonical contractである。

## Robot Plugin resource ownership

resourceのphysical ownerはtyped declarationで明示する。`RepositoryResource`は許可されたrepository root内の
fileを、`PackageResource`はimport可能なPython packageとpackage-relative pathを、
`PackageResourceBundle`はMuJoCo VFSへ渡すentrypointとbundle内relative layoutを表す。
generic catalog、runtime、viewerはrobot IDや文字列形式からowner/pathを推測しない。`ROBOT_PLUGIN`の
`RobotResourceDeclaration`がmodel、configuration、viewer declaration、viewer fixture、viewer VFS resourceを
明示し、`ViewerRobotDeclaration`がstable logical resource path / public URL対応を明示する。
plugin-owned `viewer-package-resource-bindings/v1` manifestはconcrete resource inventoryの唯一のSoTであり、
logical identifier、public URL、owning package、package-relative path、bundle-relative path、resource roleを持つ。
Python registrationとviewer build toolingは同じmanifestをdecodeし、viewer declarationのmodel、fixture、VFS mappingと
完全一致することをfail-closedに検証する。manifestはvisual style、joint、qpos、initial pose labelを所有せず、
それらは引き続きviewer declarationが所有する。

production discoveryはcatalog registryを公開する前に、repository resourceが許可root内の実fileへ、package
resourceが宣言package内の実fileへ解決すること、Profileのmodel / configuration referenceと一致すること、viewer declarationと
backend resource declarationが一致することを検証する。MJCFの`include`とmesh / texture / hfield fileは
宣言済みVFS mappingで解決できなければならない。absolute path、`..`によるescape、remote URL、missing
resourceはstartup failureであり、warning skipまたはrobot ID由来pathへのfallbackを行わない。さらに
`RepositoryResource`ではregistration identityが`<robot_id>`ならasset resourceは`assets/mujoco/<robot_id>/`、configurationは
`configs/<robot_id>/`の内側に限定する。lexical declarationだけでなくsymlink解決後の実pathも同じrobot固有
directory内に残ることを要求する。このgateはmodel、viewer declaration、viewer fixture、VFS asset、configurationの
全resource種別へ適用し、sibling robot resourceへのdirect reference / symlinkとrepository resource root外への
symlinkを拒否する。viewer public URLがowned pathを指していても、resolved file ownership違反を許可しない。
shared resourceは暗黙許可せず、必要時に独立した明示contractを追加する。package name、package-relative path、
logical identifier、bundle-relative pathは個別にvalidateし、path traversal、symlink escape、missing package/fileを
fail-closedで拒否する。checkout path fallback、runtime `sys.path`変更、network fetchは行わない。

## fast_armのcanonical asset

- `fast_arm_core` packageが`resources/model/arm.xml`、`resources/model/meshes/*.stl`、
  `resources/config/joint_limits.toml`を所有する。
- Selfrionette adapter packageが`resources/mujoco/scene.xml`、`resources/viewer-profile.json`、
  `resources/fixtures/fast_arm_sweep_x_qpos.json`と`resources/viewer-resource-bindings.json`を所有する。
- concrete binding inventoryはadapter-owned manifestだけに置く。generic viewer build toolingはuv workspace metadataと
  package `src` layoutから宣言packageをbuild時に解決し、robot ID、mesh名、core / adapter pathを列挙しない。
- `assets/mujoco/fast_arm/...`と`configs/fast_arm/...`はviewer/backend互換のstable logical identifierであり、
  physical repository pathではない。旧directoryにproduction duplicateを置かない。
- `arm.xml`はcanonicalなbundle layoutである`meshdir="meshes"`を使用し、同じtyped bundleの
  `meshes/`からmesh fileを解決しなければならない。
- `scene.xml`は同じMuJoCo VFS bundleの`arm.xml`をincludeしなければならない。package間のfilesystem相対pathは使わない。
- STL filenameは、既存の`Sholder`という綴りを含め、legacy asset名を維持する。
- joint、body、siteの名前はmodel contractの一部であり、stable identifierとして扱う。
- asset path修正とmodel semantics変更を同じ変更として暗黙に扱わない。
- headless model loaderはtyped package bundleのbytesをMuJoCo VFSへ渡す。観測可能なlogical model pathは
  `assets/mujoco/fast_arm/scene.xml`のまま維持する。
- MuJoCoのimportは`src/xpotato_sim/mujoco_backend/`内に限定する。
- state snapshotのownershipはbackend / runtime contractに従う。

他の文書ではasset ruleを再記載せず、この文書へlinkする。
