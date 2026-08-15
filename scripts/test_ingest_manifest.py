#!/usr/bin/env python3
"""Tests for manifest-driven selection in ingest_v2.

These cover SELECTION and METADATA only -- the parts that decide what enters
IV's voice bank. Extraction, embedding and Supabase writes are not exercised
here; they need credentials and cost money.

Run: OPENROUTER_API_KEY=x python3 scripts/test_ingest_manifest.py
"""
import csv
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OPENROUTER_API_KEY", "test")
sys.path.insert(0, str(Path(__file__).parent))
sys.argv = ["test"]
import ingest_v2 as I  # noqa: E402

COLUMNS = ["path", "filename", "tier", "reason", "iam_vendor", "client_name",
           "top_folder", "size_kb", "sha256_16", "proposal_type", "year",
           "review_note"]


def _manifest(tmp: Path, rows: list[dict]) -> Path:
    p = tmp / "m.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    return p


def _row(path, tier="ingest", **kw):
    return {"path": path, "filename": Path(path).name, "tier": tier,
            "sha256_16": kw.pop("sha", path[:16].ljust(16, "0")), **kw}


def test_only_the_ingest_tier_is_selected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [_row("a/good.docx"), _row("b/rfp.pdf", tier="testset"),
                _row("c/cv.pdf", tier="exclude"), _row("d/plan.pdf", tier="reference")]
        for r in rows:
            f = tmp / r["path"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        sel = I.load_manifest(_manifest(tmp, rows), tmp)
        assert [r.path for _, r in sel] == ["a/good.docx"], sel


def test_a_client_authored_rfp_can_never_be_selected():
    """The STC RFP sat in an IV folder and looked like an IV proposal."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [_row("STC/CD Identity Security Access Management CIAM v3.1.docx",
                     tier="testset")]
        f = tmp / rows[0]["path"]
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        assert I.load_manifest(_manifest(tmp, rows), tmp) == []


def test_missing_files_are_reported_not_silently_dropped(capsys=None):
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [_row("gone/missing.docx"), _row("here/present.docx")]
        f = tmp / "here/present.docx"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
        sel = I.load_manifest(_manifest(tmp, rows), tmp)
        assert len(sel) == 1


def test_vendor_filter_enables_batched_ingestion():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        rows = [_row("a/f.docx", iam_vendor="ForgeRock"),
                _row("b/o.docx", iam_vendor="Oracle"),
                _row("c/sb.docx", iam_vendor="SailPoint + BeyondTrust")]
        for r in rows:
            f = tmp / r["path"]
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        m = _manifest(tmp, rows)
        assert len(I.load_manifest(m, tmp, vendor="ForgeRock")) == 1
        # A combined-vendor row must appear in BOTH vendor batches, or it is
        # never ingested at all.
        assert len(I.load_manifest(m, tmp, vendor="SailPoint")) == 1
        assert len(I.load_manifest(m, tmp, vendor="BeyondTrust")) == 1


def test_a_manifest_missing_required_columns_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        p = tmp / "bad.csv"
        p.write_text("path,filename\na,b\n")
        try:
            I.load_manifest(p, tmp)
        except SystemExit as e:
            assert "tier" in str(e) and "sha256_16" in str(e)
        else:
            raise AssertionError("a malformed manifest was accepted")


def test_manifest_metadata_overrides_the_model():
    """Mannai -> Ahlibank. The model reads a cover page; the human read the doc."""
    p = I.Proposal(slug="s", source_filename="f.docx", file_type="docx")
    p.client_name, p.iam_vendor, p.proposal_type, p.year = "Mannai", "Ping", "implementation", 2021
    row = I.ManifestRow(path="p", filename="f.docx", tier="ingest", sha256_16="h",
                        client_name="Ahlibank", iam_vendor="Ping Identity",
                        proposal_type="migration", year="2023")
    overrides = I.apply_manifest_metadata(p, row)
    assert p.client_name == "Ahlibank"
    assert p.proposal_type == "migration"
    assert p.year == 2023
    assert len(overrides) == 4, overrides


def test_blank_manifest_fields_leave_the_model_value_intact():
    p = I.Proposal(slug="s", source_filename="f.docx", file_type="docx")
    p.client_name, p.iam_vendor = "DFCC Bank", "Ping"
    row = I.ManifestRow(path="p", filename="f.docx", tier="ingest", sha256_16="h",
                        client_name="", iam_vendor="", proposal_type="n/a", year="")
    assert I.apply_manifest_metadata(p, row) == []
    assert p.client_name == "DFCC Bank" and p.iam_vendor == "Ping"


def test_migration_is_a_valid_proposal_type():
    """The old LLM prompt could only emit implementation or mss; the curated
    bank has 39 migration files across 12 engagements."""
    p = I.Proposal(slug="s", source_filename="f.docx", file_type="docx")
    row = I.ManifestRow(path="p", filename="f.docx", tier="ingest", sha256_16="h",
                        proposal_type="migration")
    I.apply_manifest_metadata(p, row)
    assert p.proposal_type == "migration"


def test_real_reviewed_manifest_selects_exactly_the_ingest_tier():
    """Against the actual reviewed CSV, when it is present."""
    real = Path("/mnt/user-data/uploads/corpus_manifest_reviewed_1.csv")
    root = Path("/tmp/fakeroot")
    if not (real.exists() and root.is_dir()):
        return
    sel = I.load_manifest(real, root)
    assert len(sel) == 129, f"expected 129 ingest rows, got {len(sel)}"
    paths = " ".join(r.path.lower() for _, r in sel)
    for banned in ("tp-exim", "resume", "nda-ddp", "messaging guide",
                   "cd identity security", "q-654864"):
        assert banned not in paths, f"{banned!r} survived into the ingest set"


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            passed += 1
    print(f"ALL {passed} INGEST MANIFEST TESTS PASSED")
