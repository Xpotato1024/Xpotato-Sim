from __future__ import annotations

import importlib
import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from xpotato_sim.mujoco_backend.simulator import HeadlessMuJoCoSimulator
from xpotato_sim.plugins.robots.catalog import RobotCatalog
from xpotato_sim.plugins.robots.discovery import (
    RobotDiscoveryRoot,
    RobotPluginDiscoveryError,
    RobotPluginRegistry,
    discover_robot_plugins,
)
from xpotato_sim.plugins.robots.registration import (
    RepositoryResource,
    RobotResourceDeclaration,
    _resolved_resource,
    _validate_viewer_vfs_coverage,
)
from xpotato_sim.runtime.composition.config import RuntimeConfig
from xpotato_sim.runtime.composition.concrete_mujoco_pipeline import build_concrete_mujoco_pipeline
from xpotato_sim.runtime.experiment.contracts import PluginSelection, VersionedIdentity
from xpotato_sim.runtime.composition.robot_bundle import (
    CapabilityProviderBinding,
    ENDPOINT_COMMAND_V1,
    ENDPOINT_POSE_V1,
    QPOS_FEASIBILITY_V1,
    RESET_INITIAL_STATE_V1,
    SCENE_ROLE_BINDING_V1,
)


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "robot_plugins"


class _RecordingPublisher:
    def __init__(self) -> None:
        self.states = []

    async def publish(self, state) -> None:  # noqa: ANN001
        self.states.append(state)


def _clear_fixture_modules() -> None:
    for name in tuple(sys.modules):
        if name == "test_robot_plugins" or name.startswith("test_robot_plugins."):
            sys.modules.pop(name)


def _clear_broken_modules() -> None:
    for name in tuple(sys.modules):
        if name == "broken_plugins" or name.startswith("broken_plugins."):
            sys.modules.pop(name)


def _discover_fixture(monkeypatch: pytest.MonkeyPatch):
    _clear_fixture_modules()
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    namespace = importlib.import_module("test_robot_plugins")
    return discover_robot_plugins(
        RobotDiscoveryRoot(
            namespace=namespace,
            repository_root=FIXTURE_ROOT,
            asset_roots=(FIXTURE_ROOT / "assets" / "mujoco",),
            configuration_roots=(FIXTURE_ROOT / "configs",),
        )
    )


def test_second_robot_discovery_resolution_resources_and_headless_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _discover_fixture(monkeypatch)
    registration = registry.resolve("fixture_bot", robot_logical_version=2)
    bundle = registration.bundle

    assert registry.ids == ("fixture_bot",)
    assert bundle.profile.profile_id == "fixture_bot"
    assert bundle.runtime_plugin.profile is bundle.profile
    assert registration.viewer is bundle.profile.viewer_declaration
    assert json.loads(json.dumps(registration.viewer.to_document())) == (
        registration.viewer.to_document()
    )
    for capability in (
        RESET_INITIAL_STATE_V1,
        ENDPOINT_POSE_V1,
        ENDPOINT_COMMAND_V1,
        QPOS_FEASIBILITY_V1,
        SCENE_ROLE_BINDING_V1,
    ):
        provider = bundle.provider(capability)
        assert provider.assembly_binding.robot_identity == VersionedIdentity(
            "fixture_bot", 2
        )
        assert (
            provider.assembly_binding.owner is bundle.profile
            or provider.assembly_binding.owner is bundle.runtime_plugin
        )

    simulator = HeadlessMuJoCoSimulator.from_model_path(
        bundle.profile.mujoco_model_asset,
        initial_keyframe_name=bundle.profile.initial_keyframe_name,
    )
    bundle.runtime_plugin.validate_model(simulator.model)
    simulator.reset()
    assert simulator.snapshot().qpos == pytest.approx((0.25,), abs=1e-12)
    simulator.step(0.01)
    assert simulator.snapshot().frame_index == 1


