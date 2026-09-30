from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "repository" / "validate_agent_skills.py"
SPEC = importlib.util.spec_from_file_location("validate_agent_skills", SCRIPT_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _copy_agents(tmp_path: Path) -> Path:
    target = tmp_path / "repo"
    shutil.copytree(ROOT / ".agents", target / ".agents")
    return target


def _add_fixture_skill(
    target: Path,
    *,
    skill_name: str,
    candidate_key: str,
    status: str,
    score_axes: tuple[int, int, int, int, int],
    implicit: bool,
) -> None:
    skill_dir = target / ".agents" / "skills" / skill_name
    (skill_dir / "agents").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        f"name: {skill_name}\n"
        f"description: {skill_name}のdata-driven lifecycle fixtureを検証する。fixture検証以外には使用しない。\n"
        "---\n\n"
        f"# {skill_name}\n\n"
        "repository-local validatorの一時fixtureである。\n",
        encoding="utf-8",
        newline="\n",
    )
    (skill_dir / "agents" / "openai.yaml").write_text(
        "interface:\n"
        f'  display_name: "{skill_name}"\n'
        '  short_description: "data-driven fixture"\n'
        f'  default_prompt: "Use ${skill_name} for a fixture."\n\n'
        "policy:\n"
        f"  allow_implicit_invocation: {str(implicit).lower()}\n",
        encoding="utf-8",
        newline="\n",
    )
    invocation_policy = "implicit-after-validation" if implicit else "explicit-only"
    validation_status = "validated" if implicit else "draft"
    (target / ".agents" / "skill-evals" / f"{skill_name}.toml").write_text(
        "schema_version = 1\n"
        f'skill_name = "{skill_name}"\n'
        f'invocation_policy = "{invocation_policy}"\n'
        f'validation_status = "{validation_status}"\n'
        'side_effect_policy = "instruction-only"\n'
        "unresolved_approval = false\n"
        'side_effect_boundary = "fixture内のread-only検証だけ"\n'
        'positive_triggers = ["fixture Skillを検証してください。", "新しいSkillのactive化を検証してください。", "candidateとの対応を検証してください。"]\n'
        'negative_triggers = ["外部mutationを実行してください。", "hardwareを操作してください。"]\n'
        'route_boundaries = ["通常の変更検証はxpotato-sim-change-validationへrouteする。"]\n'
        'required_inputs = ["candidate", "eval", "policy"]\n'
        'expected_major_steps = ["対応確認", "threshold確認"]\n'
        'expected_outputs = ["fixture validation result"]\n'
        'forbidden_actions = ["external mutation", "hardware"]\n'
        'representative_dry_run = "metadata routing fixtureとしてcandidate、eval、policyを照合する。"\n'
        'false_positive_risk = "fixture外へ発火するリスク。"\n'
        'false_negative_risk = "追加Skillを見落とすリスク。"\n'
        'stale_reference_risk = "fixture metadataが古くなるリスク。"\n\n'
        "[[routing_cases]]\n"
        'prompt = "新しいSkillと変更検証が候補になるfixtureを確認してください。"\n'
        f'expected_skills = ["{skill_name}", "xpotato-sim-change-validation"]\n'
        f'primary_skill = "{skill_name}"\n'
        "automatic_chain = false\n"
        "permission_grant = false\n",
        encoding="utf-8",
        newline="\n",
    )
    recurrence, reconstruction, prevention, stability, verifiability = score_axes
    total = sum(score_axes)
    (target / ".agents" / "skill-candidates" / f"{candidate_key}.toml").write_text(
        "schema_version = 1\n"
        f'candidate_key = "{candidate_key}"\n'
        f'status = "{status}"\n'
        'scope = "data-driven validator fixture"\n'
        'summary = "validator scriptへSkill名を固定せず追加できることを確認する。"\n'
        'observable_evidence = ["一時fixtureでcandidate、eval、policyの対応を検証する。"]\n'
        f'realized_by_skills = ["{skill_name}"]\n'
        "related_overlapping_skills = []\n"
        'approval_boundary = "fixture内のinstruction-only検証に限定する。"\n'
        'unresolved_risks = ["実model dispatchは検証しない。"]\n'
        'proposed_action = "update"\n\n'
        "[score]\n"
        f"recurrence = {recurrence}\n"
        f"reconstruction_cost = {reconstruction}\n"
        f"error_prevention = {prevention}\n"
        f"stability = {stability}\n"
        f"verifiability = {verifiability}\n"
        f"total = {total}\n",
        encoding="utf-8",
        newline="\n",
    )


