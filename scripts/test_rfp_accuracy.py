"""Offline check of rfp_accuracy.py: scoring, determinism, and an end-to-end
run on a synthetic fixture with a fake reader. No client content here."""
import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "brain"))
sys.path.insert(0, str(Path(__file__).parent))

import rfp_accuracy as A  # noqa: E402
import rfp_intake as R  # noqa: E402


def test_a_fact_is_right_only_when_every_expected_piece_is_there():
    exp = {"contains": ["5,000", "10,000", "50"]}
    assert A.score_fact("WIAM 5000; CIAM 10,000; privileged 50", exp)
    # "50" is not satisfied by the 50 inside 5,000: numbers match whole.
    assert not A.score_fact("WIAM 5,000; CIAM 10,000", exp)
    assert not A.score_fact(None, exp)
    assert A.score_fact("SaaS, hosted in KSA", {"contains": ["saas", "KSA"]})
    assert not A.score_fact("10,000 users including 50 privileged accounts",
                            {"contains": ["10,000"], "not_contains": ["users including"]})
    assert A.score_fact(None, {"missing": True})
    assert not A.score_fact("Out of scope: data migration", {"missing": True})


def test_a_tender_report_counts_critical_facts_and_names_the_unwritten():
    answers = {"population_by_domain": "WIAM 5,000", "envs": "Dev, Test, Prod"}
    expected = {"population_by_domain": {"contains": ["5,000", "10,000"]},
                "envs": {"contains": ["dev", "prod"]}}
    rep = A.score_tender(answers, expected)
    assert rep["right"] == 1 and rep["scored"] == 2
    assert rep["wrong"] == [{"field": "population_by_domain", "got": "WIAM 5,000",
                             "expected": {"contains": ["5,000", "10,000"]}}]
    critical = {fid for _, fid in R.CRITICAL_FACTS}
    assert set(rep["no_expected_value"]) == critical - set(expected)


def test_two_reads_are_compared_field_by_field():
    assert A.differences({"a": "1", "b": "2"}, {"a": "1", "b": "2"}) == []
    assert A.differences({"a": "1", "b": "2"}, {"a": "1", "c": "3"}) == ["b", "c"]


def test_a_fixture_folder_runs_end_to_end_with_a_fake_reader(tmp_path):
    (tmp_path / "tender.pdf").write_bytes(b"%PDF-1.7 synthetic")
    (tmp_path / "001-synthetic.json").write_text(json.dumps({
        "tender": "Synthetic tender", "file": "tender.pdf",
        "expected": {"envs": {"contains": ["dev", "prod"]},
                     "app_count": {"contains": ["6"]}}}))
    reads = []

    async def fake_read(pdf_path):
        reads.append(pdf_path)
        n = len(reads)
        return {"envs": "Dev and Prod", "app_count": "6" if n == 1 else "7"}, []

    results = asyncio.run(A.run(str(tmp_path), fake_read, twice=True))
    assert len(reads) == 2 and reads[0].endswith("tender.pdf")
    (r,) = results
    assert r["tender"] == "Synthetic tender" and r["right"] == 2 and r["scored"] == 2
    assert r["second_read_differs"] == ["app_count"]
    text = A.report(results)
    assert "Synthetic tender" in text and "2/2" in text and "app_count" in text


def test_the_fixture_folder_is_ignored_by_git():
    """Fixtures are client content. The rule must actually match."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    out = subprocess.run(["git", "check-ignore", "-v", "docs/evals/rfp/001-x.json"],
                         cwd=root, capture_output=True, text=True)
    assert out.returncode == 0 and ".gitignore" in out.stdout


def test_a_wrong_row_shows_the_expected_pieces_not_the_fixture_notes():
    """Fixtures carry the tender's own words ("source"); the report must not echo them."""
    results = [{"tender": "T", "scored": 1, "right": 0, "no_expected_value": [],
                "wrong": [{"field": "envs", "got": "Dev",
                           "expected": {"contains": ["dev", "prod"], "pages": [6],
                                        "source": "TENDER WORDS"}}]}]
    text = A.report(results)
    assert "TENDER WORDS" not in text and "'contains': ['dev', 'prod']" in text
    assert "p.6" in text


def test_a_value_that_drafting_would_read_as_saas_fails_a_not_saas_fact():
    """NWC leaves the model open: on-premises today, SaaS/PaaS/IaaS/hybrid may be
    considered. Naming the options is right; a value that drafting itself would
    take as a SaaS deployment (document_engine._looks_like_saas) is wrong."""
    exp = {"contains": ["premis", "saas", "paas", "iaas", "hybrid"], "not_saas": True}
    assert A.score_fact("On-premises today; SaaS, PaaS, IaaS or hybrid options may be "
                        "considered", exp)
    # Names every piece, yet drafting reads it as SaaS (the self-hosted terms are negated).
    wrong = "SaaS in KSA; PaaS and IaaS were considered; not on-premises; not hybrid"
    assert all(A._has(wrong, p) for p in exp["contains"])
    assert not A.score_fact(wrong, exp)


def test_pages_not_read_are_counted_and_reported(tmp_path):
    (tmp_path / "t.pdf").write_bytes(b"%PDF-1.7 synthetic")
    (tmp_path / "001.json").write_text(json.dumps({
        "tender": "T", "file": "t.pdf", "expected": {"envs": {"missing": True}}}))

    async def refused(path):
        return {}, [1, 2, 3]

    (r,) = asyncio.run(A.run(str(tmp_path), refused, twice=True))
    assert r["not_read_pages"] == [1, 2, 3] and r["second_read_not_read"] == [1, 2, 3]
    text = A.report([r])
    assert "NOT READ: 3 page(s)" in text and "p. 1, 2, 3" in text
    assert "two reads identical" not in text           # two empty reads prove nothing