def test_logical_v2_selection_reaches_public_catalog_and_runtime_composition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN as FAST_ARM

    fixture_v2 = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )
    catalog = RobotCatalog(RobotPluginRegistry((FAST_ARM, fixture_v2)))
    config = RuntimeConfig(robot_profile_id="fixture_bot", robot_logical_version=2)
    selection = config.robot_selection
    assert selection == PluginSelection("fixture_bot", 2)

    assert catalog.resolve_registration(selection) is fixture_v2
    assert catalog.resolve_bundle(selection) is fixture_v2.bundle
    assert catalog.resolve_profile(selection) is fixture_v2.bundle.profile
    assert catalog.resolve_runtime_plugin(selection) is fixture_v2.bundle.runtime_plugin
    resolved_runtime = catalog.resolve_runtime(selection)
    assert resolved_runtime.profile is fixture_v2.bundle.profile
    assert resolved_runtime.plugin is fixture_v2.bundle.runtime_plugin

    with pytest.raises(ValueError, match="logical version mismatch"):
        catalog.resolve_registration(PluginSelection("fixture_bot", 1))
    with pytest.raises(ValueError, match="logical version mismatch"):
        catalog.resolve_bundle(PluginSelection("fixture_bot", 1))

    publisher = _RecordingPublisher()
    pipeline = build_concrete_mujoco_pipeline(
        config=config,
        publisher=publisher,
        robot_catalog=catalog,
    )
    assert pipeline.config.robot_selection == selection
    assert pipeline.robot_profile_metadata["robot_profile_id"] == "fixture_bot"
    state = asyncio.run(pipeline.run_once())
    assert state.frame_index == 1
    assert len(publisher.states) == 1


@pytest.mark.parametrize(
    "capability",
    (
        RESET_INITIAL_STATE_V1,
        ENDPOINT_POSE_V1,
        ENDPOINT_COMMAND_V1,
        QPOS_FEASIBILITY_V1,
        SCENE_ROLE_BINDING_V1,
    ),
)
def test_bundle_rejects_provider_bound_to_noncanonical_owner(
    monkeypatch: pytest.MonkeyPatch,
    capability: VersionedIdentity,
) -> None:
    bundle = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    ).bundle
    original = next(
        binding for binding in bundle.capability_providers if binding.identity == capability
    )
    if hasattr(original.provider, "profile"):
        stale_provider = replace(
            original.provider,
            profile=replace(bundle.profile),
            robot_identity=bundle.identity,
        )
    else:
        stale_provider = replace(
            original.provider,
            plugin=replace(bundle.runtime_plugin),
            robot_identity=bundle.identity,
        )
    bindings = tuple(
        CapabilityProviderBinding(binding.identity, stale_provider)
        if binding.identity == capability
        else binding
        for binding in bundle.capability_providers
    )
    with pytest.raises(ValueError, match="not bound to the canonical Profile or Runtime Plugin"):
        replace(bundle, capability_providers=bindings)


@pytest.mark.parametrize(
    "capability",
    (
        RESET_INITIAL_STATE_V1,
        ENDPOINT_POSE_V1,
        ENDPOINT_COMMAND_V1,
        QPOS_FEASIBILITY_V1,
        SCENE_ROLE_BINDING_V1,
    ),
)
def test_bundle_rejects_provider_bound_to_another_logical_version(
    monkeypatch: pytest.MonkeyPatch,
    capability: VersionedIdentity,
) -> None:
    bundle = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    ).bundle
    original = next(
        binding for binding in bundle.capability_providers if binding.identity == capability
    )
    stale_provider = replace(
        original.provider,
        robot_identity=VersionedIdentity("fixture_bot", 1),
    )
    bindings = tuple(
        CapabilityProviderBinding(binding.identity, stale_provider)
        if binding.identity == capability
        else binding
        for binding in bundle.capability_providers
    )
    with pytest.raises(ValueError, match="logical identity mismatch"):
        replace(bundle, capability_providers=bindings)


