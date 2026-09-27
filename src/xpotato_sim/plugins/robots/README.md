# Robot Plugin

## 責務

Robot axisは、logical RobotをProfile、Runtime Plugin、typed providerを束ねたBundle、
viewer declaration、resource declarationとしてSelfrionetteへ接続する。

## 置けるもの / 置けないもの

- 置けるもの: robot固有model contract、kinematics adapter、Bundle assembly、resource binding
- 置けないもの: generic runtime composition、viewer rendering、別axisのinput acquisition / mapping

## contractとI/O

- required contract: [Robot Profile / Runtime / Viewer contract](../../../../docs/contracts/robot-profile-runtime-viewer-profile.md)
- input: version付きRobot selectionとruntime config
- output: Profile、Runtime Plugin、Bundle、typed capability / command semantic provider

## lifecycleとside effect

declarationとdiscoveryはdeviceへ接続しない。model load、MuJoCo simulator構築、command実行は
runtimeが選択済みBundleをassemblyした後に行う。viewer declarationはrendering resourceの宣言であり、
physical stateやFK / IKのownerではない。

## catalog / discovery / registration

[`discovery.py`](discovery.py)が固定`plugin.py` / `ROBOT_PLUGIN`をfail-closedで読み、
[`catalog.py`](catalog.py)がdiscovery結果からcatalogとprojection resolverを構成する。
[`registration.py`](registration.py)はBundle、viewer、resourceのonboarding contractを所有する。
catalogへ具体Robotを手書き登録しない。

## shared private owner

axis共通のregistration、discovery、catalogだけをこのdirectory直下に置く。robot固有ownerは
各concrete packageに閉じる。

## concrete pluginの追加

direct-child packageへside-effect-freeな`__init__.py`、`plugin.py`、Profile、Runtime Plugin、
Bundle、viewer / resource declaration、README、conformance testを追加する。generic層とcatalogへ
具体IDを追加しない。

## canonical document

- [dependency boundary](../../../../docs/architecture/dependency-boundaries.md)
- [Robot onboarding contract](../../../../docs/contracts/robot-profile-runtime-viewer-profile.md)
