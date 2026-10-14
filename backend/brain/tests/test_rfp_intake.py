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


# ---------------------------------------------------------------------------
# Reading the attachment from Open WebUI's storage.
#
# OWUI extracts PDF text itself and sends the brain only that. For a raster
# tender that is nothing: the ESNAD SOW gave 468 characters across 20 pages,
# all page numbers. Confirmed by logging the payload shape -- no image_url, no
# file reference, just strings. So the brain reads the real file from OWUI's
# own upload directory, mounted read-only.
# ---------------------------------------------------------------------------

ALICE, BOB = "user-alice", "user-bob"
A_FILE = "a43c7f9c-ae47-47de-8c16-ac0d9c53f958"
B_FILE = "b289b94e-d909-4141-8087-6e0a2f01f0a7"


def _uploads_dir(tmp_path, monkeypatch, owners=None):
    """A temp uploads directory plus an Open WebUI file table saying who
    uploaded what (default: both test files are Alice's)."""
    import sqlite3
    monkeypatch.setattr(R, "OWUI_UPLOADS", str(tmp_path))
    db = tmp_path / "webui.db"
    con = sqlite3.connect(db)
    con.execute("create table file (id text primary key, user_id text, filename text)")
    for fid, uid in (owners or {A_FILE: ALICE, B_FILE: ALICE}).items():
        con.execute("insert into file values (?, ?, ?)", (fid, uid, "SOW.pdf"))
    con.commit(); con.close()
    monkeypatch.setattr(R, "OWUI_DB", str(db))
    return tmp_path


def test_a_missing_mount_is_visible_not_silent(monkeypatch):
    """A silently missing mount makes a deploy problem look like a bad
    document, which sends the user looking in the wrong place."""
    monkeypatch.setattr(R, "OWUI_UPLOADS", "/does/not/exist")
    assert R.uploads_available() is False
    assert R.pick_upload(user_id=ALICE) == (None, [], "")


def test_the_uuid_prefix_is_stripped_for_display(monkeypatch):
    assert R.display_name(
        "/owui-data/uploads/a43c7f9c-ae47-47de-8c16-ac0d9c53f958_SOW.pdf") == "SOW.pdf"
    assert R.display_name("/owui-data/uploads/plain.pdf") == "plain.pdf"


def test_a_single_recent_upload_is_chosen(tmp_path, monkeypatch):
    _uploads_dir(tmp_path, monkeypatch)
    (tmp_path / "a43c7f9c-ae47-47de-8c16-ac0d9c53f958_SOW.pdf").write_text("x")
    path, ambiguous, _ = R.pick_upload(user_id=ALICE)
    assert path and R.display_name(path) == "SOW.pdf"
    assert ambiguous == []


def test_two_uploads_seconds_apart_are_not_guessed_between(tmp_path, monkeypatch):
    """Reading the WRONG tender is far worse than one extra question. Two
    copies of SOW.pdf already exist from testing."""
    _uploads_dir(tmp_path, monkeypatch)
    (tmp_path / "a43c7f9c-ae47-47de-8c16-ac0d9c53f958_SOW.pdf").write_text("x")
    (tmp_path / "b289b94e-d909-4141-8087-6e0a2f01f0a7_SOW.pdf").write_text("x")
    path, ambiguous, _ = R.pick_upload(user_id=ALICE)
    assert path is None
    assert len(ambiguous) == 2


def test_stale_uploads_are_ignored(tmp_path, monkeypatch):
    """An RFP from last week is not the one just attached."""
    import os, time
    _uploads_dir(tmp_path, monkeypatch)
    old = tmp_path / "old_SOW.pdf"
    old.write_text("x")
    stale = time.time() - 86400
    os.utime(old, (stale, stale))
    assert R.pick_upload(user_id=ALICE) == (None, [], "")


def test_non_documents_are_ignored(tmp_path, monkeypatch):
    """A pasted client logo is not an RFP."""
    _uploads_dir(tmp_path, monkeypatch)
    (tmp_path / "logo.png").write_text("x")
    assert R.pick_upload(user_id=ALICE) == (None, [], "")


def test_another_users_newer_upload_is_never_read(tmp_path, monkeypatch):
    """Audit 2026-10-12 finding 2: Alice's file 180 s old, Bob's 10 s old, and
    Alice's extraction read Bob's tender."""
    import os, time
    _uploads_dir(tmp_path, monkeypatch, owners={A_FILE: ALICE, B_FILE: BOB})
    alice = tmp_path / f"{A_FILE}_Alice_RFP.pdf"
    bob = tmp_path / f"{B_FILE}_Bob_Tender.pdf"
    alice.write_text("x"); bob.write_text("x")
    now = time.time()
    os.utime(alice, (now - 180, now - 180)); os.utime(bob, (now - 10, now - 10))
    path, ambiguous, reason = R.pick_upload(user_id=ALICE)
    assert path == str(alice) and not ambiguous and not reason
    assert R.pick_upload(user_id=BOB)[0] == str(bob)
    assert R.pick_upload(user_id="user-carol") == (None, [], "")


