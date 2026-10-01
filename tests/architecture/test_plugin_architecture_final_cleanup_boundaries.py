from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from xpotato_sim.plugins.input_sources.catalog import INPUT_SOURCE_CATALOG
from xpotato_sim.plugins.mappings.catalog import CONTROL_MAPPING_REGISTRY


ROOT = Path(__file__).resolve().parents[2]
PLUGINS = ROOT / "src" / "xpotato_sim" / "plugins"
SOURCE_ROOT = PLUGINS / "input_sources"
MAPPING_ROOT = PLUGINS / "mappings"
ROBOT_ROOT = PLUGINS / "robots"


def _string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _concrete_identity_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"PluginSelection", "VersionedIdentity"}
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def test_final_source_identity_and_mixed_owners_are_retired() -> None:
    assert INPUT_SOURCE_CATALOG.ids == (
        "analog_fixture",
        "noop",
        "programmed_target",
        "replay",
        "selfrionette",
        "viewer",
    )
    for retired in (
        SOURCE_ROOT / "_common.py",
        SOURCE_ROOT / "_loadcell" / "__init__.py",
        SOURCE_ROOT / "loadcell_serial" / "plugin.py",
        SOURCE_ROOT / "loadcell_fixture" / "plugin.py",
    ):
        assert not retired.is_file()
    assert (SOURCE_ROOT / "selfrionette" / "protocol.py").is_file()


def test_axis_local_infrastructure_and_retired_root_paths_are_exact() -> None:
    assert {path.name for path in PLUGINS.glob("*.py")} == {
        "__init__.py",
        "bounded_discovery.py",
    }
    assert {
        "catalog.py",
        "discovery.py",
        "registration.py",
    } <= {path.name for path in ROBOT_ROOT.glob("*.py")}
    assert {
        "catalog.py",
        "discovery.py",
        "registration.py",
    } <= {path.name for path in SOURCE_ROOT.glob("*.py")}
    assert {"catalog.py", "discovery.py"} <= {
        path.name for path in MAPPING_ROOT.glob("*.py")
    }
    assert not (MAPPING_ROOT / "registration.py").exists()
    for retired_name in (
        "catalog.py",
        "robot_discovery.py",
        "robot_registration.py",
        "input_source_discovery.py",
        "input_source_registration.py",
        "control_mapping_discovery.py",
    ):
        assert not (PLUGINS / retired_name).exists()


def test_retired_axis_infrastructure_modules_are_not_importable() -> None:
    for module_name in (
        "xpotato_sim.plugins.catalog",
        "xpotato_sim.plugins.robot_discovery",
        "xpotato_sim.plugins.robot_registration",
        "xpotato_sim.plugins.input_source_discovery",
        "xpotato_sim.plugins.input_source_registration",
        "xpotato_sim.plugins.control_mapping_discovery",
    ):
        assert importlib.util.find_spec(module_name) is None


def test_plugin_packages_do_not_own_cross_axis_concrete_ids() -> None:
    mapping_ids = set(CONTROL_MAPPING_REGISTRY.ids)
    source_ids = set(INPUT_SOURCE_CATALOG.ids)
    for source_id in source_ids:
        literals = set().union(
            *(_string_literals(path) for path in (SOURCE_ROOT / source_id).rglob("*.py"))
        )
        assert not literals & mapping_ids
    for mapping_id in mapping_ids:
        concrete_identity_names = set().union(
            *(
                _concrete_identity_names(path)
                for path in (MAPPING_ROOT / mapping_id).rglob("*.py")
            )
        )
        assert not concrete_identity_names & source_ids


def test_input_source_packages_do_not_project_mapping_parameters() -> None:
    source_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE_ROOT.rglob("*.py")
    )
    for mapping_parameter_surface in (
        "viewer_mapping_parameters",
        "mapping_compatibility_parameters",
        "gamepad_speed_m_s",
        "gamepad_deadzone",
        "gamepad_max_delta_m",
        "keyboard_config",
        "operational_deadzone",
    ):
        assert mapping_parameter_surface not in source_text


def test_catalog_order_is_identity_derived_without_plugin_ordinals() -> None:
    registration = (
        SOURCE_ROOT / "registration.py"
    ).read_text(encoding="utf-8")
    catalog = (SOURCE_ROOT / "catalog.py").read_text(encoding="utf-8")
    plugin_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in SOURCE_ROOT.glob("*/plugin.py")
    )
    for retired_field in ("catalog_order", "generic_cli_order"):
        assert retired_field not in registration
        assert retired_field not in catalog
        assert retired_field not in plugin_sources
    assert "item.plugin.identity.canonical_id" in catalog


