"""deploy/deploy.sh must not swap the container when the in-image tests fail
(audit 2026-10-12, finding 3: `pytest | tail -1` hid the failure)."""
import os
import stat
import subprocess
import tempfile
from pathlib import Path

DEPLOY = Path(__file__).resolve().parent.parent / "deploy" / "deploy.sh"


_TAGS = "abc1234\\nprevious\\nold1111\\nold2222\\nold3333\\nlatest"


def _run(tests_pass: bool, uploads_mounted: bool = True, free_kb: int = 20_000_000):
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "calls.log"
        health = ('{"status":"ok","build":"abc1234","rfp_uploads_mounted":%s}'
                  % str(uploads_mounted).lower())
        fake = {
            "git": 'case "$1" in rev-parse) echo abc1234;; esac; exit 0',
            # `docker run` is the test gate; `docker images` lists tags newest
            # first; everything else succeeds.
            "docker": (f'echo "$@" >> "{log}"; '
                       f'case "$1" in run) exit {0 if tests_pass else 1};; '
                       f'images) printf "{_TAGS}\\n";; info) echo /var/lib/docker;; esac; exit 0'),
            "curl": f'case "$*" in *health*) echo \'{health}\';; esac; exit 0',
            "sleep": "exit 0",
            "df": (f'echo "Filesystem 1024-blocks Used Available Capacity Mounted"; '
                   f'echo "/dev/root 30000000 1 {free_kb} 99% /"'),
        }
        for name, body in fake.items():
            f = Path(tmp) / name
            f.write_text(f"#!/bin/sh\n{body}\n")
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}",
               "SHILPI_DEPLOY_HISTORY": f"{tmp}/history"}
        proc = subprocess.run(["bash", str(DEPLOY)], env=env, capture_output=True, text=True)
        return proc.returncode, (log.read_text() if log.exists() else "") + proc.stdout


def test_failing_tests_stop_the_deploy_before_the_swap():
    code, calls = _run(tests_pass=False)
    assert code != 0
    assert "compose" not in calls


def test_passing_tests_reach_the_swap():
    code, calls = _run(tests_pass=True)
    assert code == 0, calls
    assert "compose" in calls


def test_a_build_that_cannot_see_the_uploads_is_rolled_back():
    """Deep audit F70: the brain reads RFP attachments from Open WebUI's upload
    folder. A deploy that lost that mount answered /v1/keepalive, went green,
    and left the RFP upload path dead."""
    code, calls = _run(tests_pass=True, uploads_mounted=False)
    assert code != 0
    assert "deployed sarvam-brain" not in calls
    assert "uploads" in calls                      # says why, not just "unhealthy"
    swaps = [ln for ln in calls.splitlines() if ln.startswith("compose ")]
    assert len(swaps) == 2 and "rolling back" in calls   # swapped in, then put back



def test_a_deploy_stops_before_building_when_the_disk_is_nearly_full():
    """2026-10-23: the 29 GB disk filled (Open WebUI archives, build cache,
    1 GB per image tag) and a deploy failed part-way."""
    code, calls = _run(tests_pass=True, free_kb=2_500_000)
    assert code != 0
    assert "build" not in [ln.split()[0] for ln in calls.splitlines() if ln.split()]
    assert "compose" not in calls
    assert "3 GB" in calls and "2.4 GB free" in calls


def test_a_successful_deploy_keeps_running_previous_and_two_newest_and_prunes_build_cache():
    code, calls = _run(tests_pass=True)
    assert code == 0, calls
    removed = sorted(ln.split()[-1] for ln in calls.splitlines() if ln.startswith("rmi"))
    assert removed == ["sarvam-brain:latest", "sarvam-brain:old2222", "sarvam-brain:old3333"]
    assert "builder prune -af" in calls
    assert calls.index("deployed sarvam-brain") > 0


def test_a_failed_deploy_prunes_nothing():
    code, calls = _run(tests_pass=True, uploads_mounted=False)
    assert code != 0
    assert "rmi" not in calls and "builder prune" not in calls