def test_without_identity_nothing_is_guessed(tmp_path, monkeypatch):
    _uploads_dir(tmp_path, monkeypatch)
    (tmp_path / f"{A_FILE}_SOW.pdf").write_text("x")
    assert R.pick_upload() == (None, [], R.UNBOUND)                 # no requester
    monkeypatch.setattr(R, "OWUI_DB", str(tmp_path / "missing.db"))
    assert R.pick_upload(user_id=ALICE) == (None, [], R.UNBOUND)    # no uploader record


def test_a_named_file_settles_two_of_my_own_uploads(tmp_path, monkeypatch):
    _uploads_dir(tmp_path, monkeypatch)
    (tmp_path / f"{A_FILE}_SOW.pdf").write_text("x")
    (tmp_path / f"{B_FILE}_Annex.pdf").write_text("x")
    path, ambiguous, _ = R.pick_upload(user_id=ALICE, name_hint="read annex.pdf please")
    assert path.endswith("_Annex.pdf") and not ambiguous


# ---------------------------------------------------------------------------
# THE bug from the first two live runs against ESNAD.
#
# Both runs read all 20 pages successfully (8 gates extracted on the first
# run, proving the model genuinely read real content) but landed 0 fields and
# 0 requirements. field_values was a loose dict[str, str] and requirements was
# list[dict] -- Instructor gives the model NO schema to enforce on either, so
# it was free to key a field as "Client Name" instead of "client_name", or a
# requirement as {"id": ..., "requirement_text": ...} instead of
# {"ref": ..., "text": ...}. Every value under a key that didn't match exactly
# was silently dropped. Gates worked because they are free text with no keys
# to get wrong.
# ---------------------------------------------------------------------------

def test_requirements_use_a_strict_schema_not_a_free_dict():
    ann = R._PageExtraction.model_fields["requirements"].annotation
    assert "_RequirementItem" in str(ann), (
        f"requirements is {ann}, not a strict per-item schema")


def test_gates_are_unaffected_by_the_schema_fix():
    """Gates are free text and were never broken -- confirming that only the
    keyed shapes needed fixing."""
    pe = R._PageExtraction(eligibility_gates=[
        "ISO/IEC 27001 certified delivery organization"])
    assert pe.eligibility_gates == ["ISO/IEC 27001 certified delivery organization"]


# ---------------------------------------------------------------------------
# The merge step: a later, FULLER page value must win over an earlier,
# thinner one -- not the first mention, which is what shipped originally.
#
# Found during Sprint 1 correctness work: a module (rfp_vision.py) already
# had this fix, fully written and tested, but was never wired into the live
# extract_rfp -- the exact "built but never wired" pattern this project has
# hit before. Ported the merge idea into extract_rfp directly rather than
# swap in the whole orphaned module, which also changes the rasterisation
# dependency (pypdfium2 vs the poppler-utils/pdftoppm path already in the
# Dockerfile) -- a real infra decision that deserves its own review, not a
# side effect of a correctness fix.
# ---------------------------------------------------------------------------

def test_extraction_prompt_labels_every_field_both_paths(tmp_path):
    """ESNAD's SLA severity matrix was never extracted in three runs: the page
    prompt listed bare ids ('post_sla'), so the model had to guess what each
    meant, and every draft invented an SLA contradicting the client's own. The
    prompt must carry each field's label, on BOTH the vision and text paths."""
    import asyncio
    seen = []

    async def spy(model, messages):
        seen.append(messages[0]["content"])
        return R._PageExtraction()

    img = tmp_path / "p.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    asyncio.run(R.extract_page_vision(str(img), ["post_sla"], spy))
    asyncio.run(R._extract_page_text("some page text", ["post_sla"], spy))
    assert len(seen) == 2
    for prompt in seen:
        assert "post_sla:" in prompt and "severity" in prompt, prompt[-300:]


def test_a_capped_read_says_so_in_the_summary():
    """Audit 2026-10-12 finding 7."""
    ex = R.RfpExtraction(source_name="Tender.pdf", pages_read=60, pages_total=84,
                         used_vision=True)
    text = R.describe_extraction(ex, 96)
    assert "only the first 60 of 84 pages" in text and "61-84" in text
    full = R.RfpExtraction(source_name="T.pdf", pages_read=20, pages_total=20)
    assert "INCOMPLETE" not in R.describe_extraction(full, 96)