def test_removed_duplicate_and_unknown_fixture_registration_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )

    with pytest.raises(ValueError, match="unknown Robot Plugin ID"):
        RobotPluginRegistry(()).resolve("fixture_bot")
    with pytest.raises(ValueError, match="duplicate Robot Plugin registration"):
        RobotPluginRegistry((registration, registration))


def _temporary_discovery_root(
    tmp_path: Path,
    *,
    monkeypatch: pytest.MonkeyPatch,
    package_name: str,
    plugin_source: str | None,
) -> RobotDiscoveryRoot:
    _clear_broken_modules()
    namespace_root = tmp_path / "broken_plugins"
    package_root = namespace_root / package_name
    package_root.mkdir(parents=True)
    (namespace_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    if plugin_source is not None:
        (package_root / "plugin.py").write_text(plugin_source, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "configs").mkdir()
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    namespace = importlib.import_module("broken_plugins")
    return RobotDiscoveryRoot(
        namespace=namespace,
        repository_root=tmp_path,
        asset_roots=(tmp_path / "assets",),
        configuration_roots=(tmp_path / "configs",),
    )


@pytest.mark.parametrize(
    ("plugin_source", "message"),
    (
        (None, "entry point is missing"),
        ("raise RuntimeError('broken import')\n", "import failed"),
        ("VALUE = 1\n", "export is missing"),
        ("ROBOT_PLUGIN = object()\n", "invalid Robot Plugin registration type"),
    ),
)
def test_broken_entry_points_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    plugin_source: str | None,
    message: str,
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    root = _temporary_discovery_root(
        tmp_path,
        monkeypatch=monkeypatch,
        package_name="broken_bot",
        plugin_source=plugin_source,
    )
    with pytest.raises(RobotPluginDiscoveryError, match=message):
        discover_robot_plugins(root)
    _clear_broken_modules()


def test_package_identity_missing_resource_and_path_escape_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(FIXTURE_ROOT))
    mismatch = _temporary_discovery_root(
        tmp_path / "mismatch",
        monkeypatch=monkeypatch,
        package_name="wrong_name",
        plugin_source=(
            "from test_robot_plugins.fixture_bot.plugin import ROBOT_PLUGIN\n"
        ),
    )
    with pytest.raises(RobotPluginDiscoveryError, match="package/declaration identity mismatch"):
        discover_robot_plugins(mismatch)
    _clear_broken_modules()

    missing = _temporary_discovery_root(
        tmp_path / "missing",
        monkeypatch=monkeypatch,
        package_name="fixture_bot",
        plugin_source=(
            "from dataclasses import replace\n"
            "from test_robot_plugins.fixture_bot.plugin import ROBOT_PLUGIN as BASE\n"
            "from xpotato_sim.plugins.robots.registration import RepositoryResource, RobotResourceDeclaration\n"
            "ROBOT_PLUGIN = replace(BASE, resources=RobotResourceDeclaration("
            "model=RepositoryResource('assets/mujoco/fixture_bot/missing.xml'), "
            "configurations=BASE.resources.configurations, "
            "viewer_declaration=BASE.resources.viewer_declaration, "
            "viewer_fixture=BASE.resources.viewer_fixture, "
            "viewer_vfs_resources=()))\n"
        ),
    )
    with pytest.raises(RobotPluginDiscoveryError, match="missing robot resource"):
        discover_robot_plugins(missing)

    with pytest.raises(ValueError, match="must not be absolute or escape"):
        RepositoryResource("../outside.xml")


class _CanonicalEntry:
    def __init__(self, name: str) -> None:
        self.identity = VersionedIdentity(name, 1)

    def canonical_identity_bytes(self) -> bytes:
        return json.dumps({"identity": self.identity.name}).encode("utf-8")