def test_repository_local_skill_governance_is_valid() -> None:
    result = MODULE.validate(ROOT)

    assert result.accepted, result.errors
    assert result.skill_count == 4
    assert result.candidate_count == 5
    assert result.eval_count == 4


def test_candidate_score_total_is_deterministic(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "layer-aware-change-validation.toml"
    text = candidate.read_text(encoding="utf-8")
    candidate.write_text(text.replace("total = 10", "total = 9"), encoding="utf-8", newline="\n")

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("score total does not equal" in error for error in result.errors)


def test_duplicate_candidate_key_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    source = target / ".agents" / "skill-candidates" / "layer-aware-change-validation.toml"
    duplicate = target / ".agents" / "skill-candidates" / "another-file.toml"
    shutil.copy2(source, duplicate)

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("duplicate candidate key" in error for error in result.errors)


def test_explicit_only_eval_rejects_implicit_policy(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-pr-handoff.toml"
    text = eval_path.read_text(encoding="utf-8")
    eval_path.write_text(
        text.replace('invocation_policy = "implicit-after-validation"', 'invocation_policy = "explicit-only"'),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("cannot allow implicit invocation" in error for error in result.errors)


def test_validated_active_implicit_skills_are_accepted() -> None:
    result = MODULE.validate(ROOT)
    active_skills = {
        skill_name
        for path in (ROOT / ".agents" / "skill-candidates").glob("*.toml")
        for candidate in [tomllib.loads(path.read_text(encoding="utf-8"))]
        if candidate["status"] == "active"
        for skill_name in candidate["realized_by_skills"]
    }

    assert result.accepted, result.errors
    assert active_skills
    for skill_name in active_skills:
        eval_path = ROOT / ".agents" / "skill-evals" / f"{skill_name}.toml"
        eval_data = tomllib.loads(eval_path.read_text(encoding="utf-8"))
        policy = ROOT / ".agents" / "skills" / skill_name / "agents" / "openai.yaml"
        assert eval_data["invocation_policy"] == "implicit-after-validation"
        assert eval_data["validation_status"] == "validated"
        assert "allow_implicit_invocation: true" in policy.read_text(encoding="utf-8")


def test_active_candidate_at_or_above_promotion_threshold_is_accepted() -> None:
    config = tomllib.loads((ROOT / ".agents" / "skill-system.toml").read_text(encoding="utf-8"))
    threshold = config["thresholds"]["promotion_review"]
    active_candidates = [
        tomllib.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".agents" / "skill-candidates").glob("*.toml")
        if tomllib.loads(path.read_text(encoding="utf-8"))["status"] == "active"
    ]

    assert active_candidates
    assert all(candidate["score"]["total"] >= threshold for candidate in active_candidates)
    assert MODULE.validate(ROOT).accepted


def test_active_candidate_below_promotion_threshold_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "bounded-plugin-change-audit.toml"
    candidate.write_text(
        candidate.read_text(encoding="utf-8")
        .replace("verifiability = 1", "verifiability = 0")
        .replace("total = 9", "total = 8"),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("below promotion_review threshold" in error for error in result.errors)


def test_draft_candidate_below_promotion_threshold_is_accepted(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    _add_fixture_skill(
        target,
        skill_name="draft-threshold-fixture",
        candidate_key="draft-threshold-candidate",
        status="draft",
        score_axes=(2, 2, 2, 1, 1),
        implicit=False,
    )

    result = MODULE.validate(target)

    assert result.accepted, result.errors


def test_new_validated_active_skill_is_data_driven_without_validator_constants(
    tmp_path: Path,
) -> None:
    target = _copy_agents(tmp_path)
    _add_fixture_skill(
        target,
        skill_name="dynamic-active-fixture",
        candidate_key="dynamic-active-candidate",
        status="active",
        score_axes=(2, 2, 2, 2, 1),
        implicit=True,
    )

    result = MODULE.validate(target)

    assert result.accepted, result.errors
    validator_source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "EXPECTED_IMPLICIT_SKILLS" not in validator_source
    assert "IMPLEMENTED_CANDIDATE_SKILLS" not in validator_source
    assert "UNIMPLEMENTED_CANDIDATES" not in validator_source
    assert "dynamic-active-fixture" not in validator_source
    assert "dynamic-active-candidate" not in validator_source


def test_active_candidate_action_must_be_allowed_by_repository_config(
    tmp_path: Path,
) -> None:
    target = _copy_agents(tmp_path)
    candidate = (
        target
        / ".agents"
        / "skill-candidates"
        / "continuous-skill-lifecycle-review.toml"
    )
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            'proposed_action = "update"', 'proposed_action = "promote"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("action is not allowed by repository config" in error for error in result.errors)


def test_implicit_skill_without_active_candidate_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = (
        target / ".agents" / "skill-candidates" / "pr-handoff-state-audit.toml"
    )
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            'status = "active"', 'status = "draft"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("implicit Skill has no active candidate" in error for error in result.errors)


def test_active_candidate_without_realized_skill_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = (
        target / ".agents" / "skill-candidates" / "pr-handoff-state-audit.toml"
    )
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            'realized_by_skills = ["xpotato-sim-pr-handoff"]',
            "realized_by_skills = []",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("active candidate has no realized Skill" in error for error in result.errors)


def test_active_candidate_cannot_realize_explicit_only_skill(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    policy = (
        target
        / ".agents"
        / "skills"
        / "xpotato-sim-pr-handoff"
        / "agents"
        / "openai.yaml"
    )
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "allow_implicit_invocation: true",
            "allow_implicit_invocation: false",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any(
        "active candidate Skill must allow implicit invocation" in error
        for error in result.errors
    )


def test_skill_lifecycle_review_has_active_candidate_mapping() -> None:
    candidate_path = (
        ROOT
        / ".agents"
        / "skill-candidates"
        / "continuous-skill-lifecycle-review.toml"
    )
    candidate = tomllib.loads(candidate_path.read_text(encoding="utf-8"))
    eval_data = tomllib.loads(
        (
            ROOT / ".agents" / "skill-evals" / "skill-lifecycle-review.toml"
        ).read_text(encoding="utf-8")
    )
    policy = (
        ROOT
        / ".agents"
        / "skills"
        / "skill-lifecycle-review"
        / "agents"
        / "openai.yaml"
    ).read_text(encoding="utf-8")

    assert candidate["status"] == "active"
    assert candidate["realized_by_skills"] == ["skill-lifecycle-review"]
    assert candidate["score"]["total"] == 10
    evidence = "\n".join(candidate["observable_evidence"])
    assert "Issue #491" in evidence and "PR #492" in evidence
    assert "Issue #493" in evidence and "PR #494" in evidence
    assert eval_data["validation_status"] == "validated"
    assert eval_data["invocation_policy"] == "implicit-after-validation"
    assert "allow_implicit_invocation: true" in policy
    assert MODULE.validate(ROOT).accepted


def test_candidate_realized_and_overlapping_skills_cannot_be_confused(
    tmp_path: Path,
) -> None:
    target = _copy_agents(tmp_path)
    candidate = (
        target / ".agents" / "skill-candidates" / "layer-aware-change-validation.toml"
    )
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "related_overlapping_skills = []",
            'related_overlapping_skills = ["xpotato-sim-change-validation"]',
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("must be disjoint" in error for error in result.errors)


def test_duplicate_candidate_to_skill_mapping_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = (
        target
        / ".agents"
        / "skill-candidates"
        / "protected-long-form-body-safety.toml"
    )
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            "realized_by_skills = []",
            'realized_by_skills = ["skill-lifecycle-review"]',
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("realized by multiple candidates" in error for error in result.errors)


def test_isolated_skill_without_candidate_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    _add_fixture_skill(
        target,
        skill_name="isolated-draft-fixture",
        candidate_key="isolated-draft-candidate",
        status="draft",
        score_axes=(1, 1, 1, 1, 1),
        implicit=False,
    )
    (
        target
        / ".agents"
        / "skill-candidates"
        / "isolated-draft-candidate.toml"
    ).unlink()

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("Skill is not traceable to a candidate" in error for error in result.errors)


def test_draft_skill_cannot_allow_implicit_invocation(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-pr-handoff.toml"
    eval_path.write_text(
        eval_path.read_text(encoding="utf-8").replace(
            'validation_status = "validated"', 'validation_status = "draft"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("draft Skill cannot allow implicit invocation" in error for error in result.errors)


def test_incomplete_validation_cannot_allow_implicit_invocation(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-pr-handoff.toml"
    eval_path.write_text(
        eval_path.read_text(encoding="utf-8").replace(
            'validation_status = "validated"', 'validation_status = "pending"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("invalid eval validation_status" in error for error in result.errors)
    assert any("cannot allow implicit invocation" in error for error in result.errors)


def test_unresolved_approval_cannot_allow_implicit_invocation(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-plugin-change.toml"
    eval_path.write_text(
        eval_path.read_text(encoding="utf-8").replace(
            "unresolved_approval = false", "unresolved_approval = true"
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("unresolved approval" in error for error in result.errors)


def test_side_effectful_policy_cannot_allow_implicit_invocation(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-plugin-change.toml"
    eval_path.write_text(
        eval_path.read_text(encoding="utf-8").replace(
            'side_effect_policy = "instruction-only"', 'side_effect_policy = "side-effectful"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("side-effectful Skill" in error for error in result.errors)


def test_implicit_invocation_cannot_grant_permissions(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    config = target / ".agents" / "skill-system.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            "implicit_invocation_grants_permissions = false",
            "implicit_invocation_grants_permissions = true",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("must not grant permissions" in error for error in result.errors)


def test_policy_value_outside_policy_section_or_in_comment_is_not_adopted(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    policy = target / ".agents" / "skills" / "xpotato-sim-pr-handoff" / "agents" / "openai.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "\npolicy:\n",
            "\n  allow_implicit_invocation: false\n"
            "# allow_implicit_invocation: false\n"
            "policy:\n",
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert result.accepted, result.errors


def test_unconditional_skill_chaining_fixture_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    eval_path = target / ".agents" / "skill-evals" / "xpotato-sim-plugin-change.toml"
    eval_path.write_text(
        eval_path.read_text(encoding="utf-8").replace(
            "automatic_chain = false", "automatic_chain = true"
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("must not require unconditional Skill chaining" in error for error in result.errors)


def test_routing_fixtures_select_the_narrow_skill_and_preserve_boundaries() -> None:
    evals = {
        path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".agents" / "skill-evals").glob("*.toml")
    }
    plugin_case = evals["xpotato-sim-plugin-change"]["routing_cases"][0]
    validation_case = evals["xpotato-sim-change-validation"]["routing_cases"][0]
    handoff_case = evals["xpotato-sim-pr-handoff"]["routing_cases"][0]

    assert plugin_case["primary_skill"] == "xpotato-sim-plugin-change"
    assert validation_case["primary_skill"] == "xpotato-sim-change-validation"
    assert handoff_case["primary_skill"] == "xpotato-sim-pr-handoff"
    assert all(
        not case["automatic_chain"] and not case["permission_grant"]
        for data in evals.values()
        for case in data["routing_cases"]
    )


def test_representative_metadata_routing_scenarios_are_bounded() -> None:
    evals = {
        path.stem: tomllib.loads(path.read_text(encoding="utf-8"))
        for path in (ROOT / ".agents" / "skill-evals").glob("*.toml")
    }
    unrelated = "このrepositoryと無関係な一般質問に答えてください。"

    assert any("docs" in prompt for prompt in evals["xpotato-sim-change-validation"]["positive_triggers"])
    assert any(
        "pluginと無関係" in prompt
        for prompt in evals["xpotato-sim-plugin-change"]["negative_triggers"]
    )
    assert any(
        "read-only" in prompt for prompt in evals["xpotato-sim-pr-handoff"]["positive_triggers"]
    )
    assert all(unrelated in data["negative_triggers"] for data in evals.values())
    assert "Issue / PR mutation" in evals["skill-lifecycle-review"]["forbidden_actions"]
    assert "serial / OSC / robot output" in evals["xpotato-sim-change-validation"]["forbidden_actions"]
    assert "external mutation" in evals["xpotato-sim-plugin-change"]["forbidden_actions"]
    assert "commit" in evals["xpotato-sim-pr-handoff"]["forbidden_actions"]


def test_unimplemented_candidate_cannot_be_active(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    candidate.write_text(
        candidate.read_text(encoding="utf-8").replace(
            'status = "candidate"', 'status = "active"'
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("active candidate has no realized Skill" in error for error in result.errors)


def test_transient_branch_reference_is_rejected_from_skill_body(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    skill = target / ".agents" / "skills" / "skill-lifecycle-review" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\nDo not fix codex/example-branch in the Skill body.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("transient Issue / branch" in error for error in result.errors)


def test_transient_full_sha_is_rejected_from_skill_body(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    skill = target / ".agents" / "skills" / "skill-lifecycle-review" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\nA transient value is 05d8321e0918baec69caede2c22db398055057a6.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("transient Issue / branch / SHA" in error for error in result.errors)


def test_transient_contextual_abbreviated_sha_is_rejected_from_skill_body(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    skill = target / ".agents" / "skills" / "skill-lifecycle-review" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\nDo not pin head: 05d8321 or base SHA = 329ec5df1886.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("transient Issue / branch / SHA" in error for error in result.errors)


def test_non_sha_hex_like_text_is_not_rejected_from_skill_body(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    skill = target / ".agents" / "skills" / "skill-lifecycle-review" / "SKILL.md"
    skill.write_text(
        skill.read_text(encoding="utf-8") + "\nA generic identifier is abcdef1234567.\n",
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert result.accepted, result.errors


def test_candidate_evidence_can_contain_full_sha(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    text = candidate.read_text(encoding="utf-8")
    candidate.write_text(
        text.replace(
            'observable_evidence = [',
            'observable_evidence = [\n  "commit 05d8321e0918baec69caede2c22db398055057a6 was inspected.",',
        ),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert result.accepted, result.errors


def test_related_overlapping_skill_does_not_realize_draft_candidate(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    text = candidate.read_text(encoding="utf-8")
    candidate.write_text(
        text.replace('status = "candidate"', 'status = "draft"')
        .replace('related_overlapping_skills = []', 'related_overlapping_skills = ["xpotato-sim-change-validation"]')
        .replace('proposed_action = "record"', 'proposed_action = "create-draft"'),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("must realize at least one Skill" in error for error in result.errors)


def test_candidate_skill_reference_and_lifecycle_are_consistent(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    text = candidate.read_text(encoding="utf-8")
    candidate.write_text(
        text.replace('status = "candidate"', 'status = "draft"')
        .replace('realized_by_skills = []', 'realized_by_skills = ["missing-skill"]')
        .replace('proposed_action = "record"', 'proposed_action = "update"'),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("unknown Skill" in error for error in result.errors)


def test_draft_candidate_without_realized_skill_is_rejected(tmp_path: Path) -> None:
    target = _copy_agents(tmp_path)
    candidate = target / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    text = candidate.read_text(encoding="utf-8")
    candidate.write_text(
        text.replace('status = "candidate"', 'status = "draft"')
        .replace('proposed_action = "record"', 'proposed_action = "update"'),
        encoding="utf-8",
        newline="\n",
    )

    result = MODULE.validate(target)

    assert not result.accepted
    assert any("must realize at least one Skill" in error for error in result.errors)


def test_record_only_candidate_without_related_skill_is_allowed() -> None:
    candidate = ROOT / ".agents" / "skill-candidates" / "protected-long-form-body-safety.toml"
    text = candidate.read_text(encoding="utf-8")

    assert 'status = "candidate"' in text
    assert 'proposed_action = "record"' in text
    assert 'realized_by_skills = []' in text
    assert 'related_overlapping_skills = []' in text
    assert MODULE.validate(ROOT).accepted


def test_candidate_provenance_can_contain_issue_and_sha_evidence() -> None:
    candidate = (
        ROOT / ".agents" / "skill-candidates" / "layer-aware-change-validation.toml"
    ).read_text(encoding="utf-8")

    assert "PR #403" in candidate
    assert "3ce7f30" in candidate
    assert MODULE.validate(ROOT).accepted


def test_current_agent_routing_is_model_independent_across_skill_lifecycle() -> None:
    policy_paths = [
        ROOT / "AGENTS.md",
        ROOT / "docs" / "operations" / "codex-workflow.md",
        ROOT / "docs" / "operations" / "agent-skill-governance.md",
        *sorted((ROOT / ".agents" / "skills").glob("*/SKILL.md")),
        *sorted((ROOT / ".agents" / "skills").glob("*/agents/openai.yaml")),
        *sorted((ROOT / ".agents" / "skill-evals").glob("*.toml")),
    ]
    legacy_routing_names = ("Sol", "Astra", "Luna", "GPT-5", "GPT-6")
    violations = {
        str(path.relative_to(ROOT)): [
            name for name in legacy_routing_names if name in path.read_text(encoding="utf-8")
        ]
        for path in policy_paths
    }
    for path in sorted((ROOT / ".agents" / "skill-candidates").glob("*.toml")):
        candidate = tomllib.loads(path.read_text(encoding="utf-8"))
        candidate.pop("observable_evidence", None)
        current_policy = repr(candidate)
        names = [name for name in legacy_routing_names if name in current_policy]
        if names:
            violations[str(path.relative_to(ROOT))] = names
    assert not {path: names for path, names in violations.items() if names}

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    workflow = (ROOT / "docs" / "operations" / "codex-workflow.md").read_text(encoding="utf-8")
    governance = (ROOT / "docs" / "operations" / "agent-skill-governance.md").read_text(encoding="utf-8")
    assert "repository側では特定modelやmodel間の担当分担を固定せず" in agents
    assert "repositoryは特定model名、model ID、model階層、model間の担当表を固定しない" in workflow
    assert "Skill systemはこれを複製・上書きしない" in governance
    assert "現在の担当指定やpromotion条件へ読み替えない" in governance
