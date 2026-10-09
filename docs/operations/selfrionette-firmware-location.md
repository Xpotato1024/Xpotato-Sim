---
status: canonical
owner: operations
last_verified: 2026-10-09
canonical_for:
  - Selfrionette firmware repository and build entry
related:
  - docs/contracts/r7-a-lite-serial-frame-contract.md
  - docs/operations/hardware-safety.md
---

# Selfrionette firmwareの入手とbuild

firmwareの所有repositoryは[Selfrionette-Device](https://github.com/Xpotato1024/Selfrionette-Device)である。
Simはfirmware source、PlatformIO project、producer側の校正algorithmを保持しない。
Simの起動・test・offline fixtureにDevice cloneやfirmware buildを必須化しない。

firmwareの確認が必要な利用者だけ、Simとは別directoryへDeviceをcloneする。
この分離で確認したDevice commitへ固定する:

```powershell
git clone https://github.com/Xpotato1024/Selfrionette-Device.git
Set-Location Selfrionette-Device
git switch --detach 79c84bce463da2a2ff01d90ec81d9baac746c7d1
```

既存checkoutに未commit作業があればそのcheckoutを切り替えず、別directoryを使う。
[Deviceのbuild手順](https://github.com/Xpotato1024/Selfrionette-Device/blob/79c84bce463da2a2ff01d90ec81d9baac746c7d1/docs/operations/legacy-sim-firmware.md)に従う。
[現行v2 firmware](https://github.com/Xpotato1024/Selfrionette-Device/blob/79c84bce463da2a2ff01d90ec81d9baac746c7d1/firmware/loadcell_7ch/README.md)と、
[旧Sim互換2ターゲット](https://github.com/Xpotato1024/Selfrionette-Device/blob/79c84bce463da2a2ff01d90ec81d9baac746c7d1/firmware/legacy/xpotato-sim/arduino/legacy_selfrionette/README.md)は用途が異なる。
旧版のvector互換だけでv2 identity・EEPROM・管理commandの対応を認定しない。

## 所有境界

Deviceはsensor acquisition、physical tare/calibration、producer protocol、identity、EEPROM、
firmware buildと更新を所有する。Simは[受信parser・取得/healthの互換契約](../contracts/r7-a-lite-serial-frame-contract.md)、
記録済みfixture、Input Source Plugin、robot control Mapping、simulationを所有する。
本移行でparser/normalization/Mapping/physicsの挙動は変更しない。

## 採用順と履歴

先にDeviceの取り込みPRを採用し、source・履歴・build結果を確認した後にSimの削除PRを採用する。
両PRのDraft作成だけで採用・全面退役を完了扱いしない。submoduleやruntimeのcross-repository importは追加しない。

元の15ファイルは[分離前Sim commit](https://github.com/Xpotato1024/Xpotato-Sim/tree/552180b65d2055b63854cd3a25bc1d2e9308fb36/firmware)から復元できる。
Deviceにはfirmware配下の4件の変更履歴をsquashせず取り込み、元treeとの一致を検証した。
[履歴とcompile検証記録](https://github.com/Xpotato1024/Selfrionette-Device/blob/79c84bce463da2a2ff01d90ec81d9baac746c7d1/docs/evidence/2026-10-09-firmware-import.md)を参照する。
過去の実験note・inventoryに残るSim内のpathは当時のsource locationであり、現在のbuild入口ではない。

compileはsoftware検証である。serial open、upload、EEPROM書込や実機作動は
[hardware safety](hardware-safety.md)の別gateを必要とし、この移行では実行しない。
