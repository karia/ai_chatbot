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


if __name__ == "__main__":
    test_deployment_commands()
    print("Makefile checks passed")
