#!/usr/bin/env python3
"""Tests for RFP/SOW extraction.

Built against a real 20-page Saudi IAM tender (ESNAD, Saudi Mining Services
Company), which turned out to be a raster PDF: 468 characters of extractable
text across 20 pages, all of them page numbers.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rfp_intake as R  # noqa: E402

# Verbatim from the ESNAD tender.
ESNAD = [
    "QMS630/015-00 Page 1 of 20",
    "",
    "Annexure 1: Scope of Work\n"
    "Saudi Mining Services Company (ESNAD) is embarking on this initiative",
    "Vendor Qualifications\n"
    "Responding vendors shall demonstrate the following minimum qualifications:\n"
    "- Minimum five (5) years of proven IAM delivery experience, with at least "
    "three (3) implementations of comparable scope\n"
    "- Active partner certification from the proposed IAM platform vendor\n"
    "- ISO/IEC 27001 certified delivery organization, with documented practices\n"
    "- Three (3) reference customers with deployments of similar scale\n"
    "- Local presence in the Kingdom of Saudi Arabia, or a committed partner",
    "ILM-01 Automated provisioning, onboarding with optional registration forms\n"
    "ILM-02 Automated deprovisioning, offboarding on termination\n"
    "AM-03 FIDO2 / hardware token support for privileged and high-risk roles\n"
    "AM-04 Risk-based conditional access policies cover location and device\n"
    "PAM-01 Credential vaulting for all privileged and service accounts\n"
    "ISO 27001 certified delivery organization\n"
    "QMS630/015-00 Page 5 of 20",
]


def test_a_raster_pdf_is_detected_before_extraction():
    """THE guard. The ESNAD SOW yielded 468 chars across 20 pages -- all page
    numbers -- and both pdftotext and pypdf returned nothing else. Without this
    check the extractor runs happily on an empty string and reports that the
    document contains no requirements."""
    assert R.needs_vision(["", "Page 2 of 20", "Page 3 of 20"])
    assert R.needs_vision([])


def test_a_real_text_layer_is_not_sent_to_vision():
    """Vision on 20 pages costs real time and tokens; don't pay it needlessly."""
    assert not R.needs_vision(ESNAD)


def test_every_numbered_requirement_is_captured():
    """A missed requirement is a lost mark in evaluation."""
    refs = [r.ref for r in R.extract_requirements(ESNAD)]
    for expected in ("ILM-01", "ILM-02", "AM-03", "AM-04", "PAM-01"):
        assert expected in refs, f"{expected} missed: got {refs}"


def test_standards_are_not_mistaken_for_requirements():
    """"ISO 27001" has the same shape as a requirement reference."""
    refs = [r.ref for r in R.extract_requirements(ESNAD)]
    assert not [r for r in refs if r.startswith("ISO")], refs


def test_page_furniture_is_not_mistaken_for_a_requirement():
    """Every ESNAD page is headed "QMS630/015-00 Page N of 20", which matches
    the requirement-reference shape exactly."""
    refs = [r.ref for r in R.extract_requirements(ESNAD)]
    assert not [r for r in refs if r.startswith("QMS")], refs


def test_requirements_carry_their_page():
    reqs = R.extract_requirements(ESNAD)
    assert all(r.page for r in reqs)
    assert next(r for r in reqs if r.ref == "AM-03").page == 5


def test_mandatory_qualifications_are_surfaced():
    """Failing one disqualifies the bid. Two minutes here saves two days of
    writing a proposal that cannot be accepted."""
    gates = R.extract_eligibility_gates(ESNAD)
    joined = " ".join(g.text for g in gates).lower()
    assert "five (5) years" in joined
    assert "iso/iec 27001" in joined
    assert "local presence" in joined


def test_gate_extraction_does_not_run_away():
    """Every "shall" in a 20-page tender must not become a gate."""
    noisy = ["The vendor shall provide training. " * 60]
    assert len(R.extract_eligibility_gates(noisy)) <= 20


def test_a_mandated_response_structure_overrides_the_house_template():
    """A response that ignores a mandated format is marked down or rejected."""
    pages = ["The proposal shall contain the following sections:\n"
             "1. Executive Summary\n2. Technical Approach\n"
             "3. Implementation Plan\n4. Commercial Response"]
    got = R.extract_mandated_structure(pages)
    assert got == ["Executive Summary", "Technical Approach",
                   "Implementation Plan", "Commercial Response"]


def test_no_structure_stated_means_no_override():
    assert R.extract_mandated_structure(ESNAD) == []


def test_ivs_own_decisions_are_not_reported_as_extraction_failures():
    """An RFP is vendor-neutral BY DESIGN. iam_vendor being absent is expected,
    and calling it a failure would train the user to ignore the gap list."""
    ex = R.RfpExtraction(
        fields=[R.ExtractedField("client_name", "ESNAD", 3)],
        pages_read=20, used_vision=True, source_name="SOW.pdf")
    report = R.describe_extraction(ex, 96)
    assert "IV's decisions, not the client's" in report
    assert "iam_vendor" in report
    assert "read visually" in report, "how it was read must be stated"


def test_every_extracted_value_shows_its_page():
    """Provenance is what makes the confirm step reviewable."""
    ex = R.RfpExtraction(
        fields=[R.ExtractedField("deployment_model", "SaaS hosted in KSA", 5)],
        pages_read=20, source_name="SOW.pdf")
    assert "_(p.5)_" in R.describe_extraction(ex, 96)


def test_requirement_coverage_names_what_is_unanswered():
    reqs = R.extract_requirements(ESNAD)
    answered, missing = R.requirement_coverage(reqs, {"ILM-01", "AM-03"})
    assert answered == len(reqs) - len(missing)
    assert "PAM-01" in missing


def test_no_gates_means_no_gate_prompt():
    assert R.describe_gates([]) == ""


import os as _os, sys as _sys  # noqa: E402
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from _runner import run_tests  # noqa: E402

run_tests(globals(), "RFP INTAKE TESTS")
