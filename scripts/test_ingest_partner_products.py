#!/usr/bin/env python3
"""Tests for ingest_partner_products.py -- manifest validation, chunking and
HTML extraction only. Fetch, embedding and Supabase writes are not exercised
here; they need network access, credentials, and cost money.

Run: python3 scripts/test_ingest_partner_products.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.argv = ["test"]
import ingest_partner_products as I  # noqa: E402


def _manifest(tmp: Path, rows: list[dict]) -> Path:
    p = tmp / "m.csv"
    cols = ["vendor", "product_name", "capability", "doc_type", "source_url",
            "published_year", "notes"]
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})
    return p


def _row(**kw):
    base = {"vendor": "Ping Identity", "product_name": "PingFederate",
            "capability": "Access Management", "doc_type": "datasheet",
            "source_url": "https://example.com/x.pdf"}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# manifest loading
# ---------------------------------------------------------------------------

def test_manifest_requires_the_core_columns(tmp_path):
    p = tmp_path / "bad.csv"
    p.write_text("vendor,product_name\nPing,PingFederate\n")
    try:
        I.load_manifest(p)
        assert False, "should have raised on missing columns"
    except SystemExit:
        pass


def test_manifest_rejects_a_doc_type_not_in_the_db_constraint(tmp_path):
    """partner_products has doc_type = ANY (ARRAY['datasheet', ...]) --
    a typo here would fail at insert time, after a fetch has already run.
    Catch it at load time instead."""
    p = _manifest(tmp_path, [_row(doc_type="whitepaper")])
    try:
        I.load_manifest(p)
        assert False, "should have raised on an invalid doc_type"
    except SystemExit as e:
        assert "doc_type" in str(e)


def test_manifest_loads_valid_rows(tmp_path):
    p = _manifest(tmp_path, [_row(), _row(vendor="Okta", published_year="2023")])
    rows = I.load_manifest(p)
    assert len(rows) == 2
    assert rows[1].published_year == 2023


def test_vendor_filter_is_case_insensitive_exact_match(tmp_path):
    p = _manifest(tmp_path, [_row(vendor="Ping Identity"), _row(vendor="Okta")])
    rows = I.load_manifest(p, vendor="ping identity")
    assert len(rows) == 1 and rows[0].vendor == "Ping Identity"


# ---------------------------------------------------------------------------
# chunking -- same target/overlap discipline as the proposal corpus
# ---------------------------------------------------------------------------

def test_short_text_is_one_chunk():
    chunks = I.chunk_text("one two three", "Heading")
    assert len(chunks) == 1
    assert chunks[0].heading == "Heading"


def test_long_text_splits_with_overlap():
    words = " ".join(f"w{i}" for i in range(1000))
    chunks = I.chunk_text(words, "Heading", target_words=350, overlap=40)
    assert len(chunks) > 1
    assert chunks[0].heading == "Heading (part 1)"
    # overlap: the tail of chunk 1 reappears at the head of chunk 2
    tail = chunks[0].text.split()[-40:]
    head = chunks[1].text.split()[:40]
    assert tail == head


def test_empty_text_produces_no_chunks():
    assert I.chunk_text("", "Heading") == []


# ---------------------------------------------------------------------------
# HTML extraction -- stdlib-only tag stripper
# ---------------------------------------------------------------------------

def test_html_extraction_drops_script_and_style():
    html = ("<html><head><style>.x{color:red}</style></head><body>"
            "<script>var x = 1;</script><p>Real content here.</p>"
            "</body></html>")
    text = I.html_to_text(html)
    assert "Real content here." in text
    assert "color:red" not in text
    assert "var x" not in text


def test_html_extraction_collapses_whitespace():
    html = "<p>Line one</p>\n\n  <p>Line   two</p>"
    text = I.html_to_text(html)
    assert "  " not in text


# ---------------------------------------------------------------------------
# the min-word floor -- a bot-check or gated page must not look like content
# ---------------------------------------------------------------------------

def test_min_words_floor_is_enforced_as_a_constant():
    """Sprint 7. A gated/bot-blocked fetch (confirmed live: Okta's 'Unified
    View' datasheet, PingOne DaVinci) returns a short page of boilerplate,
    not the real document. process_row must refuse to ingest that as if it
    were content -- this asserts the floor exists and is not accidentally
    zero."""
    assert I.MIN_WORDS_TO_INGEST >= 40


if __name__ == "__main__":
    import tempfile
    import traceback

    tests = [(n, f) for n, f in list(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
                import inspect
                if "tmp_path" in inspect.signature(fn).parameters:
                    fn(Path(td))
                else:
                    fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
