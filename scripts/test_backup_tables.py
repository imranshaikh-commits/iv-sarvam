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


# --- audit 2026-10-12, finding 11 ---------------------------------------------

def test_restore_writes_parents_before_children_whatever_order_is_asked():
    """architecture_diagrams references generated_proposals and intake_sessions;
    restoring it first into an empty database fails the foreign key."""
    with tempfile.TemporaryDirectory() as tmp:
        for t in ("architecture_diagrams", "generated_proposals", "intake_sessions"):
            with gzip.open(Path(tmp) / f"{t}.jsonl.gz", "wt") as f:
                f.write(json.dumps({"id": 1}) + "\n")
        fetch, calls = _fake_db({})
        bt.restore(ENV, tmp, ["architecture_diagrams", "generated_proposals", "intake_sessions"],
                   fetch=fetch)
    assert [c[1].split("?")[0] for c in calls] == [
        "intake_sessions", "generated_proposals", "architecture_diagrams"]
    order = bt.TABLES
    assert order.index("generated_proposals") < order.index("architecture_diagrams")


def test_storage_mirror_walks_folders_and_fetches_only_what_changed():
    files = {"kit/a.png": b"AAAA", "kit/b.png": b"BB", "top.png": b"C"}
    fetched = []

    def call(env, method, path, body=None):
        if method == "POST":                                  # listing
            prefix = body["prefix"]
            names = {}
            for full, data in files.items():
                if not full.startswith(prefix):
                    continue
                rest = full[len(prefix):]
                head = rest.split("/")[0]
                names[head] = None if "/" in rest else len(data)
            return json.dumps([{"name": n, "id": None if size is None else "x",
                                "metadata": None if size is None else {"size": size}}
                               for n, size in sorted(names.items())]).encode()
        fetched.append(path)
        return files[path.split("/", 2)[2]]

    with tempfile.TemporaryDirectory() as tmp:
        counts = bt.mirror_storage(ENV, tmp, buckets=["visual-assets"], call=call)
        root = Path(tmp) / "storage" / "visual-assets"
        assert (root / "kit" / "a.png").read_bytes() == b"AAAA" and (root / "top.png").exists()
        assert counts == {"visual-assets": (3, 3)}
        fetched.clear()
        files["kit/b.png"] = b"BBBBBB"                        # one object changed
        assert bt.mirror_storage(ENV, tmp, buckets=["visual-assets"], call=call) == {
            "visual-assets": (3, 1)}
        assert fetched == ["object/visual-assets/kit/b.png"]
        # prune leaves the mirror alone
        (Path(tmp) / "20260101T000000Z").mkdir()
        bt.prune(tmp, keep=0)
        assert root.exists()


# --- F46: restore rehearsal into a scratch project ---------------------------

def test_tables_all_restores_every_table_in_the_backup_parents_first(tmp_path):
    for t in ("proposal_chunks", "architecture_diagrams", "intake_sessions"):
        with gzip.open(tmp_path / f"{t}.jsonl.gz", "wt") as f:
            f.write(json.dumps({"id": 1}) + "\n")
    assert bt.tables_in_backup(tmp_path) == ["intake_sessions", "architecture_diagrams",
                                             "proposal_chunks"]


def test_storage_files_go_back_into_their_buckets(tmp_path):
    (tmp_path / "diagram-renders" / "s1").mkdir(parents=True)
    (tmp_path / "diagram-renders" / "s1" / "d.png").write_bytes(b"png")
    (tmp_path / "generated-drafts").mkdir()
    (tmp_path / "generated-drafts" / "p.docx").write_bytes(b"docx")
    calls = []

    def put(env, method, path, data=None, headers=None):
        calls.append((method, path, data, headers or {}))
        return b"{}"

    counts = bt.restore_storage(ENV, tmp_path, ["diagram-renders", "generated-drafts"], call=put)
    assert counts == {"diagram-renders": 1, "generated-drafts": 1}
    buckets = [c for c in calls if c[1] == "bucket"]
    assert [json.loads(c[2])["id"] for c in buckets] == ["diagram-renders", "generated-drafts"]
    assert all(json.loads(c[2])["public"] is False for c in buckets)      # private, as in production
    uploads = {c[1]: c for c in calls if c[1].startswith("object/")}
    assert uploads["object/diagram-renders/s1/d.png"][2] == b"png"
    assert uploads["object/diagram-renders/s1/d.png"][3]["x-upsert"] == "true"


def test_the_comparison_puts_source_next_to_restored_for_every_table_and_bucket(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"rows": {"intake_sessions": 12, "proposals": 30}}))
    storage = tmp_path / "storage"
    (storage / "visual-assets").mkdir(parents=True)
    for i in range(3):
        (storage / "visual-assets" / f"{i}.png").write_bytes(b"x")
    restored = {"intake_sessions": 12, "proposals": 29}

    def count(env, table):
        return restored[table]

    def listing(env, bucket):
        return [("0.png", 1), ("1.png", 1), ("2.png", 1)]

    rows = bt.compare(ENV, tmp_path, storage, ["visual-assets"], count=count, listing=listing)
    assert rows == [("table", "intake_sessions", 12, 12), ("table", "proposals", 30, 29),
                    ("bucket", "visual-assets", 3, 3)]
    text = bt.format_comparison(rows)
    assert "proposals" in text and "30" in text and "29" in text and "MISMATCH" in text
    assert text.count("MISMATCH") == 1