def test_a_mandated_structure_is_shown_and_not_promised():
    """Deep audit F13: the review said "I will follow it instead of IV's house
    template" and then drafted in IV's template; the structure was never stored
    or used. The message now shows the list and says what Shilpi actually does."""
    ex = R.RfpExtraction(
        fields=[R.ExtractedField("client_name", "Acme", 1)],
        mandated_structure=["Company Profile", "Technical Approach", "Pricing Schedule B"],
        pages_read=3, pages_total=3)
    text = R.describe_extraction(ex, 96)
    for heading in ex.mandated_structure:
        assert heading in text
    assert "will follow" not in text.lower()
    assert "IV's own structure" in text and "reorder" in text.lower()


def test_a_long_requirement_is_not_cut_off(monkeypatch):
    """Deep audit F11: requirement text was cut at 600 characters on the way in,
    the same cut that once shipped "...achieves access to a" to a client, one
    step earlier. The client wrote these; they are scored line by line."""
    import asyncio
    import tempfile
    long_text = "The solution shall " + "provide audited delegated administration; " * 30
    assert 1000 < len(long_text.strip()) < R.MAX_REQUIREMENT_CHARS

    class _Req:
        ref, text = "IGA-07", long_text

    class _PageEx:
        field_values, eligibility_gates, structure_headings = [], [], []
        requirements = [_Req()]

    async def fake_structured(model, messages):
        return _PageEx()

    monkeypatch.setattr(R, "read_text_layer", lambda path: ["A page of tender text. " * 20])
    with tempfile.TemporaryDirectory() as tmp:
        ex = asyncio.run(R.extract_rfp("fake.pdf", "SOW.pdf", [], fake_structured, tmp))
    assert ex.requirements[0].text == long_text.strip()
    # The text-layer register reader keeps it whole too.
    line = "IGA-07 " + long_text.strip()
    assert R.extract_requirements([line])[0].text.endswith("administration;")


# --- Deep audit F12: a scanned page inside a text PDF -------------------------

def _mixed_pdf_run(monkeypatch, tmp_path, images_available=True):
    """Page 1 has a text layer; page 2 is a scan (no text). Returns the
    extraction and which pages were read as (page, 'text' | 'vision')."""
    import asyncio
    how = []

    class _Req:
        def __init__(self, ref, text):
            self.ref, self.text = ref, text

    class _PageEx:
        def __init__(self, reqs):
            self.field_values, self.eligibility_gates, self.structure_headings = [], [], []
            self.requirements = reqs

    async def fake_structured(model, messages):
        visual = isinstance(messages[1]["content"], list)
        how.append("vision" if visual else "text")
        return _PageEx([_Req("ANX-01", "Mandatory clause that exists only on the scanned annexure page")]
                       if visual else [_Req("AM-01", "Single sign-on for all workforce applications")])

    images = []
    for i in (1, 2):
        img = tmp_path / f"pg-{i}.png"
        img.write_bytes(b"\x89PNG fake")
        images.append(str(img))
    monkeypatch.setattr(R, "read_text_layer",
                        lambda path: ["Section 4. Access management requirements. " * 12, "  \n "])
    monkeypatch.setattr(R, "rasterise", lambda path, out: images if images_available else [])
    ex = asyncio.run(R.extract_rfp("mixed.pdf", "Tender.pdf", [], fake_structured, str(tmp_path)))
    return ex, how


def test_a_scanned_page_inside_a_text_pdf_is_read_visually(monkeypatch, tmp_path):
    """One average over the whole document decided text or vision, so a text
    PDF with scanned annexures was read as text and the annexures vanished,
    with no flag at all."""
    ex, how = _mixed_pdf_run(monkeypatch, tmp_path)
    assert how == ["text", "vision"]
    assert [(r.ref, r.page) for r in ex.requirements] == [("AM-01", 1), ("ANX-01", 2)]
    assert ex.vision_pages == [2] and ex.unread_pages == [] and not ex.used_vision
    assert "p. 2" in R.describe_extraction(ex, 96)          # says which page was a scan


def test_a_scanned_page_that_cannot_be_read_is_reported_not_skipped(monkeypatch, tmp_path):
    ex, how = _mixed_pdf_run(monkeypatch, tmp_path, images_available=False)
    assert how == ["text"] and ex.unread_pages == [2]
    text = R.describe_extraction(ex, 96)
    assert "NOT READ" in text and "page 2" in text.lower()


# --- Deep audit F16: tenders arrive as Word ----------------------------------

