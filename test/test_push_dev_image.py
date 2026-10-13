# SPDX-FileCopyrightText: Contributors to PyPSA-Eur <https://github.com/pypsa/pypsa-eur>
#
# SPDX-License-Identifier: MIT

"""Exercise publishing branches without credentials, Docker or registry writes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "ghcr.io/mlhaochang/eur-dev-env"


def publish(
    tmp_path,
    *,
    cached=True,
    event="push",
    changed=False,
    fail="",
    ref="refs/heads/master",
):
    docker = tmp_path / "docker"
    log = tmp_path / "calls.jsonl"
    docker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['DOCKER_CALL_LOG'], 'a') as f:\n"
        "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if sys.argv[1:3] == ['manifest', 'inspect']:\n"
        "    sys.exit(0 if os.environ['CACHED_IMAGE'] == 'yes' else 1)\n"
        "sys.exit(1 if sys.argv[1] == os.environ['FAIL_COMMAND'] else 0)\n"
    )
    docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(ROOT / ".github/scripts/push-dev-image.sh")],
        cwd=ROOT,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "DOCKER_CALL_LOG": str(log),
            "CACHED_IMAGE": "yes" if cached else "no",
            "FAIL_COMMAND": fail,
            "IMAGE_NAME": "mlhaochang/eur-dev-env",
            "GITHUB_SHA": "current",
            "hash_last_changed": "current" if changed else "environment",
            "GITHUB_EVENT_NAME": event,
            "GITHUB_REF": ref,
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "MLHaoChang/pypsa-eur",
        },
        capture_output=True,
        text=True,
    )
    calls = (
        [json.loads(line) for line in log.read_text().splitlines()]
        if log.exists()
        else []
    )
    return result, calls


@pytest.mark.parametrize(
    "cached,event,changed",
    [(False, "push", False), (True, "workflow_dispatch", False), (True, "push", True)],
)
def test_missing_cache_changed_environment_and_manual_dispatch_build_once(
    tmp_path, cached, event, changed
):
    result, calls = publish(tmp_path, cached=cached, event=event, changed=changed)
    assert result.returncode == 0, result.stderr
    builds = [call for call in calls if call[0] == "build"]
    assert len(builds) == 1
    assert (
        "org.opencontainers.image.source=https://github.com/MLHaoChang/pypsa-eur"
        in builds[0]
    )
    assert not any(call[0] == "pull" for call in calls)
    assert ["push", f"{IMAGE}:current"] in calls
    assert ["push", f"{IMAGE}:latest"] == calls[-1]
    if not changed:
        assert ["push", f"{IMAGE}:environment"] in calls
    assert all("ghcr.io/pypsa/" not in " ".join(call) for call in calls)


def test_unchanged_environment_reuses_owned_image_and_updates_latest(tmp_path):
    result, calls = publish(tmp_path)
    assert result.returncode == 0, result.stderr
    assert calls == [
        ["manifest", "inspect", f"{IMAGE}:environment"],
        ["pull", f"{IMAGE}:environment"],
        ["tag", f"{IMAGE}:environment", f"{IMAGE}:current"],
        ["push", f"{IMAGE}:current"],
        ["tag", f"{IMAGE}:current", f"{IMAGE}:latest"],
        ["push", f"{IMAGE}:latest"],
    ]


@pytest.mark.parametrize(
    "fail,cached", [("build", False), ("pull", True), ("push", True)]
)
def test_docker_failure_is_fatal_and_cannot_update_latest(tmp_path, fail, cached):
    result, calls = publish(tmp_path, fail=fail, cached=cached)
    assert result.returncode != 0
    assert ["push", f"{IMAGE}:latest"] not in calls


def test_dispatch_on_other_branch_cannot_publish(tmp_path):
    result, calls = publish(
        tmp_path, event="workflow_dispatch", ref="refs/heads/feature"
    )
    assert result.returncode != 0
    assert not calls


def test_workflow_uses_repository_token_and_lowercase_owner_namespace():
    workflow = yaml.safe_load((ROOT / ".github/workflows/push-images.yaml").read_text())
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}
    steps = workflow["jobs"]["push-image"]["steps"]
    login = next(
        step
        for step in steps
        if step.get("uses", "").startswith("docker/login-action@")
    )
    assert login["with"]["password"] == "${{ secrets.GITHUB_TOKEN }}"
    setup = next(step for step in steps if "IMAGE_NAME=" in step.get("run", ""))
    assert "${GITHUB_REPOSITORY_OWNER,,}/eur-dev-env" in setup["run"]
    assert (
        "pixi.toml pixi.lock pyproject.toml gridspine .dockerignore docker/dev-env/Dockerfile"
        in setup["run"]
    )
    assert steps[-1]["run"] == "bash .github/scripts/push-dev-image.sh"