def test_temporary_plugin_compatibility_facades_are_absent() -> None:
    retired = (
        MAPPING_ROOT / "analog_fixture.py",
        MAPPING_ROOT / "continuous_endpoint_velocity.py",
        MAPPING_ROOT / "keyboard.py",
        MAPPING_ROOT / "loadcell.py",
        MAPPING_ROOT / "replay.py",
        MAPPING_ROOT / "viewer.py",
    )
    assert all(not path.is_file() for path in retired)
    mapping_root = (MAPPING_ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "_PUBLIC_EXPORTS" not in mapping_root
    assert "__getattr__" not in mapping_root
    source_catalog = (SOURCE_ROOT / "catalog.py").read_text(encoding="utf-8")
    runtime_contract = (
        ROOT / "src" / "xpotato_sim" / "runtime" / "experiment" / "input_source.py"
    ).read_text(encoding="utf-8")
    for retired_name in (
        "INPUT_SOURCE_PLUGIN_REGISTRY",
        "SUPPORTED_INPUT_SOURCE_NAMES",
        "InputSourceMappingAdapter =",
        "SourceMode =",
        "def create_reader(",
        "def produced_sample_schema_identity(",
    ):
        assert retired_name not in source_catalog
        assert retired_name not in runtime_contract


def test_command_route_execution_is_provider_bound_without_central_id_dispatch() -> None:
    command_routes = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "execution"
        / "command_routes.py"
    ).read_text(encoding="utf-8")
    step_loop = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "execution"
        / "input_step_loop.py"
    ).read_text(encoding="utf-8")
    robot_bundle = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "composition"
        / "robot_bundle.py"
    ).read_text(encoding="utf-8")
    robot_provider_adapters = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "composition"
        / "robot_provider_adapters.py"
    ).read_text(encoding="utf-8")

    assert "plan.pipeline.execute_intent(" in step_loop
    assert "command_semantic_providers" in robot_bundle
    assert "supported_command_semantics:" not in robot_bundle
    assert "command_type: ClassVar[type] = JointPositionCommand" in command_routes
    assert "command_type: ClassVar[type] = MotionCommand" not in command_routes
    assert "command_type: ClassVar[type] = EndpointVelocityCommand" in command_routes
    assert "command_type = JointPositionCommand" in robot_provider_adapters
    assert "command_type = MotionCommand" not in robot_provider_adapters
    for concrete_route_constant in (
        "LOCAL_ENDPOINT_VELOCITY_TO_JOINT_POSITION_V1",
        "ENDPOINT_DELTA_TO_JOINT_POSITION_V1",
        "REPLAY_COMMAND_TO_JOINT_POSITION_V1",
        "NATIVE_ENDPOINT_VELOCITY_PASSTHROUGH_V1",
    ):
        assert concrete_route_constant not in command_routes


def test_production_pipeline_builders_do_not_accept_prebound_command_execution() -> None:
    composition_root = (
        ROOT / "src" / "xpotato_sim" / "runtime" / "composition"
    )
    builder_paths = (
        composition_root / "concrete_mujoco_pipeline.py",
        composition_root / "replay_mujoco_pipeline.py",
    )
    for path in builder_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        builder = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name.startswith("build_")
            and node.name.endswith("_mujoco_pipeline")
        )
        keyword_names = {
            argument.arg for argument in builder.args.kwonlyargs
        }
        assert "resolved_command_execution" not in keyword_names
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "resolve_command_execution"
            for node in ast.walk(builder)
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id
            == "validate_production_robot_selection_consistency"
            for node in ast.walk(builder)
        )

    replay_path = composition_root / "replay_mujoco_pipeline.py"
    replay_tree = ast.parse(
        replay_path.read_text(encoding="utf-8"),
        filename=str(replay_path),
    )
    replay_builder = next(
        node
        for node in replay_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "build_replay_mujoco_pipeline"
    )
    replay_arguments = {
        argument.arg for argument in replay_builder.args.kwonlyargs
    }
    assert {
        "resolved_command_execution",
        "simulator",
        "robot_profile_metadata",
        "qpos_feasibility_guard",
        "initial_keyframe_name",
    }.isdisjoint(replay_arguments)
    attribute_calls = {
        node.func.attr
        for node in ast.walk(replay_builder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert {
        "build_simulator",
        "validate_model",
        "build_guard",
    } <= attribute_calls
    replay_source = replay_path.read_text(encoding="utf-8")
    validation_line = next(
        node.lineno
        for node in ast.walk(replay_builder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "validate_production_robot_selection_consistency"
    )
    build_simulator_line = next(
        node.lineno
        for node in ast.walk(replay_builder)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "build_simulator"
    )
    assert validation_line < build_simulator_line
    assert "robot_bundle.runtime_plugin" in replay_source
    assert "robot_profile_runtime_metadata(robot_bundle.profile)" in replay_source

    robot_resolution_source = (
        composition_root / "robot_resolution.py"
    ).read_text(encoding="utf-8")
    for required_check in (
        "bundle_identity != expected_identity",
        "profile.profile_id != selection.plugin_id",
        "plugin.profile_id != selection.plugin_id",
        "profile.profile_contract_version != selection.contract_version",
        "validate_robot_profile_plugin_consistency(",
    ):
        assert required_check in robot_resolution_source

    registration_source = (
        ROOT / "src" / "xpotato_sim" / "plugins" / "robots" / "registration.py"
    ).read_text(encoding="utf-8")
    assert "validate_production_robot_selection_consistency(" in registration_source
    robot_bundle_source = (
        composition_root / "robot_bundle.py"
    ).read_text(encoding="utf-8")
    assert (
        "validate_production_robot_selection_consistency"
        not in robot_bundle_source
    )

    step_loop = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "execution"
        / "input_step_loop.py"
    ).read_text(encoding="utf-8")
    assert "resolved_command_execution" not in step_loop
    assert "simulator=simulator" not in step_loop
    assert "robot_profile_metadata=" not in step_loop
    assert "qpos_feasibility_guard=" not in step_loop
    assert "command_semantics_route=pipeline.command_semantics_route" in step_loop
    assert "command_execution=pipeline.command_execution" in step_loop
    assert "tests.support" not in step_loop
    for path in (ROOT / "src" / "xpotato_sim" / "runtime").rglob("*.py"):
        assert "tests.support" not in path.read_text(encoding="utf-8")

    offline_smoke_path = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "runners"
        / "offline_input_smoke.py"
    )
    offline_tree = ast.parse(
        offline_smoke_path.read_text(encoding="utf-8"),
        filename=str(offline_smoke_path),
    )
    offline_builder = next(
        node
        for node in offline_tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "run_offline_input_runtime_stepping_smoke"
    )
    assert "resolved_command_execution" not in {
        argument.arg
        for argument in (
            *offline_builder.args.args,
            *offline_builder.args.kwonlyargs,
        )
    }
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "resolve_command_execution"
        for node in ast.walk(offline_builder)
    )