def test_a_word_tender_is_converted_and_a_text_file_is_refused_plainly(tmp_path, monkeypatch):
    """The upload picker accepted .docx .doc .txt .md .rtf, and the reader is
    pdftotext and pdftoppm: every one of them failed with "Reading that
    document failed". Word is converted with the LibreOffice already in the
    image; the rest are refused by name, before any work."""
    import export_engine
    seen = {}

    def fake_convert(data, timeout=120.0, suffix=".docx"):
        seen.update(bytes=data, suffix=suffix)
        return b"%PDF-1.7 converted", None

    monkeypatch.setattr(export_engine, "export_pdf", fake_convert)
    pdf = tmp_path / "abc_SOW.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert R.as_pdf(str(pdf), str(tmp_path)) == (str(pdf), "")       # a PDF is used as it is

    for name in ("abc_Tender.docx", "abc_Old Tender.DOC"):
        src = tmp_path / name
        src.write_bytes(b"word bytes")
        out, why = R.as_pdf(str(src), str(tmp_path))
        assert why == "" and out.endswith(".pdf") and open(out, "rb").read().startswith(b"%PDF")
        assert seen["bytes"] == b"word bytes" and seen["suffix"] == os.path.splitext(name)[1].lower()

    for name in ("notes.txt", "readme.md", "tender.rtf"):
        src = tmp_path / name
        src.write_text("x")
        out, why = R.as_pdf(str(src), str(tmp_path))
        assert out is None and "PDF or Word" in why and name in why

    # A conversion that fails is said, with the way out.
    monkeypatch.setattr(export_engine, "export_pdf", lambda *a, **k: (None, "soffice crashed"))
    out, why = R.as_pdf(str(tmp_path / "abc_Tender.docx"), str(tmp_path))
    assert out is None and "could not be converted" in why and "PDF" in why


def test_the_chat_reads_a_word_tender_through_the_converter(tmp_path, monkeypatch):
    """Caller side: run_rfp_extraction must hand extract_rfp the CONVERTED file."""
    import asyncio
    os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
    os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    import app
    import export_engine
    upload = tmp_path / "1b4e28ba-2fa1-11d2-883f-0016d3cca427_Tender.docx"
    upload.write_bytes(b"word bytes")
    read = {}

    async def spy_extract(pdf_path, source_name, field_ids, structured_fn, tmp):
        read.update(path=pdf_path, name=source_name, head=open(pdf_path, "rb").read(5))
        return R.RfpExtraction(fields=[R.ExtractedField("client_name", "Acme", 1)])

    monkeypatch.setattr(R, "pick_upload", lambda **k: (str(upload), [], ""))
    monkeypatch.setattr(R, "extract_rfp", spy_extract)
    monkeypatch.setattr(export_engine, "export_pdf", lambda *a, **k: (b"%PDF-1.7 x", None))
    ex, err = asyncio.run(app.run_rfp_extraction("use this RFP"))
    assert err == "" and ex is not None
    assert read["path"].endswith(".pdf") and read["head"] == b"%PDF-" and read["name"] == "Tender.docx"

    # A format that cannot be read is refused before extraction is attempted.
    notes = tmp_path / "1b4e28ba-2fa1-11d2-883f-0016d3cca427_notes.txt"
    notes.write_text("x")
    read.clear()
    monkeypatch.setattr(R, "pick_upload", lambda **k: (str(notes), [], ""))
    ex, err = asyncio.run(app.run_rfp_extraction("use this RFP"))
    assert ex is None and "PDF or Word" in err and read == {}


def test_a_requirement_repeated_on_another_page_is_listed_once(monkeypatch, tmp_path):
    """Deep audit F15: a requirement restated on a later page (a summary table,
    a compliance checklist) was listed again each time. Repeats use up the
    budget of requirements that get assessed and pad the matrix."""
    import asyncio

    class _Req:
        def __init__(self, ref, text):
            self.ref, self.text = ref, text

    class _PageEx:
        def __init__(self, reqs):
            self.field_values, self.eligibility_gates, self.structure_headings = [], [], []
            self.requirements = reqs

    pages = iter([
        [_Req("AM-01", "Single sign-on for all workforce applications."),
         _Req("AM-02", "Adaptive MFA for remote access")],
        [_Req("am-01", "single  sign-on for all workforce applications"),     # the same, restated
         _Req("AM-02", "Adaptive MFA for privileged administrators")],        # same ref, NOT the same
    ])

    async def fake_structured(model, messages):
        return _PageEx(next(pages))

    monkeypatch.setattr(R, "read_text_layer", lambda path: ["tender text " * 30, "tender text " * 30])
    ex = asyncio.run(R.extract_rfp("x.pdf", "SOW.pdf", [], fake_structured, str(tmp_path)))
    assert [(r.ref, r.page) for r in ex.requirements] == [
        ("AM-01", 1), ("AM-02", 1), ("AM-02", 2)]
    assert "privileged administrators" in ex.requirements[2].text


# --- Design 1a: three kinds of intake field ----------------------------------

def test_every_intake_field_has_one_kind():
    import intake_template as T
    kinds = {q["id"]: q.get("kind") for q in T.iter_questions(None)}
    assert set(kinds.values()) <= {"client_fact", "iv_decision", "derived"}
    for fid in ("iam_vendor", "proposal_type", "proposal_depth", "pricing_model"):
        assert kinds[fid] == "iv_decision", fid
    assert kinds["user_count"] == "client_fact" and kinds["envs"] == "client_fact"
    assert T.field_kind("required_diagram_types") == "derived"
    assert T.field_kind("diagram_count") == "derived"
    assert "proposal_type" not in T.client_fact_ids()


