"""Turn a client RFP or SOW into a discovery answer set, a requirement register
and a bid/no-bid check.

WHY THIS EXISTS
---------------
The 22-area, 96-field interview takes about ninety minutes. Most of what it asks
is already written down in the RFP the client sent. Reading it out of the
document and asking the consultant to CORRECT rather than TYPE turns that into
about five minutes.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It is not autopilot. An RFP says what the CLIENT wants; it says nothing about
what IV proposes. The ESNAD tender that motivated this is vendor-neutral by
design -- `iam_vendor` is absent, and that single field drives the whole
proposal. Differentiators, case studies and commercials are IV's positioning,
not the client's brief. Those still come from a person.

So the flow is: extract what the document states, SHOW it with the page it came
from, and ask only for what is genuinely IV's to decide.

THE FAILURE THIS GUARDS AGAINST
-------------------------------
A silently partial extraction is worse here than in the interview. In the
interview a consultant types each answer and would notice one missing; here
nobody is watching. A proposal confidently wrong about the client's own stated
requirements is the worst artefact this system could produce. Hence:
every extracted value carries its source page, the count is always reported, and
nothing generates until a human has seen the list.
"""
from __future__ import annotations

import logging
import os
import re
import time

from pydantic import BaseModel
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("shilpi-brain.rfp")

# Below this many characters per page, the PDF has no usable text layer and must
# be read visually. The ESNAD SOW yielded 468 characters across 20 pages -- all
# of them page numbers -- and both pdftotext and pypdf returned nothing else.
# Without this check the extractor would run happily on an empty string and
# report that the document contained no requirements.
MIN_CHARS_PER_PAGE = 200


# Where Open WebUI stores chat attachments, mounted read-only into this
# container by deploy/docker-compose.yml.
#
# OWUI extracts PDF text itself and sends the brain only that text. For a raster
# tender it is nothing -- the 20-page ESNAD SOW yielded 468 characters, all page
# numbers -- so the brain reads the real file from OWUI's own storage instead.
OWUI_UPLOADS = os.environ.get("SHILPI_OWUI_UPLOADS", "/owui-data/uploads")

# How far back an upload can be and still count as "the file just attached".
UPLOAD_WINDOW_S = int(os.environ.get("SHILPI_UPLOAD_WINDOW_S", "900"))

_DOC_SUFFIXES = (".pdf", ".docx", ".doc", ".txt", ".md", ".rtf")


def uploads_available() -> bool:
    """Is OWUI's upload directory actually mounted?

    Checked at startup and reported by /health. This couples the brain to Open
    WebUI's internal layout, which can move on an upgrade -- and a silently
    missing mount would make RFP intake fail in a way that looks like a bad
    document rather than a bad deploy.
    """
    return os.path.isdir(OWUI_UPLOADS)