def test_registry_identity_material_is_order_independent_and_path_free() -> None:
    first = _CanonicalEntry("alpha")
    second = _CanonicalEntry("beta")
    forward = RobotPluginRegistry((first, second))  # type: ignore[arg-type]
    reverse = RobotPluginRegistry((second, first))  # type: ignore[arg-type]

    assert forward.ids == reverse.ids == ("alpha", "beta")
    assert forward.canonical_identity_bytes() == reverse.canonical_identity_bytes()
    assert b"module" not in forward.canonical_identity_bytes()
    assert b"package" not in forward.canonical_identity_bytes()
    assert b"class" not in forward.canonical_identity_bytes()


def test_registration_rejects_viewer_and_resource_contract_mismatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )
    mismatched_viewer = replace(registration.viewer, profile_id="other")
    with pytest.raises(ValueError, match="registration/viewer declaration identity mismatch"):
        replace(registration, viewer=mismatched_viewer)
    with pytest.raises(ValueError, match="unsupported Robot Plugin onboarding schema version"):
        replace(registration, onboarding_contract_version=2)
    with pytest.raises(ValueError, match="registration/Robot Bundle identity mismatch"):
        replace(registration, identity=VersionedIdentity("fixture_bot", 3))
    with pytest.raises(
        ValueError,
        match="Robot Plugin logical version/viewer profile contract version mismatch",
    ):
        replace(
            registration,
            viewer=replace(registration.viewer, profile_contract_version=3),
        )
    with pytest.raises(
        ValueError, match="robot profile/plugin profile contract version mismatch"
    ):
        replace(
            registration.bundle,
            profile=replace(
                registration.bundle.profile,
                profile_contract_version=3,
            ),
        )

    incomplete_bundle = replace(
        registration.bundle,
        capability_providers=registration.bundle.capability_providers[:-1],
    )
    with pytest.raises(ValueError, match="missing required capabilities"):
        replace(registration, bundle=incomplete_bundle)

    mismatched_resources = RobotResourceDeclaration(
        model=registration.resources.model,
        configurations=registration.resources.configurations,
        viewer_declaration=registration.resources.viewer_declaration,
        viewer_fixture=registration.resources.viewer_fixture,
        viewer_vfs_resources=(
            RepositoryResource("assets/mujoco/fixture_bot/other.bin"),
        ),
    )
    with pytest.raises(ValueError, match="viewer VFS/resource declaration mismatch"):
        replace(registration, resources=mismatched_resources).validate_resources(
            FIXTURE_ROOT,
            asset_roots=(FIXTURE_ROOT / "assets" / "mujoco",),
            configuration_roots=(FIXTURE_ROOT / "configs",),
        )


def test_onboarding_schema_version_is_independent_from_robot_logical_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN as FAST_ARM

    registration_v2 = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )

    registry = RobotPluginRegistry((FAST_ARM, registration_v2))
    assert registry.resolve("fast_arm").identity == VersionedIdentity("fast_arm", 1)
    assert registry.resolve("fixture_bot", robot_logical_version=2) is registration_v2
    assert registration_v2.onboarding_contract_version == 1
    assert registration_v2.identity.version == 2
    with pytest.raises(ValueError, match="logical version mismatch"):
        registry.resolve("fixture_bot")
    with pytest.raises(ValueError, match="unsupported Robot Plugin onboarding schema version"):
        replace(registration_v2, onboarding_contract_version=2)


@pytest.mark.parametrize(
    ("resource_field", "sibling_path", "message"),
    (
        ("model", "assets/mujoco/sibling_bot/model.xml", "asset resource is not owned"),
        (
            "configuration",
            "configs/sibling_bot/limits.toml",
            "configuration resource is not owned",
        ),
    ),
)
def test_robot_resource_ownership_rejects_sibling_directories(
    monkeypatch: pytest.MonkeyPatch,
    resource_field: str,
    sibling_path: str,
    message: str,
) -> None:
    registration = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )
    if resource_field == "model":
        resources = replace(
            registration.resources,
            model=RepositoryResource(sibling_path),
        )
    else:
        resources = replace(
            registration.resources,
            configurations=(RepositoryResource(sibling_path),),
        )
    with pytest.raises(ValueError, match=message):
        replace(registration, resources=resources).validate_resources(
            FIXTURE_ROOT,
            asset_roots=(FIXTURE_ROOT / "assets" / "mujoco",),
            configuration_roots=(FIXTURE_ROOT / "configs",),
        )