def test_the_chat_asks_the_reader_for_client_facts_only(tmp_path, monkeypatch):
    """Caller side: run_rfp_extraction must not ask the model for IV's decisions."""
    import asyncio
    os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
    os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    import app
    import intake_template as T
    upload = tmp_path / "1b4e28ba-2fa1-11d2-883f-0016d3cca427_SOW.pdf"
    upload.write_bytes(b"%PDF-1.7 x")
    asked = {}

    async def spy_extract(pdf_path, source_name, field_ids, structured_fn, tmp):
        asked["ids"] = list(field_ids)
        return R.RfpExtraction(fields=[R.ExtractedField("client_name", "Acme", 1)])

    monkeypatch.setattr(R, "pick_upload", lambda **k: (str(upload), [], ""))
    monkeypatch.setattr(R, "extract_rfp", spy_extract)
    asyncio.run(app.run_rfp_extraction("use this RFP"))
    assert asked["ids"] == T.client_fact_ids()
    assert "proposal_type" not in asked["ids"] and "required_diagram_types" not in asked["ids"]


def test_ivs_decisions_are_asked_once_with_their_choices():
    """Design 1a: asked once, in one message, with the valid choices listed."""
    import intake_template as T
    import proposal_templates
    assert set(T.PROPOSAL_TYPES) == set(proposal_templates.VALID_PROPOSAL_TYPES)
    ex = R.RfpExtraction(fields=[R.ExtractedField("client_name", "ESNAD", 3)],
                         pages_read=20, source_name="SOW.pdf")
    report = R.describe_extraction(ex, 96)
    block = report[report.index("IV's decisions"):]
    assert "**proposal_type**" in block and "implementation, mss, migration" in block
    assert "**proposal_depth**" in block and "brief, standard, full" in block
    assert "**pricing_model**" in block and "fixed, t&m" in block
    assert "**iam_vendor**" in block
    assert report.count("IV's decisions") == 1


def test_a_decision_outside_its_choices_is_not_an_answer():
    import intake_template as T
    answers = {"iam_vendor": "SailPoint", "proposal_type": "SOW",
               "proposal_depth": "full", "pricing_model": "fixed"}
    assert T.unanswered_decisions(answers) == ["proposal_type"]
    assert T.unanswered_decisions({**answers, "proposal_type": "Implementation"}) == []
    assert T.unanswered_decisions({}) == list(T.ASKED_DECISIONS)


# --- Design 1c: same file, same answers --------------------------------------

def _sample_extraction():
    return R.RfpExtraction(
        fields=[R.ExtractedField("user_count", "5,000 workforce", 4)],
        requirements=[R.Requirement("AM-04", "Risk-based access", "Access", 7)],
        gates=[R.EligibilityGate("Five years of IAM delivery", 3)],
        mandated_structure=["Executive Summary", "Approach", "Pricing"],
        pages_read=20, pages_total=20, used_vision=True, source_name="SOW.pdf",
        vision_pages=[2], unread_pages=[9])


def test_a_stored_reading_comes_back_identical():
    ex = _sample_extraction()
    back = R.RfpExtraction.from_dict(ex.to_dict())
    assert back == ex


def test_the_same_file_uploaded_again_reuses_its_reading(tmp_path, monkeypatch):
    """No model call, identical answers. A new extractor version reads it fresh."""
    import asyncio
    os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
    os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
    os.environ.setdefault("SUPABASE_KEY", "test-key")
    import app
    store, reads = {}, []

    async def fake_get(client, sha256, version):
        return store.get((sha256, version))

    async def fake_save(client, sha256, version, reading):
        store[(sha256, version)] = reading
        return True

    async def fake_extract(pdf_path, source_name, field_ids, structured_fn, tmp):
        reads.append(source_name)
        return _sample_extraction()

    first = tmp_path / "1b4e28ba-2fa1-11d2-883f-0016d3cca427_SOW.pdf"
    again = tmp_path / "2c5f39cb-2fa1-11d2-883f-0016d3cca427_SOW copy.pdf"
    first.write_bytes(b"%PDF-1.7 same bytes")
    again.write_bytes(b"%PDF-1.7 same bytes")
    monkeypatch.setattr(app.supabase_client, "get_rfp_reading", fake_get)
    monkeypatch.setattr(app.supabase_client, "save_rfp_reading", fake_save)
    monkeypatch.setattr(R, "extract_rfp", fake_extract)

    monkeypatch.setattr(R, "pick_upload", lambda **k: (str(first), [], ""))
    one, err = asyncio.run(app.run_rfp_extraction("use this RFP"))
    monkeypatch.setattr(R, "pick_upload", lambda **k: (str(again), [], ""))
    two, err2 = asyncio.run(app.run_rfp_extraction("use this RFP"))
    assert err == err2 == "" and reads == ["SOW.pdf"]
    assert two.answers() == one.answers() and two.requirements == one.requirements
    assert two.source_name == "SOW copy.pdf"                  # named as uploaded this time

    monkeypatch.setattr(R, "EXTRACTOR_VERSION", R.EXTRACTOR_VERSION + "-next")
    asyncio.run(app.run_rfp_extraction("use this RFP"))
    assert len(reads) == 2


