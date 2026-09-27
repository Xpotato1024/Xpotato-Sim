"""Issue #485で確定した重要code documentation boundaryのfocused guard。"""

from __future__ import annotations

import ast
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _module(path: str) -> ast.Module:
    return ast.parse((REPOSITORY_ROOT / path).read_text(encoding="utf-8"))


def _documented_top_level_symbols(path: str) -> dict[str, str]:
    tree = _module(path)
    return {
        node.name: ast.get_docstring(node) or ""
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _class_contract(path: str, name: str) -> tuple[set[str], str]:
    for node in _module(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            fields = {
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
            }
            return fields, ast.get_docstring(node) or ""
    raise AssertionError(f"{name} was not found in {path}")


def _contains_japanese(value: str) -> bool:
    return any(
        "\u3040" <= character <= "\u30ff" or "\u4e00" <= character <= "\u9fff"
        for character in value
    )


def test_architecture_sensitive_python_contracts_have_semantic_documentation() -> None:
    required_symbols = {
        "src/xpotato_sim/runtime/experiment/contracts.py": {
            "VersionedIdentity",
            "CommandSemanticsRoute",
            "EnvironmentPlugin",
            "ControlMappingPlugin",
            "TaskPlugin",
            "EvaluationPlugin",
        },
        "src/xpotato_sim/runtime/experiment/composition.py": {
            "ResolvedExperimentComposition",
            "compose_experiment",
        },
        "src/xpotato_sim/runtime/composition/robot_bundle.py": {
            "RobotCommandSemanticProvider",
            "RobotBundle",
        },
        "src/xpotato_sim/runtime/execution/command_routes.py": {
            "ResolvedCommandExecution",
            "NativeEndpointVelocityCommandExecutionBinding",
        },
        "src/xpotato_sim/runtime/control/endpoint_target_generator.py": {
            "EndpointTargetGeneratorConfig",
            "EndpointTargetGeneratorState",
            "generate_endpoint_target",
        },
    }

    for path, names in required_symbols.items():
        documented = _documented_top_level_symbols(path)
        assert names <= documented.keys()
        assert all(documented[name].strip() for name in names)


def test_r7_g_public_plugin_contracts_use_japanese_documentation() -> None:
    required_symbols = {
        "src/xpotato_sim/plugins/environments/free_space_environment/implementation.py": {
            "FreeSpaceSceneCondition",
            "FreeSpaceSceneProvider",
        },
        "src/xpotato_sim/plugins/tasks/endpoint_reach_task/implementation.py": {
            "EndpointReachTaskBinding",
            "EndpointReachTaskLifecycle",
            "EndpointReachTaskState",
        },
        "src/xpotato_sim/plugins/evaluations/success_within_timeout/implementation.py": {
            "SuccessWithinTimeoutDeriver",
        },
        "src/xpotato_sim/plugins/evaluations/off_axis_drift/implementation.py": {
            "OffAxisDriftDeriver",
        },
        "src/xpotato_sim/plugins/evaluations/completion_time/implementation.py": {
            "CompletionTimeDeriver",
        },
        "src/xpotato_sim/plugins/evaluations/final_endpoint_error/implementation.py": {
            "FinalEndpointErrorDeriver",
        },
        "src/xpotato_sim/runtime/experiment/endpoint_reach_evidence.py": {
            "EndpointReachObservation",
            "EndpointReachTaskContext",
            "decode_endpoint_reach_terminal_evidence",
            "decode_endpoint_reach_trajectory_evidence",
        },
        "src/xpotato_sim/runtime/composition/production_experiment.py": {
            "resolve_production_experiment",
        },
        "src/xpotato_sim/runtime/evaluation/r7_g_free_space.py": {
            "build_r7_g_free_space_manifest_pair",
        },
    }

    for path, names in required_symbols.items():
        module = _module(path)
        assert _contains_japanese(ast.get_docstring(module) or "")
        documented = _documented_top_level_symbols(path)
        assert names <= documented.keys()
        assert all(_contains_japanese(documented[name]) for name in names)


def test_schema_documentation_matches_current_field_and_owner_boundaries() -> None:
    command_path = "src/xpotato_sim/schemas/command.py"
    endpoint_fields, endpoint_doc = _class_contract(
        command_path, "EndpointVelocityCommand"
    )
    motion_fields, motion_doc = _class_contract(command_path, "MotionCommand")
    assert endpoint_fields == {"timestamp_s", "velocity_m_s", "frame"}
    assert "max_delta_m" not in endpoint_doc
    assert motion_fields == {"timestamp_s", "target", "joint", "metadata"}
    assert "exclusive" not in motion_doc

    raw_fields, raw_doc = _class_contract(
        "src/xpotato_sim/schemas/input.py", "RawInputFrame"
    )
    assert raw_fields == {"source", "timestamp_s", "values", "buttons", "metadata"}
    assert "JSON-compatibleなsnapshotへfreeze" not in raw_doc

    viewer_fields, viewer_doc = _class_contract(
        "src/xpotato_sim/schemas/viewer_control.py", "ViewerControlMessage"
    )
    assert {"sequence", "keyboard", "gamepad"} <= viewer_fields
    assert "monotonic sequence" not in viewer_doc

    experiment_path = "src/xpotato_sim/schemas/experiment_log.py"
    configuration_fields, configuration_doc = _class_contract(
        experiment_path, "ConfigurationRecord"
    )
    assert {
        "configuration_id",
        "software_revision",
        "initial_qpos_rad",
        "target_world_position_m",
        "source_kind",
        "comparison_parameters",
    } <= configuration_fields
    assert {
        "manifest_digest",
        "resolved_digest",
        "freeze_digest",
    }.isdisjoint(configuration_fields)
    assert "manifest v3/readiness freeze identity" not in configuration_doc
    assert "EvaluationManifest" in configuration_doc
    assert "FreezeRecord" in configuration_doc
    module_doc = ast.get_docstring(_module(experiment_path)) or ""
    assert "manifest/readiness identity" not in module_doc


def test_fixed_plugin_entry_points_document_declaration_without_starting_lifecycle() -> None:
    entry_points = sorted(
        (REPOSITORY_ROOT / "src/xpotato_sim/plugins").glob("**/plugin.py")
    )
    assert entry_points

    for path in entry_points:
        module_doc = ast.get_docstring(ast.parse(path.read_text(encoding="utf-8"))) or ""
        assert "declaration" in module_doc
        assert "import" in module_doc
        assert any(marker in module_doc for marker in ("開始しない", "行わない", "実行しない"))


def test_viewer_boundary_jsdoc_preserves_mujoco_sot_and_qpos_ordering() -> None:
    qpos_sync = (
        REPOSITORY_ROOT
        / "apps/mujoco-viewer/src/wasm-scene/mujocoQposSync.ts"
    ).read_text(encoding="utf-8")
    renderer = (
        REPOSITORY_ROOT
        / "apps/mujoco-viewer/src/wasm-scene/mujocoSceneRenderer.ts"
    ).read_text(encoding="utf-8")
    input_provider = (
        REPOSITORY_ROOT
        / "apps/mujoco-viewer/src/input/viewerInputProvider.ts"
    ).read_text(encoding="utf-8")

    assert "model ordering" in qpos_sync
    assert "fallbackしない" in qpos_sync
    assert "physical stateをSoT" in renderer
    assert "viewer-side FK/IK" in renderer
    assert "backend Mapping" in input_provider
