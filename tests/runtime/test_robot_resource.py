from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

from xpotato_sim.runtime.composition.robot_resource import (
    PackageResource,
    package_resource_traversable,
)


@pytest.mark.parametrize(
    "field,value",
    (
        ("resource_path", "../escape.xml"),
        ("resource_path", "/absolute.xml"),
        ("logical_identifier", "assets/../escape.xml"),
        ("bundle_path", "../arm.xml"),
    ),
)
def test_package_resource_rejects_traversal(field: str, value: str) -> None:
    arguments: dict[str, str] = {
        "package": "fast_arm_core",
        "resource_path": "resources/model/arm.xml",
        "logical_identifier": "assets/mujoco/fast_arm/arm.xml",
        "bundle_path": "arm.xml",
    }
    arguments[field] = value
    with pytest.raises(ValueError):
        PackageResource(**arguments)


def test_package_resource_fails_for_missing_package_and_resource() -> None:
    with pytest.raises(ValueError, match="missing package resource owner"):
        package_resource_traversable(
            PackageResource(
                "missing_fast_arm_package",
                "resource.xml",
                "assets/mujoco/fast_arm/resource.xml",
            )
        )
    with pytest.raises(ValueError, match="missing package resource"):
        package_resource_traversable(
            PackageResource(
                "fast_arm_core",
                "resources/model/missing.xml",
                "assets/mujoco/fast_arm/missing.xml",
            )
        )


def test_package_resource_rejects_resolved_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package_root = tmp_path / "fast_arm_resource_owner"
    package_root.mkdir()
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    outside = tmp_path / "outside.xml"
    outside.write_text("<mujoco/>", encoding="utf-8")
    link = package_root / "escaped.xml"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    sys.modules.pop("fast_arm_resource_owner", None)
    importlib.import_module("fast_arm_resource_owner")
    with pytest.raises(ValueError, match="escapes its owning package"):
        package_resource_traversable(
            PackageResource(
                "fast_arm_resource_owner",
                "escaped.xml",
                "assets/mujoco/fast_arm/escaped.xml",
            )
        )
