---
status: canonical
owner: operations
last_verified: 2026-07-29
canonical_for:
  - R7-A-lite Selfrionette serial dry-run smoke
related:
  - docs/contracts/r7-a-lite-serial-frame-contract.md
  - docs/operations/r7-b-manual-live-selfrionette-runtime-runner.md
---

# R7-A-lite Selfrionette serial dry-run smoke

## 目的とownership

hardware accessなしでSelfrionette固有7 channel protocolのinjected backendを検証する。
production Input Source identityは`selfrionette/v1`であり、recorded / injected linesは別の
production identityではない。

```text
Selfrionette recorded serial lines
-> protocol parser
-> loadcell_vector_sample/v1
-> loadcell_normalized_input_intent/v1
-> loadcell_endpoint_mapping/v1
-> MotionCommand.metadata["desired_endpoint_m"]
```

Selfrionette packageはserial framing、7 channel validation、intrinsic calibration /
normalization、diagnostics、healthを所有する。Mappingはoperational deadzone、gain、sign、
channel-to-axis assignment、endpoint conversionを所有する。

## current implementation

- `src/xpotato_sim/plugins/input_sources/selfrionette/`
- `src/xpotato_sim/runtime/runners/selfrionette_serial_dry_run.py`
- `src/xpotato_sim/plugins/mappings/loadcell_endpoint_mapping/`
- `tests/fixtures/r7_a_lite_serial_frames/minimal_valid.txt`
- `tests/fixtures/r7_a_lite_serial_frames/malformed.txt`
- `scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py`
- `scripts/hardware/selfrionette/run_live_selfrionette_runtime.py`
- `scripts/hardware/selfrionette/monitor_selfrionette_serial.ps1`
- `scripts/hardware/selfrionette/measure_loadcell_channel_response.ps1`
- `scripts/hardware/selfrionette/plot_loadcell_vectors.py`（旧.ps1は引数転送のみ）

`monitor_selfrionette_serial.ps1`は`status` / `warn` / `vector` protocolとcalibration
commandを扱うためdevice-specificである。`measure_loadcell_channel_response.ps1`と
`plot_loadcell_vectors.py`の`loadcell`はsensor response / recorded sample semanticsを
表すためbasenameを維持するが、Selfrionette固有protocol owner配下に置く。

## offline fixture smoke

```powershell
uv run python scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py `
  --fixture tests/fixtures/r7_a_lite_serial_frames/minimal_valid.txt `
  --max-vectors 1 `
  --current-tip-position-m 0.25,0.5,0.75 `
  --scale 100000.0 `
  --deadzone 0.0 `
  --gain-m 1.0 `
  --max-delta-m 0.03
```

期待するsummary:

```text
frames_read=1
vectors=1
diagnostics=5
last_endpoint_delta_m=(...)
last_desired_endpoint_m=(...)
```

確認点:

- `status` / `warn` diagnosticsを保持する
- `vector` lineを7 channel `RawInputFrame`へ変換する
- intrinsic normalizationとMapping operational semanticsを順に適用する
- `metadata["desired_endpoint_m"]`と`metadata["endpoint_delta_m"]`を生成する
- `target_position_m`をprimary commandとして追加しない
- serial portを開かない

malformed fixtureはdeterministic failureの確認に使用する。

```powershell
uv run python scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py `
  --fixture tests/fixtures/r7_a_lite_serial_frames/malformed.txt
```

## recorded vectorのoffline plot

```powershell
just loadcell-plot --input-path tests/fixtures/r7_a_lite_serial_frames/minimal_valid.txt --output-path tmp/loadcell.png
```

fileまたはstdinの記録済みvector行だけを読み、CSVとsample index基準のPNGを作る。
serial port、校正、Mapping、health判定は実行しない。詳細は
[repository操作入口](repository-commands.md#offline-plotとbrowser-smoke)を参照する。

## manual live serial

live serialは人間のoperatorだけがhardware safety gate後に実行する。

```powershell
.\scripts\hardware\selfrionette\monitor_selfrionette_serial.ps1 -Port COM5 -Calibrate
.\scripts\hardware\selfrionette\measure_loadcell_channel_response.ps1 -Port COM5 -AllSensors
```

Codex / CIはCOM port open、firmware upload、OSC、actuator / robot output、hardware
validationを行わない。