def test_robot_resource_symlink_escape_is_rejected(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    owned_root = repository_root / "assets" / "mujoco" / "fixture_bot"
    owned_root.mkdir(parents=True)
    outside = tmp_path / "outside.xml"
    outside.write_text("<mujoco/>", encoding="utf-8")
    link = owned_root / "linked.xml"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match="escapes allowed repository resource roots"):
        _resolved_resource(
            repository_root,
            RepositoryResource("assets/mujoco/fixture_bot/linked.xml"),
            allowed_roots=(repository_root / "assets" / "mujoco",),
        )


@pytest.mark.parametrize(
    ("resource_kind", "repository_path", "root_parts"),
    (
        (
            "asset",
            "assets/mujoco/fixture_bot/linked.xml",
            ("assets", "mujoco"),
        ),
        (
            "configuration",
            "configs/fixture_bot/linked.toml",
            ("configs",),
        ),
    ),
)
def test_robot_resource_symlink_to_sibling_robot_is_rejected(
    tmp_path: Path,
    resource_kind: str,
    repository_path: str,
    root_parts: tuple[str, ...],
) -> None:
    repository_root = tmp_path / "repository"
    shared_root = repository_root.joinpath(*root_parts)
    owned_root = shared_root / "fixture_bot"
    sibling_root = shared_root / "sibling_bot"
    owned_root.mkdir(parents=True)
    sibling_root.mkdir(parents=True)
    suffix = ".xml" if resource_kind == "asset" else ".toml"
    sibling = sibling_root / f"resource{suffix}"
    sibling.write_text("<mujoco/>" if suffix == ".xml" else "value = 1", encoding="utf-8")
    link = repository_root / repository_path
    try:
        link.symlink_to(sibling)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    with pytest.raises(ValueError, match=f"resolved {resource_kind} resource is not owned"):
        _resolved_resource(
            repository_root,
            RepositoryResource(repository_path),
            allowed_roots=(shared_root,),
            ownership_root=owned_root,
            ownership_label=resource_kind,
        )


def test_viewer_owned_url_mapping_cannot_hide_resolved_model_ownership_violation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )
    repository_root = tmp_path / "repository"
    asset_root = repository_root / "assets" / "mujoco"
    owned_root = asset_root / "fixture_bot"
    sibling_root = asset_root / "sibling_bot"
    owned_root.mkdir(parents=True)
    sibling_root.mkdir(parents=True)
    sibling_model = sibling_root / "model.xml"
    sibling_model.write_text("<mujoco/>", encoding="utf-8")
    try:
        (owned_root / "model.xml").symlink_to(sibling_model)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    assert registration.viewer.model_url == "/mujoco/fixture_bot/model.xml"
    with pytest.raises(ValueError, match="resolved asset resource is not owned"):
        registration.validate_resources(
            repository_root,
            asset_roots=(asset_root,),
            configuration_roots=(repository_root / "configs",),
        )


