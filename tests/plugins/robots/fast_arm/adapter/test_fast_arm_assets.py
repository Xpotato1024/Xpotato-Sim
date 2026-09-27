from xpotato_sim.plugins.robots.fast_arm.adapter.resources import (
    FAST_ARM_MESH_RESOURCES,
    FAST_ARM_SCENE_RESOURCE,
)
from xpotato_sim.runtime.composition.robot_resource import read_package_resource_bytes


def test_fast_arm_assets_exist() -> None:
    assert read_package_resource_bytes(FAST_ARM_SCENE_RESOURCE)
    assert tuple(resource.bundle_path for resource in FAST_ARM_MESH_RESOURCES) == (
        "meshes/BaseLink.stl",
        "meshes/SholderLink1.stl",
        "meshes/SholderLink2.stl",
        "meshes/UpperArmLink.stl",
        "meshes/ForeArmLink.stl",
    )
    assert all(read_package_resource_bytes(resource) for resource in FAST_ARM_MESH_RESOURCES)


def test_fast_arm_scene_xml_contract() -> None:
    scene_xml = read_package_resource_bytes(FAST_ARM_SCENE_RESOURCE).decode("utf-8")
    assert '<include file="arm.xml"/>' in scene_xml