def test_rfp_readings_are_stored_under_hash_and_version():
    import asyncio
    import supabase_client as S
    sent = []

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self._body

    class _Client:
        async def get(self, url, **kw):
            sent.append(("get", url, kw.get("params")))
            return _Resp([{"reading": {"pages_read": 3}}])

        async def post(self, url, **kw):
            sent.append(("post", url, kw.get("params"), kw.get("json"), kw["headers"].get("Prefer")))
            return _Resp([{}])

    got = asyncio.run(S.get_rfp_reading(_Client(), "ab" * 32, "v1"))
    assert got == {"pages_read": 3}
    assert sent[0][1].endswith("/rest/v1/rfp_readings")
    assert sent[0][2]["sha256"] == "eq." + "ab" * 32 and sent[0][2]["extractor_version"] == "eq.v1"
    assert asyncio.run(S.save_rfp_reading(_Client(), "ab" * 32, "v1", {"pages_read": 3})) is True
    assert sent[1][3] == {"sha256": "ab" * 32, "extractor_version": "v1",
                          "reading": {"pages_read": 3}}
    assert "resolution=merge-duplicates" in sent[1][4]


def test_the_rfp_readings_migration_keeps_row_level_security_on():
    import pytest
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "supabase",
                        "migrations", "sarvam_023_rfp_readings.sql")
    if not os.path.exists(path):
        # The brain image carries backend/brain only; CI's repo job runs this.
        pytest.skip("migrations are not in the brain image")
    sql = open(path).read().lower()
    assert "create table if not exists public.rfp_readings" in sql
    assert "enable row level security" in sql
    assert "primary key (sha256, extractor_version)" in sql


# --- Design 1b: two-pass reading with evidence --------------------------------

import re  # noqa: E402

_FILLER = "General terms and conditions of the tender apply to this section. "


def _two_pass_run(monkeypatch, text_pages, page_quotes, field_ids=None, combine=None,
                  vision=None, tmp_path=None):
    """Runs the real extract_rfp. page_quotes: {page: [(field_id, quote)]}; a
    text page returns only the quotes present in the part it was sent. vision:
    {page: transcription}, every page then read as an image."""
    import asyncio
    calls = {"pages": [], "combine": [], "prompts": []}

    async def fake(model, messages):
        if model is R._FieldReading:
            calls["combine"].append(messages[1]["content"])
            if combine:
                return combine(messages)
            fid = messages[0]["content"].split("Field: ", 1)[1].split(":", 1)[0]
            pages = sorted({int(p) for p in re.findall(r"\(p\.(\d+)\)", messages[1]["content"])})
            return R._FieldReading(value=f"STATED {fid}", pages=pages)
        calls["prompts"].append(messages[0]["content"])
        content = messages[1]["content"]
        if isinstance(content, list):
            page_no = sum(1 for c in calls["pages"] if c == "image") + 1
            calls["pages"].append("image")
            return R._PageExtraction(
                transcription=vision.get(page_no, ""),
                quotes=[R._FieldQuote(field_id=f, quote=q) for f, q in page_quotes.get(page_no, [])])
        page_no = next(i + 1 for i, t in enumerate(text_pages) if content in t)
        calls["pages"].append(page_no)
        return R._PageExtraction(quotes=[
            R._FieldQuote(field_id=f, quote=q) for f, q in page_quotes.get(page_no, [])
            if q in content])

    monkeypatch.setattr(R, "read_text_layer", lambda path: text_pages)
    if vision:
        images = []
        for i in range(1, len(vision) + 1):
            img = tmp_path / f"pg-{i}.png"
            img.write_bytes(b"\x89PNG fake")
            images.append(str(img))
        monkeypatch.setattr(R, "needs_vision", lambda pages: True)
        monkeypatch.setattr(R, "rasterise", lambda path, out: images)
    ex = asyncio.run(R.extract_rfp("x.pdf", "SOW.pdf", field_ids or ["population_by_domain"],
                                   fake, str(tmp_path or "/tmp/none")))
    return ex, calls


