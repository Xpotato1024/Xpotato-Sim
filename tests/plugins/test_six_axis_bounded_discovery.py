from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from xpotato_sim.plugins.environments.catalog import ENVIRONMENT_REGISTRY
from xpotato_sim.plugins.environments.discovery import (
    EnvironmentDiscoveryRoot,
    EnvironmentPluginDiscoveryError,
    discover_environment_plugins,
)
from xpotato_sim.plugins.evaluations.catalog import EVALUATION_REGISTRY
from xpotato_sim.plugins.evaluations.discovery import (
    EvaluationDiscoveryRoot,
    EvaluationPluginDiscoveryError,
    discover_evaluation_plugins,
)
from xpotato_sim.plugins.tasks.catalog import TASK_REGISTRY
from xpotato_sim.plugins.tasks.discovery import (
    TaskDiscoveryRoot,
    TaskPluginDiscoveryError,
    discover_task_plugins,
)


def _namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    packages: dict[str, str | None],
):
    root = tmp_path / name
    root.mkdir()
    (root / "__init__.py").write_text("", encoding="utf-8")
    for package_name, source in packages.items():
        package = root / package_name
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        if source is not None:
            (package / "plugin.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    return importlib.import_module(name)


def _environment_export(plugin_id: str) -> str:
    return f"""
from dataclasses import replace
from xpotato_sim.plugins.environments.free_space_environment import FREE_SPACE_ENVIRONMENT_PLUGIN
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
ENVIRONMENT_PLUGIN = replace(
    FREE_SPACE_ENVIRONMENT_PLUGIN,
    identity=VersionedIdentity("{plugin_id}", 1),
)
"""


def _task_export(plugin_id: str) -> str:
    return f"""
from dataclasses import replace
from xpotato_sim.plugins.tasks.endpoint_reach_task import ENDPOINT_REACH_TASK_PLUGIN
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
TASK_PLUGIN = replace(
    ENDPOINT_REACH_TASK_PLUGIN,
    identity=VersionedIdentity("{plugin_id}", 1),
)
"""


def _evaluation_export(plugin_id: str) -> str:
    return f"""
from dataclasses import replace
from xpotato_sim.plugins.evaluations.success_within_timeout import SUCCESS_WITHIN_TIMEOUT_PLUGIN
from xpotato_sim.runtime.experiment.contracts import VersionedIdentity
EVALUATION_PLUGIN = replace(
    SUCCESS_WITHIN_TIMEOUT_PLUGIN,
    identity=VersionedIdentity("{plugin_id}", 1),
)
"""


def test_environment_task_evaluation_order_private_exclusion_and_production_separation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment_namespace = _namespace(
        tmp_path,
        monkeypatch,
        "ordered_environment_plugins",
        {
            "_private": "raise RuntimeError('private package must not import')",
            "z_environment": _environment_export("z_environment"),
            "a_environment": _environment_export("a_environment"),
        },
    )
    task_namespace = _namespace(
        tmp_path,
        monkeypatch,
        "ordered_task_plugins",
        {
            "_private": "raise RuntimeError('private package must not import')",
            "z_task": _task_export("z_task"),
            "a_task": _task_export("a_task"),
        },
    )
    evaluation_namespace = _namespace(
        tmp_path,
        monkeypatch,
        "ordered_evaluation_plugins",
        {
            "_private": "raise RuntimeError('private package must not import')",
            "z_evaluation": _evaluation_export("z_evaluation"),
            "a_evaluation": _evaluation_export("a_evaluation"),
        },
    )

    environments = discover_environment_plugins(
        EnvironmentDiscoveryRoot(environment_namespace)
    )
    tasks = discover_task_plugins(TaskDiscoveryRoot(task_namespace))
    evaluations = discover_evaluation_plugins(
        EvaluationDiscoveryRoot(evaluation_namespace)
    )

    assert environments.ids == ("a_environment", "z_environment")
    assert tasks.ids == ("a_task", "z_task")
    assert evaluations.ids == ("a_evaluation", "z_evaluation")
    assert not set(environments.ids) & set(ENVIRONMENT_REGISTRY.ids)
    assert not set(tasks.ids) & set(TASK_REGISTRY.ids)
    assert not set(evaluations.ids) & set(EVALUATION_REGISTRY.ids)


@pytest.mark.parametrize(
    ("axis", "packages", "match"),
    (
        ("environment", {"missing_environment": None}, "entry point is missing"),
        (
            "environment",
            {"wrong_type": "ENVIRONMENT_PLUGIN = object()"},
            "invalid Environment Plugin type",
        ),
        (
            "environment",
            {"broken_environment": "raise RuntimeError('broken environment')"},
            "import failed",
        ),
        (
            "environment",
            {
                "duplicate_a": _environment_export("duplicate_environment"),
                "duplicate_b": _environment_export("duplicate_environment"),
            },
            "duplicate environment plugin registration",
        ),
        (
            "environment",
            {"wrong_package": _environment_export("other_environment")},
            "package/declaration identity mismatch",
        ),
        ("task", {"missing_task": None}, "entry point is missing"),
        (
            "task",
            {"wrong_type": "TASK_PLUGIN = object()"},
            "invalid Task Plugin type",
        ),
        (
            "task",
            {"broken_task": "raise RuntimeError('broken task')"},
            "import failed",
        ),
        (
            "task",
            {
                "duplicate_a": _task_export("duplicate_task"),
                "duplicate_b": _task_export("duplicate_task"),
            },
            "duplicate task plugin registration",
        ),
        (
            "task",
            {"wrong_package": _task_export("other_task")},
            "package/declaration identity mismatch",
        ),
        ("evaluation", {"missing_evaluation": None}, "entry point is missing"),
        (
            "evaluation",
            {"wrong_type": "EVALUATION_PLUGIN = object()"},
            "invalid Evaluation Plugin type",
        ),
        (
            "evaluation",
            {"broken_evaluation": "raise RuntimeError('broken evaluation')"},
            "import failed",
        ),
        (
            "evaluation",
            {
                "duplicate_a": _evaluation_export("duplicate_evaluation"),
                "duplicate_b": _evaluation_export("duplicate_evaluation"),
            },
            "duplicate evaluation plugin registration",
        ),
        (
            "evaluation",
            {"wrong_package": _evaluation_export("other_evaluation")},
            "package/declaration identity mismatch",
        ),
    ),
)
def test_environment_task_evaluation_discovery_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    axis: str,
    packages: dict[str, str | None],
    match: str,
) -> None:
    namespace = _namespace(
        tmp_path,
        monkeypatch,
        f"broken_{axis}_plugins_{len(packages)}_{next(iter(packages))}",
        packages,
    )
    if axis == "environment":
        with pytest.raises(EnvironmentPluginDiscoveryError, match=match):
            discover_environment_plugins(EnvironmentDiscoveryRoot(namespace))
    elif axis == "task":
        with pytest.raises(TaskPluginDiscoveryError, match=match):
            discover_task_plugins(TaskDiscoveryRoot(namespace))
    else:
        with pytest.raises(EvaluationPluginDiscoveryError, match=match):
            discover_evaluation_plugins(EvaluationDiscoveryRoot(namespace))
