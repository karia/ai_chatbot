"""Run with python3 tests/test_makefile.py; no AWS calls or deployments."""

import os
from pathlib import Path
import subprocess
import tempfile


def test_deployment_commands():
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        calls = root / "calls"
        for name, body in {
            "terraform": 'echo terraform >> "$CALLS"; echo \'{"config":"shared"}\'; exit "${TF_EXIT:-0}"',
            "lambroll": 'echo "lambroll $APP_CONFIG $*" >> "$CALLS"; exit "${DEPLOY_EXIT:-0}"',
        }.items():
            tool = root / name
            tool.write_text("#!/bin/sh\n" + body + "\n")
            tool.chmod(0o755)
        env = {**os.environ, "PATH": directory + os.pathsep + os.environ["PATH"], "CALLS": str(calls)}
        command = ["make", "-f", str(makefile), "-o", "init-infra", "-o", "build", "deploy-app"]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True)
        assert result.returncode == 0, result.stderr
        lines = calls.read_text().splitlines()
        assert len(lines) == 3 and lines[0] == "terraform", lines
        assert all('lambroll {"config":"shared"}' in line for line in lines[1:]), lines
        assert all("--alias current" in line and "--no-publish" not in line for line in lines[1:]), lines
        for failure, expected_calls in [("TF_EXIT", 1), ("DEPLOY_EXIT", 2)]:
            calls.unlink()
            result = subprocess.run(command, cwd=root, env={**env, failure: "1"}, capture_output=True)
            assert result.returncode != 0
            assert len(calls.read_text().splitlines()) == expected_calls
        # Remove the stub so this path can be the configuration directory.
        (root / "terraform").unlink()
        (root / "terraform").mkdir()
        (root / "terraform/dev.tfvars").touch()
        result = subprocess.run(["make", "-f", str(makefile), "init-infra", "ENV=dev"], cwd=root, env=env, capture_output=True)
        assert result.returncode != 0
        assert b"Missing file: terraform/dev.tfbackend" in result.stderr


ROOT = Path(__file__).resolve().parents[1]


def test_rollback_selects_one_function_and_exact_version():
    result = subprocess.run(
        ["make", "-n", "rollback-app", "ENV=dev", "FUNCTION=worker", "VERSION=7"],
        cwd=ROOT, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "lambroll rollback" in result.stdout
    assert "--version 7" in result.stdout
    assert "src/worker/function.jsonnet" in result.stdout
    assert "lambroll deploy" not in result.stdout


def test_invalid_rollback_stops_before_aws(tmp_path):
    fake_bin = tmp_path / "terraform"
    fake_bin.write_text("#!/bin/sh\nexit 99\n")
    fake_bin.chmod(0o755)
    for args in (["FUNCTION=invalid", "VERSION=7"], ["FUNCTION=worker", "VERSION=latest"]):
        result = subprocess.run(
            ["make", "rollback-app", *args], cwd=ROOT, text=True, capture_output=True,
            env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        )
        assert result.returncode != 0
        assert "FUNCTION must" in result.stdout or "VERSION must" in result.stdout


if __name__ == "__main__":
    test_deployment_commands()
    print("Makefile checks passed")
