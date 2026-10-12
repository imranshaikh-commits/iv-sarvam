"""deploy/deploy.sh must not swap the container when the in-image tests fail
(audit 2026-10-12, finding 3: `pytest | tail -1` hid the failure)."""
import os
import stat
import subprocess
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"


def _run(tests_pass: bool):
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "calls.log"
        fake = {
            "git": 'case "$1" in rev-parse) echo abc1234;; esac; exit 0',
            # `docker run` is the test gate; everything else succeeds.
            "docker": f'echo "$@" >> "{log}"; [ "$1" = run ] && exit {0 if tests_pass else 1}; exit 0',
            "curl": 'case "$*" in *health*) echo \'{"build":"abc1234"}\';; esac; exit 0',
        }
        for name, body in fake.items():
            f = Path(tmp) / name
            f.write_text(f"#!/bin/sh\n{body}\n")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}",
               "SHILPI_DEPLOY_HISTORY": f"{tmp}/history"}
        proc = subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True)
        return proc.returncode, log.read_text() if log.exists() else ""


def test_failing_tests_stop_the_deploy_before_the_swap():
    code, calls = _run(tests_pass=False)
    assert code != 0
    assert "compose" not in calls


def test_passing_tests_reach_the_swap():
    code, calls = _run(tests_pass=True)
    assert code == 0, calls
    assert "compose" in calls
