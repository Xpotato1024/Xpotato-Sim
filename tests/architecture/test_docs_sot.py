from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def front_matter(text: str) -> str:
    assert text.startswith("---\n"), "missing opening front matter delimiter"
    end = text.find("\n---\n", 4)
    assert end != -1, "missing closing front matter delimiter"
    return text[4:end]


def status_value(front: str) -> str:
    for line in front.splitlines():
        if line.startswith("status:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("front matter missing status")


def source_map_targets() -> tuple[str, ...]:
    targets = []
    for line in read("docs/README.md").splitlines():
        if not line.startswith("|") or line.startswith("|---") or "| Topic |" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[1].startswith("`"):
            continue
        targets.append(cells[1].strip("`"))
    return tuple(targets)


def test_docs_sot_required_paths_exist() -> None:
    required = [
        "docs/README.md",
        "docs/conventions.md",
        "docs/architecture/code-documentation-policy.md",
        "docs/architecture/documentation-sot-policy.md",
        "docs/architecture/dependency-boundaries.md",
    ]

    for path in required:
        assert (ROOT / path).is_file(), path


def test_doc_directory_is_not_used() -> None:
    assert not (ROOT / "doc").exists()


def test_canonical_docs_have_valid_front_matter() -> None:
    for path in ("docs/README.md", *source_map_targets()):
        front = front_matter(read(path))
        assert status_value(front) == "canonical", path


def test_docs_readme_has_source_of_truth_map() -> None:
    text = read("docs/README.md")
    assert "Source of Truth Map" in text


def test_code_documentation_policy_is_canonical_and_registered() -> None:
    index = read("docs/README.md")
    policy = read("docs/architecture/code-documentation-policy.md")

    assert "`docs/architecture/code-documentation-policy.md`" in index
    for marker in (
        "code and plugin documentation policy",
        "contract significance",
        "TODO(#123)",
        "commented-out code",
        "suppression",
        "plugin root README",
        "axis README",
        "concrete production plugin README",
        "src/xpotato_sim/plugins/README.md",
        "command semantics route",
        "該当するREADME",
        "repository-wide policyとsubproject固有policyの境界",
        "全private helper",
        "symbol count",
        "coverage guard",
        "false-positive risk",
    ):
        assert marker in policy

    policy_without_tracking_example = policy.replace("TODO(#123)", "")
    assert re.search(r"#\d+", policy_without_tracking_example) is None


def test_concrete_plugin_readme_template_is_axis_neutral() -> None:
    policy = read("docs/architecture/code-documentation-policy.md")
    template = read("docs/operations/plugin-readme-templates.md")
    concrete = template.split("## concrete plugin README", 1)[1]
    required, optional = concrete.split("optional section:", 1)

    assert "src/xpotato_sim/plugins/<axis>/<plugin>/README.md" in policy
    assert "src/xpotato_sim/plugins/<axis>/<plugin>/README.md" in template
    assert "root直下" in policy
    assert "## compatibilityとcomposition" in required
    assert "command semantics" not in required
    assert "command semantics route" in optional
    assert "Environment、Task、Evaluation等へ一律に要求しない" in optional


def test_current_contracts_are_registered_and_evidence_is_not_in_sot_map() -> None:
    index = read("docs/README.md")
    operations = read("docs/operations/README.md")
    reports = read("docs/reports/README.md")
    archive = read("docs/archive/README.md")

    assert "`docs/contracts/analog-fixture-mapping.md`" in index
    assert "| Topic | Canonical document | Notes |" in index
    assert "docs/contracts/kinematics-command-contract.md" in index
    assert "`research/README.md`" in index
    assert "r7-c-manual-validation-preflight.md" not in operations
    assert "r7-c-manual-validation-preflight.md" in reports
    assert "native-mujoco-fast-arm-viewer-check.md" not in operations
    assert "native-mujoco-fast-arm-viewer-check.md" in archive
    assert "r7-c-viewer-fixture-demo-procedure.md" in operations
    assert "r7-c-keyboard-replay-demo-package.md" in operations
    assert "r7-c-live-selfrionette-validation-log.md" in operations
    assert "r7-c-axis-sanity-check.md" in operations
    assert "docs/reports/implementation/r7-c-presentation-demo-notes.md" not in index
    assert "docs/reports/audits/r7-c-completion-audit.md" not in index
    assert (ROOT / "docs/reports/implementation/r7-c-manual-validation-preflight.md").is_file()
    assert (ROOT / "docs/archive/operations/native-mujoco-fast-arm-viewer-check.md").is_file()


def test_runtime_composition_documents_current_responsibility_split() -> None:
    text = read("docs/architecture/runtime-composition.md")
    stages = (
        "source planning",
        "source lifecycle",
        "control-frame resolution",
        "motion policy",
        "backend update",
        "MuJoCo measurement",
        "diagnostic annotation",
        "publication",
        "target lifecycle",
        "experiment record construction",
    )

    for stage in stages:
        assert f"| {stage} |" in text

    assert "MuJoCo remains the physical source of truth" in text
    assert "only composition root" in text
    assert "render-only" in text
    assert "publish-before-ViewerInputSource-rebase" in text
    assert "`experiment/world_tool_runner.py`" in text
    assert "manifest initial tipをmeasurementへ変換せず" in text
    assert "diagnostic / operational runtime" in text
    assert "planned #486" in text


def test_robot_profile_contract_is_canonical_and_registered() -> None:
    index = read("docs/README.md")
    text = read("docs/contracts/robot-profile-runtime-viewer-profile.md")

    assert "`docs/contracts/robot-profile-runtime-viewer-profile.md`" in index
    assert "RobotRuntimePlugin" in text
    assert "ViewerRobotProfile" in text
    assert "arbitrary dynamic" in text
    assert "payload-v0" in text
    assert "rendering declaration" in text
    assert "ProviderAssemblyBinding" in text
    assert "RuntimeConfig.robot_selection" in text
    assert "symlink解決後の実path" in text


def test_experiment_plugin_composition_contract_is_canonical_and_registered() -> None:
    index = read("docs/README.md")
    text = read("docs/contracts/experiment-plugin-composition.md")

    assert "`docs/contracts/experiment-plugin-composition.md`" in index
    for marker in (
        "Robot Bundle",
        "Environment / Scene",
        "Control / Mapping",
        "Task",
        "Evaluation",
        "Input Source",
        "contact_evidence/v1",
        "PluginParameterOwner",
        "EvidenceProducerBinding",
        "SemanticRoleRequirement",
        "exact `VersionedIdentity`",
        "ambiguous producer",
        "requested",
        "resolved",
        "predicted",
        "measured",
        "unavailable",
        "invalid",
        "compose_experiment()",
        "#405",
        "#411",
    ):
        assert marker in text


def test_plugin_infrastructure_and_shared_owners_match_current_tree() -> None:
    dependency = read("docs/architecture/dependency-boundaries.md")
    composition = read("docs/contracts/experiment-plugin-composition.md")
    registry = read("docs/contracts/runtime-input-source-registry.md")

    for marker in (
        "`plugins/mappings/_continuous_endpoint_velocity.py`",
        "`plugins/mappings/_command_routes.py`",
        "algorithm primitive",
        "declaration / route factory",
    ):
        assert marker in dependency
        assert marker in composition

    assert "production 6 source" in registry
    assert "root\n`plugins/input_source_registration.py`" in registry
    assert "plugins/input_sources/registration.py`はtyped registration contract" in registry
    assert "source registrationはMapping object、default selection、" in composition
    assert "production runnerは`runtime/experiment/world_tool_runner.py`" in composition
    assert "TaskTransitionのclassification / evidenceを変更しない" in composition
    assert "#486" in composition


def test_evaluation_manifest_readiness_contract_is_canonical_and_registered() -> None:
    index = read("docs/README.md")
    text = read("docs/contracts/evaluation-manifest-readiness.md")

    assert "`docs/contracts/evaluation-manifest-readiness.md`" in index
    for marker in (
        "EvaluationManifest",
        "evaluation-manifest/v3",
        "canonical JSON",
        "sha256",
        "FreezeRecord",
        "requested identity",
        "resolved identity",
        "world / tool condition-pair",
        "condition_specific",
        "software-only",
        "#423",
        "#406",
        "partial success",
    ):
        assert marker in text


def test_p22_neutral_pose_contract_is_registered_and_freezes_selection_order() -> None:
    index = read("docs/README.md")
    evidence = read("docs/reports/implementation/r7-e-p22-neutral-initial-pose.md")
    contract = read("docs/contracts/robot-profile-runtime-viewer-profile.md")

    assert "`docs/contracts/robot-profile-runtime-viewer-profile.md`" in index
    assert "`docs/reports/implementation/r7-e-p22-neutral-initial-pose.md`" not in index
    assert "(0, -0.5235987755982989, 0, -1.0471975511965976)" in contract
    assert "tip = (0.240000, -0.245951, 0.284308) m" in contract
    assert "collision-freeの物理証拠とは扱わない" in contract
    assert "Selection contract fixed before evaluation" in evidence
    assert "rank 3は必須にしない" in evidence
    assert "MuJoCo native `home` keyframe" in evidence
    assert "candidate count: `82`" in evidence
    assert "#339 / P6: implementation evidence complete; manual viewer smoke required" in evidence
    assert (
        "#341 / P7: local-policy evidence complete with documented workspace limitation; "
        "manual viewer smoke required"
    ) in evidence
    assert "collision_check_available=false" in evidence