def test_production_runtime_cannot_reintroduce_motion_command_backend_bypass() -> None:
    runtime_root = ROOT / "src" / "xpotato_sim" / "runtime"
    violations: list[str] = []
    for path in runtime_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "apply_command"
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in {
                    "apply_command",
                    "apply_joint_position_command",
                }
                and path.name != "robot_provider_adapters.py"
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:"
                    f"backend getattr {node.args[1].value}"
                )
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                if isinstance(value, ast.Name) and value.id == "MotionCommand":
                    targets = (
                        node.targets
                        if isinstance(node, ast.Assign)
                        else (node.target,)
                    )
                    if any(
                        isinstance(target, ast.Name)
                        and target.id == "command_type"
                        for target in targets
                    ):
                        violations.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}:"
                            "command_type=MotionCommand"
                        )
        if (
            path.name != "command_adapter.py"
            and "motion_command_to_qpos_command(" in path.read_text(
                encoding="utf-8"
            )
        ):
            violations.append(
                f"{path.relative_to(ROOT)}:"
                "motion_command_to_qpos_command"
            )

    assert violations == []


def test_retired_motion_command_backend_entry_and_calls_are_absent() -> None:
    source_root = ROOT / "src" / "xpotato_sim"
    call_owners: set[tuple[str, str]] = set()
    for path in source_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents: dict[ast.AST, ast.AST] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                parents[child] = parent
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "apply_command"
            ):
                continue
            owner = parents.get(node)
            while owner is not None and not isinstance(
                owner,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                owner = parents.get(owner)
            call_owners.add(
                (
                    path.relative_to(ROOT).as_posix(),
                    "<module>" if owner is None else owner.name,
                )
            )

    assert call_owners == set()
    simulator = source_root / "mujoco_backend/simulator.py"
    tree = ast.parse(simulator.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.FunctionDef) and node.name == "apply_command" for node in ast.walk(tree))
    assert not (source_root / "mujoco_backend/command_adapter.py").exists()


def test_endpoint_motion_capability_is_not_a_robot_command_semantic() -> None:
    contracts = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "experiment"
        / "contracts.py"
    ).read_text(encoding="utf-8")
    bundle = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "composition"
        / "robot_bundle.py"
    ).read_text(encoding="utf-8")
    assert 'ENDPOINT_COMMAND_V1 = VersionedIdentity("endpoint_command", 1)' in bundle
    assert (
        'ENDPOINT_POSITION_COMMAND_V1 = VersionedIdentity("endpoint_position_command", 1)'
        in contracts
    )
    assert (
        'ENDPOINT_VELOCITY_COMMAND_V1 = VersionedIdentity("endpoint_velocity_command", 1)'
        in contracts
    )