def test_an_rfp_that_says_sow_cannot_set_proposal_type(monkeypatch, tmp_path):
    """Run 1: proposal_type came back "SOW", which the database rejects. IV's
    decisions and derived fields are never taken from a client document, even
    when a caller asks for them and the model offers them."""
    import intake_template as T
    page = ("This Statement of Work (SOW) covers the identity programme. "
            "The organisation has 5,000 workforce users. Diagrams: deployment view. ") * 3
    quotes = {1: [("proposal_type", "This Statement of Work (SOW) covers the identity programme."),
                  ("required_diagram_types", "Diagrams: deployment view."),
                  ("user_count", "The organisation has 5,000 workforce users.")]}
    ex, calls = _two_pass_run(monkeypatch, [page], quotes, tmp_path=tmp_path,
                              field_ids=[q["id"] for q in T.iter_questions(None)])
    assert ex.answers() == {"user_count": "STATED user_count"}
    assert "  proposal_type:" not in calls["prompts"][0]            # never even asked


def test_facts_spread_over_pages_are_combined_with_their_pages(monkeypatch, tmp_path):
    """Run 1 lost population_by_domain: WIAM and CIAM on one page, privileged
    accounts on another, and the per-page merge kept one page's answer."""
    pages = [_FILLER * 5] * 3 + [
        _FILLER * 3 + "Workforce users (WIAM): 5,000. Customer identities (CIAM): 10,000. ",
        _FILLER * 5, _FILLER * 5,
        _FILLER * 3 + "Privileged accounts in scope for PAM: 50 accounts. "]
    quotes = {4: [("population_by_domain",
                   "Workforce users (WIAM): 5,000. Customer identities (CIAM): 10,000.")],
              7: [("population_by_domain", "Privileged accounts in scope for PAM: 50 accounts.")]}
    ex, calls = _two_pass_run(
        monkeypatch, pages, quotes, tmp_path=tmp_path,
        combine=lambda m: R._FieldReading(value="WIAM 5,000; CIAM 10,000; privileged 50",
                                          pages=[4, 7, 99]))
    field = next(f for f in ex.fields if f.field_id == "population_by_domain")
    assert field.value == "WIAM 5,000; CIAM 10,000; privileged 50"
    assert field.pages == [4, 7] and field.page == 4              # 99 is no quote's page
    assert [q["page"] for q in field.quotes] == [4, 7]
    assert len(calls["combine"]) == 1                              # one call for the field
    assert "(p.4)" in calls["combine"][0] and "(p.7)" in calls["combine"][0]


def test_the_reader_checks_each_quote_against_the_page_itself(monkeypatch, tmp_path):
    """Not trusting the model: invented quotes and changed figures are dropped."""
    import asyncio
    page = _FILLER * 3 + "The workforce has 5,000 named users across three sites. "

    async def fake(model, messages):
        if model is R._FieldReading:
            return R._FieldReading(value="5,000 named users", pages=[1])
        return R._PageExtraction(quotes=[
            R._FieldQuote(field_id="user_count",
                          quote="The workforce has 9,000 named users across three sites."),
            R._FieldQuote(field_id="app_count",
                          quote="Forty business applications are to be onboarded"),
            R._FieldQuote(field_id="user_count",
                          quote="The workforce has 5,000 named users across three sites.")])

    monkeypatch.setattr(R, "read_text_layer", lambda path: [page])
    ex = asyncio.run(R.extract_rfp("x.pdf", "SOW.pdf", ["user_count", "app_count"], fake,
                                   str(tmp_path)))
    assert ex.answers() == {"user_count": "5,000 named users"}
    assert ex.fields[0].quotes == [
        {"page": 1, "text": "The workforce has 5,000 named users across three sites."}]
    assert ex.dropped_quotes == [{"page": 1, "field_id": "user_count"},
                                 {"page": 1, "field_id": "app_count"}]


def test_a_field_with_no_grounded_quote_is_not_found(monkeypatch, tmp_path):
    ex, calls = _two_pass_run(monkeypatch, [_FILLER * 5], {}, tmp_path=tmp_path)
    assert ex.fields == [] and calls["combine"] == []


def test_a_dense_page_is_read_in_parts_and_none_of_it_goes_unread(monkeypatch, tmp_path):
    """Audit F11 remainder: each page was cut at 6,000 characters."""
    fact = "Customer identities (CIAM): 10,000 registered customers."
    page = (_FILLER + "\n") * 220 + fact + "\n"
    assert len(page) > 2 * R.PAGE_PART_CHARS
    ex, calls = _two_pass_run(monkeypatch, [page], {1: [("population_by_domain", fact)]},
                              tmp_path=tmp_path)
    assert calls["pages"].count(1) == len(R.page_parts(page)) >= 3
    assert "".join(R.page_parts(page)) == page
    assert ex.answers() == {"population_by_domain": "STATED population_by_domain"}