@pytest.mark.parametrize(
    ("resource_path", "resource_kind"),
    (
        ("assets/mujoco/fixture_bot/model.xml", "asset"),
        ("assets/mujoco/fixture_bot/viewer-profile.json", "asset"),
        ("assets/mujoco/fixture_bot/fixture.json", "asset"),
        ("assets/mujoco/fixture_bot/robot.xml", "asset"),
        ("configs/fixture_bot/limits.toml", "configuration"),
    ),
)
def test_discovery_rejects_resolved_sibling_ownership_for_every_resource_kind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resource_path: str,
    resource_kind: str,
) -> None:
    copied_root = tmp_path / "robot_plugins"
    shutil.copytree(
        FIXTURE_ROOT,
        copied_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    owned_file = copied_root / resource_path
    if resource_kind == "configuration":
        sibling_file = copied_root / "configs" / "sibling_bot" / owned_file.name
    else:
        sibling_file = (
            copied_root / "assets" / "mujoco" / "sibling_bot" / owned_file.name
        )
    sibling_file.parent.mkdir(parents=True)
    sibling_file.write_bytes(owned_file.read_bytes())
    owned_file.unlink()
    try:
        owned_file.symlink_to(sibling_file)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    _clear_fixture_modules()
    monkeypatch.syspath_prepend(str(copied_root))
    importlib.invalidate_caches()
    namespace = importlib.import_module("test_robot_plugins")
    with pytest.raises(
        RobotPluginDiscoveryError,
        match=f"resolved {resource_kind} resource is not owned",
    ):
        discover_robot_plugins(
            RobotDiscoveryRoot(
                namespace=namespace,
                repository_root=copied_root,
                asset_roots=(copied_root / "assets" / "mujoco",),
                configuration_roots=(copied_root / "configs",),
            )
        )


def test_viewer_resource_path_and_public_url_must_identify_the_same_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registration = _discover_fixture(monkeypatch).resolve(
        "fixture_bot", robot_logical_version=2
    )
    with pytest.raises(ValueError, match="model resource path/URL mismatch"):
        replace(registration.viewer, model_url="/mujoco/fixture_bot/other.xml")
    with pytest.raises(ValueError, match="fixture resource path/URL mismatch"):
        replace(registration.viewer, fixture_url="/mujoco/fixture_bot/missing.json")
    from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN as FAST_ARM

    with pytest.raises(ValueError, match="VFS resource path/URL mismatch"):
        replace(
            FAST_ARM.viewer.vfs_assets[0],
            resource_path="assets/mujoco/fast_arm/other.xml",
        )

    missing_fixture_resources = replace(
        registration.resources,
        viewer_fixture=RepositoryResource(
            "assets/mujoco/fixture_bot/missing.json"
        ),
    )
    with pytest.raises(ValueError, match="missing robot resource"):
        replace(registration, resources=missing_fixture_resources).validate_resources(
            FIXTURE_ROOT,
            asset_roots=(FIXTURE_ROOT / "assets" / "mujoco",),
            configuration_roots=(FIXTURE_ROOT / "configs",),
        )


def test_viewer_vfs_validation_rejects_missing_required_model_asset() -> None:
    from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN
    from xpotato_sim.runtime.composition.robot_resource import read_package_resource_bytes

    incomplete_viewer = replace(
        ROBOT_PLUGIN.viewer,
        vfs_assets=ROBOT_PLUGIN.viewer.vfs_assets[:-1],
    )
    resolved_resources = tuple(
        read_package_resource_bytes(item)
        for item in ROBOT_PLUGIN.resources.viewer_vfs_resources[:-1]
    )

    with pytest.raises(ValueError, match="viewer VFS mapping is missing required mesh asset"):
        _validate_viewer_vfs_coverage(
            read_package_resource_bytes(ROBOT_PLUGIN.resources.model.entrypoint),
            incomplete_viewer,
            resolved_resources,
        )


def test_production_registration_identity_material_excludes_python_location() -> None:
    from xpotato_sim.plugins.robots.fast_arm.plugin import ROBOT_PLUGIN

    material = ROBOT_PLUGIN.canonical_identity_bytes()
    assert b"xpotato_sim.plugins.robots.fast_arm" not in material
    assert ROBOT_PLUGIN.bundle.runtime_plugin.__class__.__module__.encode() not in material
    assert ROBOT_PLUGIN.bundle.runtime_plugin.__class__.__name__.encode() not in material
