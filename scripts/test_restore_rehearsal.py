"""scripts/restore_rehearsal.sh (deep audit F46): rebuild a scratch Supabase
project and a throwaway Open WebUI from the backups, then compare counts. Run
with fake docker/python3/curl so nothing leaves this machine."""
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "restore_rehearsal.sh"
PROD = "jthrjmiulefmyrqtwsnz"


def _run(tmp_path, url="https://scratchref.supabase.co",
         db="postgresql://postgres.scratchref:pw@aws-0-eu.pooler.supabase.com:5432/postgres"):
    log = tmp_path / "calls.log"
    fake = {
        "docker": (f'echo "docker $*" >> "{log}"; '
                   'case "$1" in inspect) echo sarvam-open-webui;; '
                   'run) case "$*" in *sqlite3*) echo "users=2 chats=40 files=7";; esac;; esac; exit 0'),
        "python3": f'echo "python3 $* SUPABASE_URL=$SUPABASE_URL" >> "{log}"; exit 0',
        "curl": f'echo "curl $*" >> "{log}"; exit 0',
        "sleep": "exit 0",
    }
    for name, body in fake.items():
        f = tmp_path / name
        f.write_text(f"#!/bin/sh\n{body}\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    backup = tmp_path / "20261020T021500Z"
    backup.mkdir(exist_ok=True)
    archive = tmp_path / "owui-20261020T022000Z.tgz"
    archive.write_bytes(b"tgz")
    if log.exists():
        log.unlink()
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "HOME": str(tmp_path),
           "SCRATCH_SUPABASE_URL": url, "SCRATCH_SUPABASE_KEY": "scratch-service-key",
           "SCRATCH_DB_URL": db, "STORAGE_DIR": str(tmp_path / "storage")}
    proc = subprocess.run(["bash", str(SCRIPT), str(backup), str(archive)], env=env,
                          capture_output=True, text=True, cwd=ROOT)
    return proc, (log.read_text() if log.exists() else "")


def test_it_refuses_the_production_project(tmp_path):
    for kw in ({"url": f"https://{PROD}.supabase.co"},
               {"db": f"postgresql://postgres.{PROD}:pw@pooler.supabase.com:5432/postgres"}):
        proc, calls = _run(tmp_path, **kw)
        assert proc.returncode != 0 and PROD in proc.stdout + proc.stderr
        assert calls == "", "nothing may run against production"


def test_it_never_reads_the_production_env_file():
    code = [ln for ln in SCRIPT.read_text().splitlines() if not ln.lstrip().startswith("#")]
    assert not any("sarvam.env" in ln or "--env-file" in ln for ln in code)


def test_migrations_then_tables_then_storage_then_open_webui_then_counts(tmp_path):
    proc, calls = _run(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    lines = calls.splitlines()
    migrations = sorted(p.name for p in (ROOT / "supabase" / "migrations").glob("*.sql"))
    applied = [ln for ln in lines if "psql" in ln]
    assert len(applied) == len(migrations)
    assert all(m in ln for m, ln in zip(migrations, applied))          # every file, in order

    def first(text):
        return next(i for i, ln in enumerate(lines) if text in ln)
    tables, storage = first("--restore "), first("--restore-storage")
    owui, counts = first("tar xzf"), first("--compare")
    assert first("psql") < tables < storage < owui < counts
    assert "--tables all" in lines[tables]
    for i in (tables, storage, counts):
        assert "SUPABASE_URL=https://scratchref.supabase.co" in lines[i]  # the scratch project only
    assert "users=2 chats=40 files=7" in proc.stdout                    # Open WebUI counts shown
    assert any("rm -f owui-rehearsal" in ln for ln in lines)          # throwaway container removed
    assert any("volume rm" in ln for ln in lines)
