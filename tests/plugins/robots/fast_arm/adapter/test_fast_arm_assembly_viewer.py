"""FastArm assemblyからViewer declaration/resourceを一意に生成する。"""
from __future__ import annotations

import json
from math import cos, pi, sin

import mujoco
import pytest

from fast_arm_core.assembly import FastArmAssembly, FastArmInstance
from fast_arm_core.models import resolve_fast_arm_model
from xpotato_sim.plugins.robots.fast_arm.adapter.assembly_viewer import (
    FAST_ARM_ASSEMBLY_MODEL_CONTRACT_VERSION,
    build_fast_arm_assembly_viewer_bundle,
)
from xpotato_sim.runtime.composition.viewer_robot_declaration import (
    decode_viewer_robot_declaration,
)


def assembly():
    return resolve_fast_arm_model("bimanual").assembly


def test_bimanual_bundle_uses_assembly_joint_order_and_loadable_model():
    value = assembly()
    bundle = build_fast_arm_assembly_viewer_bundle(value)
    declaration = bundle.declaration
    assert declaration.profile_id == "fast_arm_assembly"
    assert declaration.model_contract_version == FAST_ARM_ASSEMBLY_MODEL_CONTRACT_VERSION
    assert declaration.joint_names == value.joint_names
    assert declaration.qpos_dimension == 8

    model = mujoco.MjModel.from_xml_string(
        bundle.built.xml.decode("utf-8"), dict(bundle.built.assets)
    )
    assert model.nq == 8
    assert tuple(
        mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_JOINT, joint_id)
        for joint_id in range(model.njnt)
    ) == value.joint_names

    decoded = decode_viewer_robot_declaration(
        json.loads(bundle.resources[bundle.declaration_resource_path].decode("utf-8"))
    )
    assert decoded == declaration
    fixture_path = declaration.fixture_resource_path
    fixture = json.loads(bundle.resources[fixture_path].decode("utf-8"))
    assert fixture["qpos_length"] == 8
    assert len(fixture["frames"][0]["qpos"]) == 8


def test_resource_tree_is_model_digest_scoped_and_round_trips(tmp_path):
    bundle = build_fast_arm_assembly_viewer_bundle(assembly())
    prefix = f"assets/mujoco/fast_arm_assembly/{bundle.built.model_sha256}/"
    assert bundle.resources
    assert all(path.startswith(prefix) for path in bundle.resources)
    written = bundle.write_public_tree(tmp_path)
    assert len(written) == len(bundle.resources)
    for logical, payload in bundle.resources.items():
        public_relative = logical.removeprefix("assets/")
        assert (tmp_path / public_relative).read_bytes() == payload


def test_bundle_requires_exactly_two_arms_and_empty_resource_root(tmp_path):
    single = FastArmAssembly((assembly().instances[0],))
    assert build_fast_arm_assembly_viewer_bundle(single).declaration.qpos_dimension == 4

    bundle = build_fast_arm_assembly_viewer_bundle(assembly())
    (tmp_path / "existing.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(ValueError, match="must be empty"):
        bundle.write_public_tree(tmp_path)
