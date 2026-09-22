#!/usr/bin/env python3
"""Tests for extract_partner_product_images.py -- classification and manifest
loading only. Fetch, storage and Supabase writes are not exercised here.

Run: python3 scripts/test_extract_partner_product_images.py
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.argv = ["test"]
import extract_partner_product_images as E  # noqa: E402


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


# ---------------------------------------------------------------------------
# manifest loading -- ALL rows, not pre-filtered by URL suffix
# ---------------------------------------------------------------------------

def test_load_all_rows_does_not_filter_by_url_suffix(tmp_path):
    """The first version of this script filtered by url.endswith('.pdf') and
    silently missed IBM's extensionless download URL and Saviynt's URL with
    a trailing '?hsLang=en' query string -- 4 of 6 real PDFs instead of 6.
    load_all_rows must return every row; PDF-or-not is now decided later, by
    content-type after fetching."""
    p = _manifest(tmp_path, [
        {"vendor": "IBM", "source_url": "https://www.ibm.com/downloads/cas/POBY5B69"},
        {"vendor": "Saviynt", "source_url": "https://saviynt.com/x.pdf?hsLang=en"},
        {"vendor": "Okta", "source_url": "https://example.com/page.html"},
    ])
    rows = E.load_all_rows(p)
    assert len(rows) == 3


# ---------------------------------------------------------------------------
# classification -- heuristics only, no vision description available
# ---------------------------------------------------------------------------

def test_small_image_is_corporate():
    assert E.classify_asset(size=20_000, width=200, height=100, ocr="", vendor="Ping") == "corporate"


def test_wide_image_with_lots_of_ocr_text_is_product():
    ocr = " ".join(["word"] * 30)
    assert E.classify_asset(size=200_000, width=1200, height=600, ocr=ocr, vendor="Okta") == "product"


def test_vendor_named_in_ocr_text_is_product():
    ocr = ("Ping Identity dashboard showing active sessions policy rules "
          "applications users groups roles permissions audit log settings "
          "configuration overview summary status active inactive pending")
    assert len(ocr.split()) > 20
    assert E.classify_asset(size=200_000, width=600, height=1200, ocr=ocr, vendor="Ping Identity") == "product"


def test_no_signal_is_unknown_not_guessed_as_architecture():
    """No vision description exists in this cheap pass. Guessing 'architecture'
    on weak signal is the wrong direction -- that category carries the
    strongest reuse restriction, so an under-confident guess should land in
    'unknown' for human review, not the most-restricted bucket by accident."""
    assert E.classify_asset(size=500_000, width=800, height=600, ocr="", vendor="Oracle") == "unknown"


# ---------------------------------------------------------------------------
# the in-run dedup constant exists and is sane
# ---------------------------------------------------------------------------

def test_min_image_bytes_floor_matches_the_rest_of_the_pipeline():
    """Must match ingest_v2.py / extract_visual_assets.py's own floor so a
    bullet, rule or spacer graphic is never treated as a real asset here
    while being treated as noise everywhere else."""
    assert E.MIN_IMAGE_BYTES == 3000


if __name__ == "__main__":
    import inspect
    import tempfile
    import traceback

    tests = [(n, f) for n, f in list(globals().items())
             if n.startswith("test_") and callable(f)]
    passed = failed = 0
    for name, fn in tests:
        try:
            with tempfile.TemporaryDirectory() as td:
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