def test_a_scanned_page_is_checked_against_its_transcription(monkeypatch, tmp_path):
    vision = {1: _FILLER + "Applications in scope: 6 business applications.",
              2: _FILLER * 2}
    quotes = {1: [("app_count", "Applications in scope: 6 business applications.")],
              2: [("user_count", "Total users: 20,000 identities in the directory")]}
    ex, _ = _two_pass_run(monkeypatch, ["", ""], quotes, field_ids=["app_count", "user_count"],
                          vision=vision, tmp_path=tmp_path)
    assert [f.field_id for f in ex.fields] == ["app_count"]
    assert ex.dropped_quotes == [{"page": 2, "field_id": "user_count"}]


def test_if_stating_a_field_fails_its_own_quotes_stand(monkeypatch, tmp_path):
    def broken(messages):
        raise RuntimeError("model down")
    page = _FILLER * 3 + "Environments: Development, Test and Production. "
    ex, _ = _two_pass_run(monkeypatch, [page],
                          {1: [("envs", "Environments: Development, Test and Production.")]},
                          field_ids=["envs"], combine=broken, tmp_path=tmp_path)
    assert ex.answers() == {"envs": "Environments: Development, Test and Production."}


def test_a_stated_skip_or_na_is_not_a_value(monkeypatch, tmp_path):
    page = _FILLER * 3 + "Number of applications: to be confirmed by the client. "
    ex, _ = _two_pass_run(monkeypatch, [page],
                          {1: [("app_count", "Number of applications: to be confirmed by the client.")]},
                          field_ids=["app_count"], tmp_path=tmp_path,
                          combine=lambda m: R._FieldReading(value="N/A", pages=[1]))
    assert ex.fields == []


def test_quotes_and_values_use_strict_schemas():
    ann = R._PageExtraction.model_fields["quotes"].annotation
    assert "_FieldQuote" in str(ann)
    assert set(R._FieldReading.model_fields) == {"value", "pages"}
    assert R._match_field("Client Name", ["client_name"]) == "client_name"
    assert R._match_field("not_a_real_field", ["client_name"]) is None   # dropped, not guessed


# --- Design 1d: one-screen confirmation ---------------------------------------

def test_the_review_shows_the_critical_facts_first_with_pages_and_names_the_missing():
    ex = R.RfpExtraction(
        fields=[R.ExtractedField("industry", "Mining", 2, pages=[2]),
                R.ExtractedField("population_by_domain", "WIAM 5,000; CIAM 10,000; privileged 50",
                                 4, pages=[4, 7]),
                R.ExtractedField("deployment_model", "SaaS hosted in KSA", 5, pages=[5, 12])],
        mandated_structure=["Executive Summary", "Technical Approach", "Pricing"],
        pages_read=20, pages_total=20, source_name="SOW.pdf")
    text = R.describe_extraction(ex, 96)
    critical = text[text.index("Critical facts"):]
    assert "WIAM 5,000; CIAM 10,000; privileged 50" in critical and "p.4, 7" in critical
    assert "SaaS hosted in KSA" in critical and "p.5, 12" in critical
    for label, _fid in R.CRITICAL_FACTS:
        assert label in critical, label
    assert critical.count("**missing**") == len(R.CRITICAL_FACTS) - 2
    assert text.index("Critical facts") < text.index("Executive Summary") < text.index("Mining")


def test_a_reread_never_overwrites_what_the_consultant_corrected():
    ex = R.RfpExtraction(fields=[
        R.ExtractedField("envs", "Dev, Test, Prod", 3, pages=[3],
                         quotes=[{"page": 3, "text": "Environments: Dev, Test, Prod."}]),
        R.ExtractedField("app_count", "6", 4, pages=[4],
                         quotes=[{"page": 4, "text": "Six applications are in scope."}])])
    out = R.answers_to_store(ex, {"envs": "Dev, UAT, Prod", R.CONSULTANT_KEY: ["envs"]})
    assert "envs" not in out and out["app_count"] == "6"
    assert out[R.EVIDENCE_KEY] == {"app_count": {
        "pages": [4], "quotes": [{"page": 4, "text": "Six applications are in scope."}]}}



def test_the_review_names_the_pages_whose_quotes_were_dropped():
    """A quote the reader could not find on its page is dropped, never used;
    the consultant is told which pages to check, since a fact stated there
    may be missing from the list."""
    ex = R.RfpExtraction(
        fields=[R.ExtractedField("envs", "Dev, Test, Prod", 3, pages=[3])],
        dropped_quotes=[{"page": 7, "field_id": "user_count"},
                        {"page": 4, "field_id": "app_count"},
                        {"page": 7, "field_id": "population_by_domain"}],
        pages_read=12, pages_total=12, source_name="SOW.pdf")
    text = R.describe_extraction(ex, 96)
    line = next(l for l in text.splitlines() if "not found on" in l)
    assert "p. 4, 7" in line and "check" in line.lower()
    assert "app_count" in line and "user_count" in line and "population_by_domain" in line
    assert "not found on" not in R.describe_extraction(
        R.RfpExtraction(fields=ex.fields, pages_read=12, source_name="SOW.pdf"), 96)
