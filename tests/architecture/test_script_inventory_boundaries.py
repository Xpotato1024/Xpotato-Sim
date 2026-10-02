from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs/reports/implementation/script-inventory-and-retirement.md"
RETIRED_LAUNCHER = "run_mujoco_viewer_dev.py"
TEXT_SUFFIXES = {".md", ".py", ".ps1", ".yml", ".yaml", ".toml", ".txt"}
EXPECTED_SCRIPTS = {
    "scripts/diagnostics/fast_arm/measure_pinch_checkpoint.py",
    "scripts/diagnostics/fast_arm/measure_simulation_hot_path.py",
    "scripts/diagnostics/fast_arm/pinch-checkpoint.json",
    "scripts/viewer/measure_workbench_latency.py",
    "scripts/diagnostics/run_prehardware_signal_e2e.py",
    "scripts/hardware/selfrionette/measure_loadcell_channel_response.ps1",
    "scripts/hardware/selfrionette/monitor_selfrionette_serial.ps1",
    "scripts/hardware/selfrionette/plot_loadcell_vectors.ps1",
    "scripts/hardware/selfrionette/run_live_selfrionette_runtime.py",
    "scripts/hardware/selfrionette/run_selfrionette_serial_dry_run.py",
    "scripts/diagnostics/fast_arm/plot_fast_arm_endpoint_trajectory_log.py",
    "scripts/diagnostics/fast_arm/run_fast_arm_endpoint_motion_sanity.py",
    "scripts/diagnostics/fast_arm/run_fast_arm_jacobian_mobility_diagnostics.py",
    "scripts/diagnostics/fast_arm/run_fast_arm_neutral_pose_evaluator.py",
    "scripts/diagnostics/fast_arm/run_fast_arm_neutral_pose_startup_smoke.py",
    "scripts/diagnostics/fast_arm/view_fast_arm_native_mujoco.py",
    "scripts/viewer/export_wasm_qpos_fixture.py",
    "scripts/viewer/run_live_viewer_smoke.py",
    "scripts/viewer/generate_contact_e2e_artifacts.py",
    "scripts/viewer/generate_contact_task_log_demo.py",
    "scripts/viewer/run-browser-viewer-smoke.ps1",
    "scripts/repository/validate_github_body_structure.py",
    "scripts/repository/validate_agent_skills.py",
    "scripts/repository/validate_markdown_docs.py",
}
HISTORICAL_SCRIPT_NAMES = {
    "measure_loadcell_channel_response.ps1",
    "monitor_loadcell_serial.ps1",
    "plot_loadcell_vectors.ps1",
    "run_live_loadcell_runtime.py",
    "run_loadcell_serial_dry_run.py",
}


def _tracked_scripts() -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "scripts"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return set(result.stdout.splitlines()) - {"scripts/README.md"}


def _current_consumer_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for directory in (ROOT / ".github", ROOT / "apps", ROOT / "tests", ROOT / "scripts"):
        files.extend(
            path for path in directory.rglob("*") if path.is_file() and path.suffix in TEXT_SUFFIXES
        )
    for path in (ROOT / "docs").rglob("*.md"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith(("docs/reports/", "docs/archive/", "docs/experiment-notes/", "docs/audits/")):
            continue
        text = path.read_text(encoding="utf-8")
        if "status: canonical" in text or "status: supporting" in text:
            files.append(path)
    return [path for path in files if path.is_file()]


def test_script_inventory_covers_the_exact_current_paths() -> None:
    assert _tracked_scripts() == EXPECTED_SCRIPTS
    assert {
        path.name for path in (ROOT / "scripts").iterdir() if path.is_file()
    } == {"README.md"}
    assert not any(path.name == ".gitkeep" for path in (ROOT / "scripts").rglob("*"))
    assert not any(
        path.name.lower() == "readme.md" and not path.read_text(encoding="utf-8").strip()
        for path in (ROOT / "scripts").rglob("*")
        if path.is_file()
    )

    report = INVENTORY.read_text(encoding="utf-8")
    current_non_migrated_names = {
        Path(path).name
        for path in EXPECTED_SCRIPTS
        if not path.startswith("scripts/hardware/selfrionette/")
    }
    for name in current_non_migrated_names | HISTORICAL_SCRIPT_NAMES | {
        RETIRED_LAUNCHER
    }:
        assert f"`{name}`" in report

    assert not (ROOT / "scripts" / "hardware" / "loadcell").exists()


def test_old_root_script_paths_are_not_current_consumers() -> None:
    consumers: list[str] = []
    for path in _current_consumer_files():
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8")
        for relative in EXPECTED_SCRIPTS:
            name = Path(relative).name
            if f"scripts/{name}" in text or f"scripts\\{name}" in text:
                consumers.append(f"{path.relative_to(ROOT)}: scripts/{name}")
    assert consumers == []


def test_retired_launcher_has_no_current_consumer() -> None:
    assert not (ROOT / "scripts" / RETIRED_LAUNCHER).exists()

    files = _current_consumer_files()

    consumers = []
    for path in files:
        if not path.is_file() or path == Path(__file__):
            continue
        if path.suffix not in {".md", ".py", ".ps1", ".yml", ".yaml", ".toml"}:
            continue
        if RETIRED_LAUNCHER in path.read_text(encoding="utf-8"):
            consumers.append(path.relative_to(ROOT).as_posix())
    assert consumers == []


def test_canonical_default_commands_use_the_installable_cli() -> None:
    for relative_path in (
        "README.md",
        "docs/operations/runtime-dry-run.md",
        "docs/operations/websocket-publisher-runner.md",
    ):
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "uv run xpotato-sim" in text
