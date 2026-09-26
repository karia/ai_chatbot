from pathlib import Path

import yaml


def workflow(name):
    document = yaml.safe_load(Path(f".github/workflows/{name}.yml").read_text())
    return document, document.get("on", document.get(True))


def test_ci_audits_discord_gateway_dependencies():
    document, _ = workflow("ci")
    install = next(
        step["run"]
        for step in document["jobs"]["audit"]["steps"]
        if "pip install" in step.get("run", "")
    )

    assert "-r src/discord_gateway/requirements.txt" in install


def test_image_workflow_builds_pull_requests_without_pushing():
    document, triggers = workflow("discord-gateway-image")
    expected_paths = [
        "src/discord_gateway/**",
        ".github/workflows/discord-gateway-image.yml",
    ]
    assert triggers["pull_request"]["paths"] == expected_paths
    assert triggers["push"]["paths"] == expected_paths

    job = document["jobs"]["build"]
    assert job["runs-on"] == "ubuntu-latest"
    steps = job["steps"]
    login = next(step for step in steps if "login-action" in step.get("uses", ""))
    build = next(step for step in steps if "build-push-action" in step.get("uses", ""))
    assert login["if"] == "github.event_name == 'push'"
    assert build["with"]["platforms"] == "linux/amd64"
    assert build["with"]["push"] == "${{ github.event_name == 'push' }}"


def test_gateway_requirements_target_amd64():
    requirements = Path("src/discord_gateway/requirements.txt").read_text()

    assert "--python-platform x86_64-manylinux_2_28" in requirements
