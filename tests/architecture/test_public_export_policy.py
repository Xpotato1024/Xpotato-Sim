from __future__ import annotations

import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOTS = (
    "xpotato_sim.kinematics",
    "xpotato_sim.motion",
    "xpotato_sim.mujoco_backend",
    "xpotato_sim.transport",
    "xpotato_sim.runtime",
)

FORBIDDEN_PREFIXES = ("NoOp", "Zero", "Static")

STUB_EXPORTS = {
    "tests.support.input_source_doubles": ("StaticInputSource",),
    "tests.support.kinematics_solver_doubles": ("ZeroForwardKinematicsSolver", "ZeroInverseKinematicsSolver"),
    "tests.support.motion_doubles": ("NoOpMotionGenerator",),
    "tests.support.mujoco_doubles": ("NoOpMuJoCoSimulator",),
    "tests.support.transport_doubles": ("NoOpStatePublisher",),
}

DOC_PATH = ROOT / "docs" / "reports" / "implementation" / "r6-i-p2-public-export-policy.md"
DOCS_README_PATH = ROOT / "docs" / "README.md"
BOUNDARY_DOC_PATH = ROOT / "docs" / "architecture" / "dependency-boundaries.md"
R6_I_P3_DOC_PATH = ROOT / "docs" / "reports" / "implementation" / "r6-i-p3-stub-reclassification.md"


def test_package_root_all_excludes_stub_exports() -> None:
    for module_name in PACKAGE_ROOTS:
        module = importlib.import_module(module_name)
        exported = tuple(getattr(module, "__all__", ()))
        forbidden = [name for name in exported if name.startswith(FORBIDDEN_PREFIXES)]
        assert not forbidden, f"{module_name} exports stub names from package root: {forbidden}"


def test_stub_modules_export_only_stub_classes_in_all() -> None:
    for module_name, expected_exports in STUB_EXPORTS.items():
        module = importlib.import_module(module_name)
        assert set(expected_exports).issubset(module.__all__)


def test_r6_i_p2_docs_record_option_a_policy() -> None:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert "Option A" in text
    assert "contract-reexport" in text


def test_public_export_policy_is_canonical_and_evidence_is_not_in_sot_map() -> None:
    map_text = DOCS_README_PATH.read_text(encoding="utf-8")
    boundary_text = BOUNDARY_DOC_PATH.read_text(encoding="utf-8")
    assert "public export境界" in boundary_text
    assert "test doubleは`tests/support/`だけが所有する" in boundary_text
    assert DOC_PATH.relative_to(ROOT).as_posix() not in map_text
    assert R6_I_P3_DOC_PATH.relative_to(ROOT).as_posix() not in map_text
    assert DOC_PATH.is_file()
    assert R6_I_P3_DOC_PATH.is_file()