def test_evaluation_manifest_v3_has_no_misnamed_command_semantics_field() -> None:
    manifest = (
        ROOT
        / "src"
        / "xpotato_sim"
        / "runtime"
        / "evaluation"
        / "manifest.py"
    ).read_text(encoding="utf-8")
    assert '"command_semantics_route_identity"' in manifest
    assert '"requested_command_semantics_route_identity"' in manifest
    assert '"resolved_command_semantics_route"' in manifest
    assert '"command_semantics_identity"' not in manifest
    assert '"requested_command_semantics_identity"' not in manifest


def test_first_party_package_basename_matches_logical_identity() -> None:
    for registration in INPUT_SOURCE_CATALOG.registrations:
        identity = registration.plugin.identity.name
        assert (SOURCE_ROOT / identity / "plugin.py").is_file()
    for identity in CONTROL_MAPPING_REGISTRY.ids:
        assert (MAPPING_ROOT / identity / "plugin.py").is_file()


def test_selfrionette_backend_and_normalization_boundaries_are_explicit() -> None:
    source = (SOURCE_ROOT / "selfrionette" / "protocol.py").read_text(
        encoding="utf-8"
    )
    mapping = (
        MAPPING_ROOT / "loadcell_endpoint_mapping" / "implementation.py"
    ).read_text(encoding="utf-8")
    reader = (SOURCE_ROOT / "selfrionette" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "operational_deadzone" not in source
    assert "operational_deadzone" in mapping
    assert "import serial" in reader
    assert reader.index("def start(") < reader.index("import serial")


def test_test_fixtures_are_not_discoverable_production_sources() -> None:
    assert "fixture" not in INPUT_SOURCE_CATALOG.ids
    assert "test_dummy_input_source" not in INPUT_SOURCE_CATALOG.ids


def test_mapping_default_resource_is_package_owned() -> None:
    package_resource = (
        MAPPING_ROOT
        / "viewer_keyboard_gamepad_mapping"
        / "resources"
        / "keyboard_default.json"
    )
    assert package_resource.is_file()
    assert not (ROOT / "configs" / "input" / "keyboard_default.json").exists()


def test_generic_runtime_uses_typed_health_and_lifecycle_contracts() -> None:
    generic_runtime = "\n".join(
        (
            (
                ROOT
                / "src"
                / "xpotato_sim"
                / "runtime"
                / "execution"
                / "input_step_loop.py"
            ).read_text(encoding="utf-8"),
            (
                ROOT
                / "src"
                / "xpotato_sim"
                / "runtime"
                / "runners"
                / "live_selfrionette.py"
            ).read_text(encoding="utf-8"),
        )
    )
    for migration_duck_surface in (
        'getattr(reader, "start"',
        'getattr(reader, "close"',
        'getattr(source, "start"',
        'getattr(source, "close"',
        'getattr(plan.pipeline.input_source, "current_health"',
    ):
        assert migration_duck_surface not in generic_runtime
    assert "isinstance(reader, ManagedInputSource)" in generic_runtime
    assert "plan.pipeline.input_source.current_health()" in generic_runtime


def test_current_device_facing_surfaces_use_selfrionette_identity() -> None:
    runners = ROOT / "src" / "xpotato_sim" / "runtime" / "runners"
    hardware = ROOT / "scripts" / "hardware"
    operations = ROOT / "docs" / "operations"

    assert (runners / "live_selfrionette.py").is_file()
    assert (runners / "selfrionette_serial_dry_run.py").is_file()
    assert not (runners / "live_loadcell.py").exists()
    assert not (runners / "loadcell_serial_dry_run.py").exists()

    assert (
        hardware / "selfrionette" / "run_live_selfrionette_runtime.py"
    ).is_file()
    assert (
        hardware / "selfrionette" / "run_selfrionette_serial_dry_run.py"
    ).is_file()
    assert (
        hardware / "selfrionette" / "monitor_selfrionette_serial.ps1"
    ).is_file()
    assert not (hardware / "loadcell").exists()

    canonical_docs = "\n".join(
        path.read_text(encoding="utf-8")
        for path in operations.rglob("*.md")
        if "status: canonical" in path.read_text(encoding="utf-8")
        or "status: supporting" in path.read_text(encoding="utf-8")
    )
    for retired_current_surface in (
        "scripts/hardware/loadcell/",
        "run_live_loadcell_runtime.py",
        "run_loadcell_serial_dry_run.py",
        "runtime.runners.live_loadcell",
        "runtime.runners.loadcell_serial_dry_run",
    ):
        assert retired_current_surface not in canonical_docs

    assert "selfrionette/v1" in (
        SOURCE_ROOT / "selfrionette" / "plugin.py"
    ).read_text(encoding="utf-8")
    assert "loadcell_vector_sample" in (
        SOURCE_ROOT / "selfrionette" / "plugin.py"
    ).read_text(encoding="utf-8")
