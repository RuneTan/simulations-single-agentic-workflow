"""Check the actual environment, graph and command boundary, including failures."""
import json
import os
from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from nsbi_agent.contracts import EnvironmentRequest
from nsbi_agent.graph import run_graph
from nsbi_agent.tools.environment import PROJECT_ROOT, inspect_environment


def request() -> EnvironmentRequest:
    return EnvironmentRequest.model_validate_json(
        (PROJECT_ROOT / "examples/environment.json").read_text()
    )


@pytest.mark.parametrize("payload", [
    {"packages": {"langgraph": 123}},
    {"packages": {"langgraph": "1.2.17"}, "nevents": 2000},
])
def test_invalid_requests_are_rejected(payload):
    with pytest.raises(ValidationError):
        EnvironmentRequest.model_validate(payload)


def test_real_environment_is_owned_by_this_project():
    result = inspect_environment(request())
    assert result.status == "succeeded", result.model_dump()
    assert all(item.status == "pass" for item in result.checks)
    for package in result.data["packages"].values():
        assert Path(package["location"]).is_relative_to(PROJECT_ROOT / ".venv")


@pytest.mark.parametrize("packages", [
    {"nsbi-deliberately-missing-package": "1.0"},
    {"langgraph": "0.0.0"},
])
def test_missing_packages_and_wrong_versions_fail(packages):
    result = inspect_environment(EnvironmentRequest(packages=packages))
    assert result.status == "failed"
    assert result.errors
    assert any(item.status == "fail" for item in result.checks)


@pytest.mark.parametrize("valid", [True, False])
def test_real_langgraph_preserves_tool_success_and_failure(valid):
    item = request() if valid else EnvironmentRequest(packages={"langgraph": "0.0.0"})
    direct = inspect_environment(item)
    graphed = run_graph(item)
    assert graphed.model_dump() == direct.model_dump()


def test_launcher_ignores_a_contaminated_terminal(tmp_path):
    (tmp_path / "sitecustomize.py").write_text("raise RuntimeError('foreign Python loaded')")
    environment = os.environ.copy()
    environment.update({
        "PYTHONPATH": str(tmp_path),
        "PYTHONHOME": "/deliberately-missing-python",
        "LD_LIBRARY_PATH": str(tmp_path),
        "ROOTSYS": str(tmp_path),
        "ROOT_INCLUDE_PATH": str(tmp_path),
        "VIRTUAL_ENV": "/deliberately-wrong-environment",
    })
    completed = subprocess.run(
        ["/bin/bash", str(PROJECT_ROOT / "run.sh"), "graph",
         str(PROJECT_ROOT / "examples/environment.json")],
        cwd=tmp_path, env=environment, text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "succeeded"
    assert str(tmp_path) not in result["data"]["import_paths"]


@pytest.mark.parametrize("content", [
    "{broken JSON",
    b"\xff",
    '{"packages": {"langgraph": "1.2.17"}, "nevents": 2000}',
])
def test_cli_input_errors_are_json_and_nonzero(tmp_path, content):
    source = tmp_path / "invalid.json"
    source.write_bytes(content if isinstance(content, bytes) else content.encode())
    completed = subprocess.run(
        ["/bin/bash", str(PROJECT_ROOT / "run.sh"), "inspect", str(source)],
        text=True, capture_output=True, timeout=30,
    )
    assert completed.returncode == 2
    result = json.loads(completed.stdout)
    assert result["status"] == "failed"
    assert result["errors"][0]["code"] == "INVALID_INPUT"