def recent_uploads(window_s: int = UPLOAD_WINDOW_S) -> list[tuple[str, float]]:
    """(path, mtime) for documents uploaded recently, newest first."""
    if not uploads_available():
        log.warning("OWUI uploads directory not mounted at %s", OWUI_UPLOADS)
        return []
    cutoff = time.time() - window_s
    out: list[tuple[str, float]] = []
    try:
        for name in os.listdir(OWUI_UPLOADS):
            if not name.lower().endswith(_DOC_SUFFIXES):
                continue
            path = os.path.join(OWUI_UPLOADS, name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime >= cutoff:
                out.append((path, mtime))
    except OSError as e:
        log.warning("could not list %s: %s", OWUI_UPLOADS, e)
        return []
    return sorted(out, key=lambda pair: pair[1], reverse=True)


def display_name(path: str) -> str:
    """OWUI prefixes a uuid: "a43c7f9c-..._SOW.pdf" -> "SOW.pdf"."""
    base = os.path.basename(path)
    return re.sub(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
                  r"[0-9a-f]{12}_", "", base, flags=re.I)


def pick_upload(window_s: int = UPLOAD_WINDOW_S) -> tuple[Optional[str], list[str]]:
    """(chosen path, ambiguous candidates).

    Returns the newest upload when it is clearly the one just attached. When two
    files land within seconds of each other the caller must ASK: reading the
    wrong tender is far worse than one extra question, and two copies of
    SOW.pdf already exist from testing.
    """
    recent = recent_uploads(window_s)
    if not recent:
        return None, []
    if len(recent) == 1:
        return recent[0][0], []
    newest_path, newest_mtime = recent[0]
    contenders = [p for p, m in recent if newest_mtime - m <= 60]
    if len(contenders) > 1:
        return None, [display_name(p) for p in contenders]
    return newest_path, []


@dataclass
class ExtractedField:
    """One discovery answer, with where it came from."""
    field_id: str
    value: str
    page: Optional[int] = None
    confidence: float = 1.0


@dataclass
class Requirement:
    """A numbered requirement from the client's own register.

    These are what a tender evaluator scores, line by line. They are not a
    supporting appendix -- they are the spine of the response.
    """
    ref: str                      # "AM-04", "ILM-01", "PAM-07"
    text: str
    category: str = ""            # "Access Management", "Identity Lifecycle"
    page: Optional[int] = None


@dataclass
class EligibilityGate:
    """A mandatory qualification the bidder must meet to be considered."""
    text: str
    page: Optional[int] = None
    met: Optional[bool] = None    # None until a human answers


@dataclass
class RfpExtraction:
    fields: list[ExtractedField] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    gates: list[EligibilityGate] = field(default_factory=list)
    mandated_structure: list[str] = field(default_factory=list)
    pages_read: int = 0
    used_vision: bool = False
    source_name: str = ""

    def answers(self) -> dict[str, str]:
        """The extraction as a discovery answer dict."""
        return {f.field_id: f.value for f in self.fields if f.value}


def needs_vision(text_by_page: list[str]) -> bool:
    """Is the text layer too thin to trust?

    Checked explicitly rather than inferred from an empty result, because
    "extracted nothing" and "the document says nothing" are indistinguishable
    downstream and have opposite fixes.
    """
    if not text_by_page:
        return True
    total = sum(len((t or "").strip()) for t in text_by_page)
    return total / max(len(text_by_page), 1) < MIN_CHARS_PER_PAGE


# --- Requirement register -----------------------------------------------------
#
# Real registers use a prefix and a number: ILM-01, AM-12, PAM-07, REQ-3.2.
# Matched structurally rather than by a fixed prefix list, because every client
# invents their own.
_REQ_REF_RE = re.compile(r"\b([A-Z]{2,6}[-_ ]?\d{1,3}(?:\.\d{1,2})?)\b")

# Prefixes that are units, standards or dates rather than requirement ids.
# Header and footer lines, which carry document-control numbers shaped exactly
# like requirement references.
_PAGE_FURNITURE = re.compile(r"page\s+\d+\s+of\s+\d+|^\s*QMS\d", re.I)

_REQ_REF_EXCLUDE = re.compile(
    r"^(ISO|IEC|NIST|RFC|SHA|AES|RSA|TLS|SSL|SAML|OAUTH|SLA|KPI|VAT|GB|MB|TB|"
    r"CPU|RAM|IPV|HTTP|API|SP|Q)\b", re.I)


def extract_requirements(text_by_page: list[str]) -> list[Requirement]:
    """Every numbered requirement, with its page.

    A missing requirement is a lost mark in evaluation, so this errs toward
    over-capture: a false positive is visible to the reviewer, a miss is not.
    """
    out: list[Requirement] = []
    seen: set[str] = set()
    for page_no, text in enumerate(text_by_page, start=1):
        for line in (text or "").splitlines():
            line = line.strip()
            if len(line) < 12:
                continue
            # Page furniture. The ESNAD tender headers every page with a
            # document-control number ("QMS630/015-00 Page 1 of 20"), which
            # matches the requirement-reference shape exactly.
            if _PAGE_FURNITURE.search(line):
                continue
            m = _REQ_REF_RE.match(line) or _REQ_REF_RE.search(line[:24])
            if not m:
                continue
            ref = m.group(1).replace("_", "-").replace(" ", "-").upper()
            if _REQ_REF_EXCLUDE.match(ref) or ref in seen:
                continue
            body = line[m.end():].strip(" .:\t-|")
            if len(body.split()) < 3:
                continue
            seen.add(ref)
            out.append(Requirement(ref=ref, text=body[:600], page=page_no))
    return out


# --- Eligibility gates --------------------------------------------------------
#
# Page 4 of the ESNAD tender lists mandatory qualifications: five years' IAM
# delivery, three implementations at >=10,000 identities, ISO 27001 certified
# delivery organisation, local KSA presence. Failing ONE disqualifies the bid.
#
# Surfacing these before drafting turns two days of wasted work into a
# two-minute check.
_GATE_TRIGGERS = re.compile(
    r"(shall demonstrate|must demonstrate|minimum qualification|mandatory "
    r"qualification|vendor qualification|eligibility|pre[- ]?qualification|"
    r"bidder shall|responding vendors shall)", re.I)

_GATE_LINE = re.compile(
    r"(minimum|at least|no less than|\bmust\b|\bshall\b|certified|certification|"
    r"reference customers|local presence|years of)", re.I)


def extract_eligibility_gates(text_by_page: list[str]) -> list[EligibilityGate]:
    """Mandatory qualifications, from the section that announces them.

    Scoped to the pages following a qualifications heading rather than scanning
    the whole document, or every "shall" in a 20-page tender becomes a gate.
    """
    out: list[EligibilityGate] = []
    for page_no, text in enumerate(text_by_page, start=1):
        lines = (text or "").splitlines()
        armed = False
        for line in lines:
            stripped = line.strip()
            if _GATE_TRIGGERS.search(stripped):
                armed = True
                continue
            if not armed:
                continue
            # A new top-level heading ends the qualifications block.
            if stripped and stripped.endswith(":") and len(stripped.split()) <= 6:
                armed = False
                continue
            cleaned = stripped.lstrip("-•‑– \t")
            if len(cleaned.split()) >= 5 and _GATE_LINE.search(cleaned):
                out.append(EligibilityGate(text=cleaned[:400], page=page_no))
            if len(out) >= 20:      # a register this long is a parse failure
                return out
    return out


# --- Mandated response structure ---------------------------------------------
#
# Many tenders specify the order of the response, or supply their own template.
# Producing IV's house structure against a mandated one gets marked down or
# rejected outright, so a detected structure OVERRIDES the template.
_STRUCTURE_TRIGGERS = re.compile(
    r"(respond in the (following )?order|response (shall|must) (be )?structur|"
    r"proposal (shall|must) (follow|contain|include) the following|"
    r"submission format|response format|shall be organi[sz]ed)", re.I)


def extract_mandated_structure(text_by_page: list[str]) -> list[str]:
    """Section headings the client requires, in order. Empty when none stated."""
    for page_no, text in enumerate(text_by_page, start=1):
        lines = [ln.strip() for ln in (text or "").splitlines()]
        for i, line in enumerate(lines):
            if not _STRUCTURE_TRIGGERS.search(line):
                continue
            headings = []
            for nxt in lines[i + 1:i + 25]:
                m = re.match(r"^\s*(?:\d{1,2}[.)]|[-•])\s*(.{4,80})$", nxt)
                if m:
                    headings.append(m.group(1).strip(" .:"))
                elif headings:
                    break
            if len(headings) >= 3:
                log.info("mandated response structure found on page %d: %d sections",
                         page_no, len(headings))
                return headings
    return []


# --- Reporting ----------------------------------------------------------------
#
# Fields IV must decide. An RFP is vendor-neutral by design, so their ABSENCE is
# expected -- reporting them as extraction failures would be wrong and would
# train the user to ignore the gap list.
IV_DECISION_FIELDS = (
    "iam_vendor", "differentiators", "case_studies_to_highlight",
    "case_studies_include", "partner_positioning", "vendor_partner_positioning",
    "similar_projects", "pricing_model", "payment_milestones", "license_included",
    "taxes", "travel", "support_terms", "validity_period", "proposal_depth",
    "required_diagram_types", "diagram_count",
)


def describe_extraction(ex: RfpExtraction, total_fields: int) -> str:
    """What was read, from where, and what still needs a person.

    Shown BEFORE anything is generated. A silently partial extraction is worse
    here than in the interview: nobody is watching each answer arrive, and a
    proposal confidently wrong about the client's own requirements is the worst
    thing this system could produce.
    """
    got = len(ex.fields)
    how = "read visually (no text layer)" if ex.used_vision else "read from the text layer"
    lines = [
        f"**{ex.source_name or 'Document'}** — {ex.pages_read} pages, {how}.",
        "",
        f"Extracted **{got} of {total_fields}** discovery fields, "
        f"**{len(ex.requirements)}** numbered requirements"
        + (f", **{len(ex.gates)}** eligibility conditions." if ex.gates else "."),
        "",
    ]
    if ex.mandated_structure:
        lines += [f"The RFP mandates a response structure of "
                  f"{len(ex.mandated_structure)} sections. I will follow it "
                  f"instead of IV's house template.", ""]
    for f in ex.fields[:40]:
        page = f" _(p.{f.page})_" if f.page else ""
        lines.append(f"  - **{f.field_id}** — {f.value[:110]}{page}")
    if got > 40:
        lines.append(f"  - _…and {got - 40} more_")

    missing_iv = [k for k in IV_DECISION_FIELDS if k not in ex.answers()]
    if missing_iv:
        lines += ["",
                  "These are **IV's decisions, not the client's** — an RFP is "
                  "vendor-neutral by design, so I cannot read them from it:",
                  "  " + " · ".join(missing_iv[:12])]
    lines += ["",
              "Correct anything above as `field_name: value`, or say "
              "**continue** to move on."]
    return "\n".join(lines)


def describe_gates(gates: list[EligibilityGate]) -> str:
    """The bid/no-bid check, before any drafting happens.

    Failing one mandatory qualification kills the bid. Two minutes here saves
    two days of writing a proposal that cannot be accepted.
    """
    if not gates:
        return ""
    lines = ["**Before we write anything — mandatory qualifications in this RFP.**",
             "Failing any one of these disqualifies the bid, so it is worth "
             "checking now rather than after the proposal is written:",
             ""]
    for i, g in enumerate(gates, start=1):
        page = f" _(p.{g.page})_" if g.page else ""
        lines.append(f"  {i}. {g.text}{page}")
    lines += ["",
              "Tell me which of these IV does **not** meet, or say **all met** "
              "to continue."]
    return "\n".join(lines)


# --- Rasterising --------------------------------------------------------------

RASTER_DPI = int(os.environ.get("SHILPI_RFP_RASTER_DPI", "150"))
MAX_RFP_PAGES = int(os.environ.get("SHILPI_MAX_RFP_PAGES", "60"))


def rasterise(pdf_path: str, out_dir: str) -> list[str]:
    """PNG per page, in order. Empty list on any failure -- never partial.

    A partial rasterisation (page 12 of 20 failed silently) is worse than none:
    it would extract a confident answer set from an incomplete document and
    never say so. `pdftoppm` either produces every page or the caller is told
    nothing was read.
    """
    import subprocess
    os.makedirs(out_dir, exist_ok=True)
    prefix = os.path.join(out_dir, "pg")
    try:
        subprocess.run(
            ["pdftoppm", "-r", str(RASTER_DPI), "-png",
             "-f", "1", "-l", str(MAX_RFP_PAGES), pdf_path, prefix],
            check=True, capture_output=True, timeout=180)
    except Exception as e:  # noqa: BLE001
        log.error("rasterising %s failed: %s", pdf_path, e)
        return []
    pages = sorted(f for f in os.listdir(out_dir) if f.startswith("pg-"))
    return [os.path.join(out_dir, f) for f in pages]


def read_text_layer(pdf_path: str) -> list[str]:
    """Per-page text via pdftotext. Empty strings where a page has none."""
    import subprocess
    try:
        out = subprocess.run(
            ["pdftotext", "-layout", pdf_path, "-"],
            check=True, capture_output=True, timeout=60, text=True)
    except Exception as e:  # noqa: BLE001
        log.warning("text-layer read failed for %s: %s", pdf_path, e)
        return []
    # pdftotext separates pages with a form-feed character.
    return out.stdout.split("\x0c")


# --- Vision extraction ---------------------------------------------------------
#
# One page per call rather than the whole document at once. A 20-page tender in
# a single vision call risks the model summarising rather than transcribing, and
# a truncated response loses the back half silently. One page at a time is
# slower but each page's extraction is independently verifiable against its
# image -- the same reason diagrams are approved one at a time in this system.

_PAGE_EXTRACT_PROMPT = """You are reading ONE PAGE of a client RFP or Statement
of Work for an IAM (Identity and Access Management) proposal.

Transcribe what this page states. Do not summarise, do not infer anything not
written on the page, and do not carry over content from other pages.

Return, for THIS PAGE ONLY:
- field_values: any of these that this page states outright: {field_ids}
- requirements: numbered requirement lines (e.g. "AM-04", "ILM-01") with their
  reference and full text
- eligibility_gates: mandatory bidder qualifications, ONLY if this page is
  explicitly about vendor/bidder qualifications
- structure_headings: numbered/bulleted section names, ONLY if this page
  explicitly mandates the proposal's response structure

Leave anything not on THIS page empty. A wrong answer is worse than an empty
one: this system will show every value to a human with this page number
attached, so a value must be traceable to what is actually written here."""


class _PageExtraction(BaseModel):
    field_values: dict[str, str] = {}
    requirements: list[dict] = []
    eligibility_gates: list[str] = []
    structure_headings: list[str] = []


async def extract_page_vision(image_path: str, field_ids: list[str],
                              structured_fn) -> "_PageExtraction":
    """One page, read visually. Fails to an empty extraction, never raises.

    `structured_fn` is injected (rather than importing app.py) to keep this
    module free of app.py's dependency surface -- same arrangement as
    document_engine's retrieve_fn/embed_fn.
    """
    import base64
    try:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        messages = [
            {"role": "system", "content": _PAGE_EXTRACT_PROMPT.format(
                field_ids=", ".join(field_ids))},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ]
        return await structured_fn(_PageExtraction, messages)
    except Exception as e:  # noqa: BLE001 - one bad page must not sink the SOW
        log.warning("vision extraction failed for %s: %s", image_path, e)
        return _PageExtraction()


async def extract_rfp(pdf_path: str, source_name: str, field_ids: list[str],
                      structured_fn, tmp_dir: str) -> RfpExtraction:
    """The whole pipeline: text-layer check -> rasterise if needed -> per-page
    extraction -> merge.

    First value wins per field (earlier pages are more likely to state facts
    like client name and scope; later repeats are usually restatement). Every
    requirement and gate across all pages is kept -- under-collecting those
    costs a mark in evaluation, unlike a duplicated field value.
    """
    text_pages = read_text_layer(pdf_path)
    use_vision = needs_vision(text_pages)

    ex = RfpExtraction(source_name=source_name,
                       pages_read=len(text_pages) or 0, used_vision=use_vision)

    if not use_vision:
        # A usable text layer: mandated structure and gates can be read
        # directly; field values still need per-page interpretation, so the
        # SAME per-page loop runs, just against text instead of images.
        pass

    if use_vision:
        images = rasterise(pdf_path, tmp_dir)
        if not images:
            log.error("could not rasterise %s; extraction is EMPTY, not partial",
                     source_name)
            return ex
        ex.pages_read = len(images)

    seen_fields: set = set()
    for i in range(ex.pages_read):
        if use_vision:
            page_ex = await extract_page_vision(images[i], field_ids, structured_fn)
        else:
            page_ex = await _extract_page_text(text_pages[i], field_ids, structured_fn)

        page_no = i + 1
        for fid, val in (page_ex.field_values or {}).items():
            if fid in field_ids and val and fid not in seen_fields:
                seen_fields.add(fid)
                ex.fields.append(ExtractedField(fid, str(val)[:2000], page_no))
        for r in (page_ex.requirements or []):
            ref = str(r.get("ref") or r.get("id") or "").strip()
            text = str(r.get("text") or "").strip()
            if ref and text:
                ex.requirements.append(Requirement(ref=ref, text=text[:600], page=page_no))
        for g in (page_ex.eligibility_gates or []):
            if g and len(g.split()) >= 5:
                ex.gates.append(EligibilityGate(text=str(g)[:400], page=page_no))
        if page_ex.structure_headings and not ex.mandated_structure:
            ex.mandated_structure = [str(h)[:80] for h in page_ex.structure_headings]

    log.info("RFP extraction: %s, %d pages, %d fields, %d requirements, %d gates",
             source_name, ex.pages_read, len(ex.fields), len(ex.requirements),
             len(ex.gates))
    return ex


async def _extract_page_text(text: str, field_ids: list[str],
                             structured_fn) -> "_PageExtraction":
    """The text-layer counterpart to extract_page_vision, for a readable PDF."""
    if not (text or "").strip():
        return _PageExtraction()
    try:
        messages = [
            {"role": "system", "content": _PAGE_EXTRACT_PROMPT.format(
                field_ids=", ".join(field_ids))},
            {"role": "user", "content": text[:6000]},
        ]
        return await structured_fn(_PageExtraction, messages)
    except Exception as e:  # noqa: BLE001
        log.warning("text extraction failed: %s", e)
        return _PageExtraction()


def requirement_coverage(requirements: list[Requirement],
                         answered_refs: set) -> tuple[int, list[str]]:
    """(answered, missing refs). A missing requirement is a lost mark."""
    missing = [r.ref for r in requirements if r.ref not in (answered_refs or set())]
    return len(requirements) - len(missing), missing
