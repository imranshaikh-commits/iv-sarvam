#!/usr/bin/env python3
"""Nightly backup of the Supabase tables that cannot be re-derived.

WHY THIS EXISTS
---------------
The project is on the Supabase free tier: no point-in-time recovery and no
downloadable daily backups. Chunks can be rebuilt from the source bank, but
intake sessions, approved diagrams, generated proposals and 210 human asset
approvals cannot. The project has already idle-paused once.

This uses PostgREST with the service-role key the brain already has, so it
needs no database password and no new secret. Standard library only, so it runs
with the host's python3.

WHAT IT WRITES
--------------
<out>/<UTC timestamp>/<table>.jsonl.gz   one row per line
<out>/<UTC timestamp>/manifest.json      row count per table

proposal_chunks (173 MB, mostly embeddings) is skipped unless
--with-proposal-chunks is given; run that weekly, not nightly.
Storage buckets (rendered diagrams, generated DOCX) are NOT covered.

USAGE (on the EC2 host)
-----
python3 scripts/backup_tables.py --env-file scripts/sarvam.env
python3 scripts/backup_tables.py --env-file scripts/sarvam.env --with-proposal-chunks
python3 scripts/backup_tables.py --env-file scripts/sarvam.env \\
    --restore ~/sarvam-backups/20260925T020000Z --tables intake_sessions

Restore upserts on the primary key: rows present in the backup are written
back, rows created since are left alone. It never deletes.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

TABLES = [
    "organizations", "org_members", "profiles",
    "intake_sessions", "architecture_diagrams", "generated_proposals",
    "proposals", "visual_assets",
    "partner_products", "partner_product_chunks", "partner_product_assets",
]
PRIMARY_KEYS = {"org_members": "org_id,user_id", "profiles": "user_id"}
PAGE = 1000          # PostgREST's default max-rows
KEEP = 14            # backup directories kept locally


def load_env(path: str | None) -> dict[str, str]:
    """SUPABASE_URL/SUPABASE_KEY from the process env, else from a KEY=VALUE file.
    Reads the file only; never writes it."""
    env = {k: os.environ[k] for k in ("SUPABASE_URL", "SUPABASE_KEY") if k in os.environ}
    if path:
        for line in Path(path).expanduser().read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.removeprefix("export ").split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("'\""))
    missing = [k for k in ("SUPABASE_URL", "SUPABASE_KEY") if not env.get(k)]
    if missing:
        sys.exit(f"missing {', '.join(missing)} (set them or pass --env-file)")
    return env


def _request(env, method, path, body=None, extra_headers=None):
    req = urllib.request.Request(
        env["SUPABASE_URL"].rstrip("/") + "/rest/v1/" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"apikey": env["SUPABASE_KEY"],
                 "Authorization": f"Bearer {env['SUPABASE_KEY']}",
                 "Content-Type": "application/json", **(extra_headers or {})})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read()
    return json.loads(raw) if raw else None


def fetch_table(env, table, fetch=_request):
    """All rows, paged in a stable primary-key order so no row is skipped or repeated."""
    order = ",".join(f"{c}.asc" for c in PRIMARY_KEYS.get(table, "id").split(","))
    rows, offset = [], 0
    while True:
        page = fetch(env, "GET", f"{table}?select=*&order={order}&limit={PAGE}&offset={offset}")
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def backup(env, out_root, tables, fetch=_request, now=None):
    stamp = (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    out = Path(out_root).expanduser() / stamp
    out.mkdir(parents=True)
    counts = {}
    for t in tables:
        rows = fetch_table(env, t, fetch)
        with gzip.open(out / f"{t}.jsonl.gz", "wt", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        counts[t] = len(rows)
        print(f"{t}: {len(rows)} rows")
    (out / "manifest.json").write_text(json.dumps({"taken_at": stamp, "rows": counts}, indent=2))
    return out, counts


def prune(out_root, keep=KEEP):
    dirs = sorted(d for d in Path(out_root).expanduser().iterdir() if d.is_dir())
    for d in dirs[:-keep]:
        shutil.rmtree(d)


def restore(env, backup_dir, tables, fetch=_request, batch=500):
    for t in tables:
        path = Path(backup_dir).expanduser() / f"{t}.jsonl.gz"
        with gzip.open(path, "rt", encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        for i in range(0, len(rows), batch):
            fetch(env, "POST", f"{t}?on_conflict={PRIMARY_KEYS.get(t, 'id')}", rows[i:i + batch],
                  {"Prefer": "resolution=merge-duplicates,return=minimal"})
        print(f"{t}: {len(rows)} rows upserted")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--env-file")
    ap.add_argument("--out", default="~/sarvam-backups")
    ap.add_argument("--tables", help="comma-separated; default: all irreplaceable tables")
    ap.add_argument("--with-proposal-chunks", action="store_true")
    ap.add_argument("--restore", metavar="BACKUP_DIR")
    a = ap.parse_args()
    env = load_env(a.env_file)
    tables = a.tables.split(",") if a.tables else TABLES + (
        ["proposal_chunks"] if a.with_proposal_chunks else [])
    if a.restore:
        if not a.tables:
            sys.exit("--restore needs an explicit --tables list")
        restore(env, a.restore, tables)
        return
    out, _ = backup(env, a.out, tables)
    prune(a.out)
    print(f"backup written to {out}")


if __name__ == "__main__":
    main()
