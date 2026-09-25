"""Offline checks for backup_tables.py: paging, round trip, prune, env parsing."""
import datetime as dt
import gzip
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import backup_tables as bt  # noqa: E402

ENV = {"SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "k"}


def _fake_db(rows_by_table):
    calls = []

    def fetch(env, method, path, body=None, headers=None):
        calls.append((method, path, body, headers))
        if method == "POST":
            return None
        table, qs = path.split("?", 1)
        q = dict(p.split("=", 1) for p in qs.split("&"))
        off, lim = int(q["offset"]), int(q["limit"])
        return rows_by_table[table][off:off + lim]
    return fetch, calls


def test_pages_past_the_row_cap_without_losing_rows():
    rows = [{"id": i} for i in range(2 * bt.PAGE + 5)]
    fetch, calls = _fake_db({"intake_sessions": rows})
    assert bt.fetch_table(ENV, "intake_sessions", fetch) == rows
    assert len(calls) == 3
    assert "order=id.asc" in calls[0][1]


def test_composite_key_orders_by_both_columns():
    fetch, calls = _fake_db({"org_members": []})
    bt.fetch_table(ENV, "org_members", fetch)
    assert "order=org_id.asc,user_id.asc" in calls[0][1]


def test_backup_then_restore_round_trips_every_row():
    rows = [{"id": i, "answers": {"client": "Acé"}} for i in range(3)]
    fetch, calls = _fake_db({"intake_sessions": rows})
    with tempfile.TemporaryDirectory() as d:
        out, counts = bt.backup(ENV, d, ["intake_sessions"], fetch,
                                now=dt.datetime(2026, 9, 25, 2, tzinfo=dt.timezone.utc))
        assert out.name == "20260925T020000Z"
        assert counts == {"intake_sessions": 3}
        assert json.loads((out / "manifest.json").read_text())["rows"] == counts
        with gzip.open(out / "intake_sessions.jsonl.gz", "rt") as f:
            assert [json.loads(line) for line in f] == rows
        bt.restore(ENV, out, ["intake_sessions"], fetch, batch=2)
    posts = [c for c in calls if c[0] == "POST"]
    assert [len(c[2]) for c in posts] == [2, 1]
    assert posts[0][1] == "intake_sessions?on_conflict=id"
    assert "merge-duplicates" in posts[0][3]["Prefer"]


def test_prune_keeps_only_the_newest():
    with tempfile.TemporaryDirectory() as d:
        for i in range(5):
            (Path(d) / f"2026092{i}T000000Z").mkdir()
        bt.prune(d, keep=2)
        assert sorted(p.name for p in Path(d).iterdir()) == ["20260923T000000Z", "20260924T000000Z"]


def test_env_file_parsing_strips_quotes_and_export():
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write('# c\nexport SUPABASE_URL="https://y.supabase.co"\nSUPABASE_KEY=\'abc\'\nOTHER=1\n')
    # Process env wins over the file, so isolate from anything a test suite set.
    saved = {k: bt.os.environ.pop(k) for k in ("SUPABASE_URL", "SUPABASE_KEY")
             if k in bt.os.environ}
    try:
        env = bt.load_env(f.name)
    finally:
        bt.os.environ.update(saved)
        Path(f.name).unlink()
    assert env["SUPABASE_URL"] == "https://y.supabase.co" and env["SUPABASE_KEY"] == "abc"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("ALL BACKUP TESTS PASSED")
