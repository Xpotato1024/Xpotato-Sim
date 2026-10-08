# scripts

repository operationを補助する明示実行scriptの入口である。script名の一覧を正本化せず、用途別directoryと
canonical operationへrouteする。

## responsibility

`workbench_local.py`は引継ぎ用のroot `.env`、専用環境、固定buildと正式CLI組立てを所有する。
justなしでも同じscriptを使う。physicsや試行のownerを持たず、通常起動で同期・buildを行わない。
初回・通常・更新後・開発の手順は[root README](../README.md)、起動契約は
[Workbench](../docs/contracts/workbench.md)を参照する。

- `repository/`: Markdown / GitHub body等のrepository validationとjustの既存commandへのargv転送
- `viewer/`: viewer fixture exportとbrowser / live smoke
- `diagnostics/`: software-only Robot diagnostics
- `hardware/`: serial / deviceを扱いうるoperator-gated script

## safety

`hardware/`のlive scriptは閲覧だけで実行せず、
[hardware safety](../docs/operations/hardware-safety.md)に従ってdevice、port、stop手順を確認する。
記録済みfile/stdinだけを扱う`just loadcell-plot`と既存dry-runはoffline例外である。
diagnosticsやdry-runをhardware validationと呼ばない。

## canonical routing

- [validation](../docs/operations/validation.md)
- [backend / viewer startup](../docs/operations/backend-viewer-startup.md)
- [hardware safety](../docs/operations/hardware-safety.md)

信号からcontactまでのhardware-free実行は[pre-hardware contract](../docs/contracts/pre-hardware-signal-emulation.md)のCLI手順を参照する。
