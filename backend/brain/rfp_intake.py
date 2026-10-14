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

import asyncio
import contextvars
import logging
import os
import re
import sqlite3
import time

from pydantic import BaseModel, Field as PField
from dataclasses import asdict, dataclass, field
from typing import Optional

import document_qa

log = logging.getLogger("shilpi-brain.rfp")

# The version of what reading a tender produces. A stored reading is reused only
# for the same file AND this version (design 1c): change it whenever the reader
# would read the same file differently.
EXTRACTOR_VERSION = "2026-10-14.2"  # .2: two-pass reading (1b)

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

# What can be read: PDF directly, Word by converting it to PDF first (as_pdf).
# The rest stay candidates only so that their owner is TOLD the format is not
# readable, instead of "I don't see a document".
_WORD_SUFFIXES = (".docx", ".doc")
_DOC_SUFFIXES = (".pdf",) + _WORD_SUFFIXES + (".txt", ".md", ".rtf")


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


# Whose upload is it (audit 2026-10-12, finding 2). The uploads directory is
# shared by every Open WebUI user, and "the newest file in the last 15 minutes"
# handed Alice's extraction Bob's tender. Open WebUI records the uploader of
# each file in its own database (file.id is the uuid prefix of the stored
# name), on the volume already mounted here read-only, and forwards the
# signed-in user's id as X-OpenWebUI-User-Id. An upload is read only when it
# provably belongs to the person asking. With no identity there is no guess.
OWUI_DB = os.environ.get("SHILPI_OWUI_DB",
                         os.path.join(os.path.dirname(OWUI_UPLOADS.rstrip("/")), "webui.db"))
REQUEST_USER_ID: contextvars.ContextVar = contextvars.ContextVar("owui_user_id", default=None)
# Escape hatch for a deliberately single-user install whose Open WebUI cannot
# forward identity: restores "newest recent upload". Off by default.
ALLOW_UNBOUND_UPLOADS = os.environ.get("SHILPI_ALLOW_UNBOUND_UPLOADS") == "1"
_UUID_PREFIX = re.compile(r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})_", re.I)

UNBOUND = "unbound"   # pick_upload reason: ownership could not be established


def upload_owners(paths: list[str]) -> Optional[dict[str, str]]:
    """path -> Open WebUI user id of the uploader. None when OWUI's records
    cannot be read at all (not mounted, schema moved): ownership is then unknown."""
    ids = {}
    for path in paths:
        m = _UUID_PREFIX.match(os.path.basename(path))
        if m:
            ids[m.group(1).lower()] = path
    if not ids:
        return {}
    try:
        con = sqlite3.connect(f"file:{OWUI_DB}?mode=ro", uri=True, timeout=2)
        try:
            marks = ",".join("?" * len(ids))
            rows = con.execute(f"select id, user_id from file where lower(id) in ({marks})",
                               list(ids)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as e:
        log.warning("cannot read upload ownership from %s: %s", OWUI_DB, e)
        return None
    return {ids[str(fid).lower()]: str(uid) for fid, uid in rows if uid}


def pick_upload(window_s: int = UPLOAD_WINDOW_S, user_id: Optional[str] = None,
                name_hint: str = "") -> tuple[Optional[str], list[str], str]:
    """(chosen path, ambiguous candidates, reason).

    Only uploads belonging to `user_id` are considered. reason is UNBOUND when
    the requester or the uploader cannot be identified: the caller must say so
    rather than read someone's document. Among the requester's own files the
    newest wins when it is clearly the one just attached; two within a minute
    are settled by a file name in `name_hint`, else the caller must ASK.
    """
    recent = recent_uploads(window_s)
    if not recent:
        return None, [], ""
    if not ALLOW_UNBOUND_UPLOADS:
        owners = upload_owners([p for p, _ in recent])
        if not user_id or owners is None:
            return None, [], UNBOUND
        recent = [(p, m) for p, m in recent if owners.get(p) == str(user_id)]
        if not recent:
            return None, [], ""
    if len(recent) == 1:
        return recent[0][0], [], ""
    newest_path, newest_mtime = recent[0]
    contenders = [p for p, m in recent if newest_mtime - m <= 60]
    if len(contenders) > 1:
        named = [p for p in contenders if display_name(p).lower() in (name_hint or "").lower()]
        if len(named) == 1:
            return named[0], [], ""
        return None, [display_name(p) for p in contenders], ""
    return newest_path, [], ""


def as_pdf(path: str, tmp_dir: str) -> tuple[Optional[str], str]:
    """(path of a PDF to read, "") or (None, what to tell the user).

    The reader is pdftotext and pdftoppm, so everything is read as a PDF.
    Tenders arrive as Word at least as often: those are converted with the
    LibreOffice already in the image. Any other type is refused by name before
    work starts; it used to be accepted and then fail as "Reading that
    document failed" (audit F16). Blocking (LibreOffice): call it off the
    event loop."""
    name = display_name(path)
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return path, ""
    if ext not in _WORD_SUFFIXES:
        return None, (f"I can read a tender as PDF or Word. **{name}** is a "
                      f"{ext or 'file with no'} type I cannot read: save it as PDF or "
                      f"DOCX and attach it again, or say **interview** to answer "
                      f"questions instead.")
    import export_engine
    try:
        with open(path, "rb") as fh:
            pdf, err = export_engine.export_pdf(fh.read(), suffix=ext)
    except OSError as e:
        pdf, err = None, str(e)
    if not pdf:
        log.error("could not convert %s to PDF: %s", name, err)
        return None, (f"**{name}** could not be converted for reading (a problem on "
                      f"my side, not with your file). Save it as PDF and attach that, "
                      f"or say **interview** to answer questions instead.")
    os.makedirs(tmp_dir, exist_ok=True)
    out = os.path.join(tmp_dir, "tender.pdf")
    with open(out, "wb") as fh:
        fh.write(pdf)
    return out, ""


@dataclass
class ExtractedField:
    """One discovery answer, with where it came from: the pages it rests on and
    the client's own words ({"page", "text"}) that state it."""
    field_id: str
    value: str
    page: Optional[int] = None
    confidence: float = 1.0
    pages: list[int] = field(default_factory=list)
    quotes: list[dict] = field(default_factory=list)


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
    pages_total: int = 0      # pages in the source; more than pages_read = cut off
    used_vision: bool = False
    source_name: str = ""
    # In a document that HAS a text layer: the pages that did not (scans bound
    # into it) and were read as images, and the ones that could not be read at
    # all. An unread page is reported, never passed over.
    vision_pages: list[int] = field(default_factory=list)
    unread_pages: list[int] = field(default_factory=list)
    # Quotes the model offered that were not on the page it named: dropped.
    dropped_quotes: int = 0

    def answers(self) -> dict[str, str]:
        """The extraction as a discovery answer dict."""
        return {f.field_id: f.value for f in self.fields if f.value}

    def to_dict(self) -> dict:
        """JSON-safe, for the stored reading (rfp_readings)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RfpExtraction":
        d = dict(d)
        return cls(fields=[ExtractedField(**f) for f in d.pop("fields", [])],
                   requirements=[Requirement(**r) for r in d.pop("requirements", [])],
                   gates=[EligibilityGate(**g) for g in d.pop("gates", [])], **d)


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


def page_is_thin(text: str) -> bool:
    """Does THIS page lack a usable text layer? needs_vision answers for the
    document as an average, which a text PDF with scanned annexures passes
    while the annexures carry nothing but an image (audit F12)."""
    return len((text or "").strip()) < MIN_CHARS_PER_PAGE


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


# A requirement is the client's own wording and is scored line by line. This
# cap was 600 and cut long ones mid-sentence on the way in; it is a guard
# against runaway model output, the same size as the cap on a field value.
MAX_REQUIREMENT_CHARS = 2000


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
            out.append(Requirement(ref=ref, text=body[:MAX_REQUIREMENT_CHARS], page=page_no))
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


def describe_decisions(answers: dict) -> str:
    """IV's decisions still to make, in one message, each with its choices.

    An RFP is vendor-neutral by design, so these are never read from it
    (intake_template's field kinds); their absence is expected, not a failure."""
    import intake_template
    missing = intake_template.unanswered_decisions(answers)
    if not missing:
        return ""
    questions = {q["id"]: q for q in intake_template.iter_questions(None)}
    lines = ["**IV's decisions, not the client's.** An RFP is vendor-neutral by "
             "design, so I do not read these from it. Answer them in one reply, "
             "as `field: value`:"]
    for qid in missing:
        q = questions[qid]
        choices = (f": one of {', '.join(str(o) for o in q['options'])}"
                   if q.get("options") else "")
        lines.append(f"  - **{qid}** — {q['label']}{choices}")
    return "\n".join(lines)


def describe_extraction(ex: RfpExtraction, total_fields: int) -> str:
    """What was read, from where, and what still needs a person.

    Shown BEFORE anything is generated. A silently partial extraction is worse
    here than in the interview: nobody is watching each answer arrive, and a
    proposal confidently wrong about the client's own requirements is the worst
    thing this system could produce.
    """
    got = len(ex.fields)
    how = "read visually (no text layer)" if ex.used_vision else "read from the text layer"
    if ex.vision_pages:
        how += (f"; {len(ex.vision_pages)} scanned page(s) read visually "
                f"(p. {', '.join(str(p) for p in ex.vision_pages)})")
    lines = [
        f"**{ex.source_name or 'Document'}** — {ex.pages_read} pages, {how}.",
        "",
    ]
    if ex.unread_pages:
        lines += [
            f"**NOT READ: page {', '.join(str(p) for p in ex.unread_pages)}** "
            f"{'has' if len(ex.unread_pages) == 1 else 'have'} no text layer and could "
            f"not be read as an image. If anything is written there, it is NOT in what "
            f"follows. Check those pages against the source before relying on this.",
            "",
        ]
    if ex.pages_total > ex.pages_read:
        # Audit 2026-10-12 finding 7: a capped read looked like a complete one.
        lines += [
            f"**INCOMPLETE READ: only the first {ex.pages_read} of {ex.pages_total} pages "
            f"were read.** Requirements, gates and scope on pages "
            f"{ex.pages_read + 1}-{ex.pages_total} are NOT in what follows. Split the "
            f"document and upload the rest, or add those requirements yourself, before "
            f"relying on the compliance matrix.",
            "",
        ]
    lines += [
        f"Extracted **{got} of {total_fields}** discovery fields, "
        f"**{len(ex.requirements)}** numbered requirements"
        + (f", **{len(ex.gates)}** eligibility conditions." if ex.gates else "."),
        "",
    ]
    if ex.mandated_structure:
        # Said honestly: the structure is not stored or used by drafting. This
        # used to promise "I will follow it instead of IV's house template".
        lines += [f"The RFP mandates a response structure of "
                  f"{len(ex.mandated_structure)} sections:", ""]
        lines += [f"  {i}. {h}" for i, h in enumerate(ex.mandated_structure, start=1)]
        lines += ["", "I draft in IV's own structure, not this one. Reorder the "
                      "draft to match the list above before it is submitted.", ""]
    for f in ex.fields[:40]:
        page = f" _(p.{f.page})_" if f.page else ""
        lines.append(f"  - **{f.field_id}** — {f.value[:110]}{page}")
    if got > 40:
        lines.append(f"  - _…and {got - 40} more_")

    decisions = describe_decisions(ex.answers())
    if decisions:
        lines += ["", decisions]
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
    # pdftotext ENDS every page with a form feed, the last one included, so a
    # plain split yields one empty page too many. That phantom page made a
    # fully read scanned tender report "only the first N of N+1 pages".
    pages = out.stdout.split("\x0c")
    if pages and not pages[-1].strip():
        pages.pop()
    return pages


# --- Reading: two passes with evidence (design 2026-10-14, part 1b) ------------
#
# Pass 1, per page: for each client-fact field, the words the page uses,
# verbatim, with no interpretation. Each quote is checked against that page's
# text (its text layer, or for a scanned page the model's own transcription)
# with the compliance matrix's quote-grounding test; a quote that is not there
# is dropped and counted. A dense page is read in parts, so no text goes unread.
#
# Pass 2, per field: one call sees every grounded quote for that field across
# the document and states the value, with the pages it rests on. Facts spread
# over pages ("WIAM 5,000" on p.4, "50 privileged accounts" on p.7) are
# combined here; the old per-page merge kept whichever single page said most.
# A field with no grounded quote is "not found", never guessed.
#
# One page per call rather than the whole document at once: a 20-page tender in
# a single vision call risks the model summarising rather than transcribing,
# and a truncated response loses the back half silently.

# A page's text is sent in parts of at most this many characters (cut at line
# breaks). It used to be cut at 6,000 and the rest never read (audit F11).
PAGE_PART_CHARS = int(os.environ.get("SHILPI_RFP_PAGE_PART_CHARS", "6000"))
# Pass-2 calls in flight at once.
FIELD_CALLS_IN_FLIGHT = 6
# A stated value longer than this is a runaway answer: kept, cut and marked.
MAX_FIELD_CHARS = 2000

_PAGE_EXTRACT_PROMPT = """You are reading ONE PAGE (or one part of a page) of a
client RFP or Statement of Work for an IAM (Identity and Access Management)
proposal.

Copy, do not interpret. Return, for THIS PAGE ONLY:
- quotes: for each field below that this page states, the words exactly as
  written: the whole sentence, list item or table row, not a summary and not a
  value you worked out. A field may have several quotes. Use the id (left of
  the colon) exactly as field_id; the text after it says what the field means:
{field_ids}
- transcription: when you are given an IMAGE, every line of text on it,
  verbatim, in reading order. When you are given text, leave it empty.
- requirements: numbered requirement lines (e.g. "AM-04", "ILM-01") with their
  reference and full text
- eligibility_gates: mandatory bidder qualifications, ONLY if this page is
  explicitly about vendor/bidder qualifications
- structure_headings: numbered/bulleted section names, ONLY if this page
  explicitly mandates the proposal's response structure

Leave anything not on THIS page empty. Every quote is checked against the
page's text and dropped if it is not there."""

_COMBINE_PROMPT = """You are given every verbatim quote a client tender makes
about ONE discovery field, each with its page. State the field's value.

Field: {field}

- Use only what the quotes state. Keep every distinct fact (for example a user
  count for each population), and keep figures exactly as written.
- pages: the page numbers of the quotes your value rests on.
- If the quotes do not actually state this field, return an empty value."""


def _field_list(field_ids: list[str]) -> str:
    """'id: label' per line. Bare ids alone ('post_sla') left the model to
    guess meaning: ESNAD's SLA severity matrix was never extracted in three
    runs, so every draft invented an SLA contradicting the client's own."""
    import intake_template
    labels = {q["id"]: q["label"] for q in intake_template.iter_questions(None)}
    return "\n".join(f"  {fid}: {labels.get(fid, fid)}" for fid in field_ids)


class _FieldQuote(BaseModel):
    """One verbatim quote for one discovery field. A strict schema, not a free
    dict: a loose dict[str, str] let the model key values however it liked and
    every key not exactly a field id was dropped (two ESNAD runs, 0 fields)."""
    field_id: str = PField(description="must be one of the provided field ids, exactly")
    quote: str = PField(description="the words exactly as written on the page")


class _RequirementItem(BaseModel):
    """A numbered requirement line. A strict schema for the same reason: a
    list[dict] let the model return "ref"/"Ref"/"id"/"reference" at will."""
    ref: str = PField(description="the requirement reference, e.g. AM-04, ILM-01")
    text: str


class _PageExtraction(BaseModel):
    quotes: list[_FieldQuote] = []
    transcription: str = ""
    requirements: list[_RequirementItem] = []
    eligibility_gates: list[str] = []
    structure_headings: list[str] = []


class _FieldReading(BaseModel):
    """Pass 2: one field's value from its quotes, and the pages it rests on."""
    value: str = ""
    pages: list[int] = []


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
                field_ids=_field_list(field_ids))},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ]
        return await structured_fn(_PageExtraction, messages)
    except Exception as e:  # noqa: BLE001 - one bad page must not sink the SOW
        log.warning("vision extraction failed for %s: %s", image_path, e)
        return _PageExtraction()


async def _extract_page_text(text: str, field_ids: list[str],
                             structured_fn) -> "_PageExtraction":
    """The text-layer counterpart to extract_page_vision, for one page part."""
    if not (text or "").strip():
        return _PageExtraction()
    try:
        messages = [
            {"role": "system", "content": _PAGE_EXTRACT_PROMPT.format(
                field_ids=_field_list(field_ids))},
            {"role": "user", "content": text},
        ]
        return await structured_fn(_PageExtraction, messages)
    except Exception as e:  # noqa: BLE001
        log.warning("text extraction failed: %s", e)
        return _PageExtraction()


def page_parts(text: str, limit: int = PAGE_PART_CHARS) -> list[str]:
    """The page in parts of at most `limit` characters, cut at line breaks (a
    single longer line is cut where it must). Every character is in a part."""
    parts, cur = [], ""
    for line in (text or "").splitlines(keepends=True):
        while len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) > limit:
            parts.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        parts.append(cur)
    return parts


def _match_field(fid: str, field_ids: list[str]) -> Optional[str]:
    """The field id the model meant, tolerating case and spacing ("Client
    Name"); an id that is not one of field_ids is dropped, not guessed."""
    fid = (fid or "").strip()
    if fid in field_ids:
        return fid
    canon = fid.lower().replace(" ", "_").replace("-", "_")
    return next((f for f in field_ids if f.lower() == canon), None)


async def _state_field(fid: str, quotes: list[tuple[int, str]], structured_fn,
                       gate: asyncio.Semaphore) -> Optional[ExtractedField]:
    """Pass 2 for one field. None when the quotes turn out not to state it."""
    import intake_template
    label = next((q["label"] for q in intake_template.iter_questions(None)
                  if q["id"] == fid), fid)
    quote_pages = sorted({p for p, _ in quotes})
    evidence = [{"page": p, "text": q} for p, q in quotes]
    messages = [
        {"role": "system", "content": _COMBINE_PROMPT.format(field=f"{fid}: {label}")},
        {"role": "user", "content": "\n".join(f"(p.{p}) {q}" for p, q in quotes)},
    ]
    try:
        async with gate:
            res = await structured_fn(_FieldReading, messages)
        value = " ".join((res.value or "").split())
        pages = sorted({p for p in (res.pages or []) if p in quote_pages}) or quote_pages
    except Exception as e:  # noqa: BLE001
        # The quotes are grounded; without the call they still are the
        # client's own words, so they stand as the value, never a guess.
        log.warning("stating %s from its quotes failed (%s); using the quotes", fid, e)
        value, pages = "; ".join(q for _, q in quotes), quote_pages
    if not value or value.lower() in _SKIP_VALUES or value.lower() == "not found":
        return None
    if len(value) > MAX_FIELD_CHARS:
        value = value[:MAX_FIELD_CHARS - 1].rstrip() + "…"
    return ExtractedField(fid, value, pages[0], pages=pages, quotes=evidence)


_SKIP_VALUES = {"", "skip", "n/a", "na", "none", "unknown"}


async def extract_rfp(pdf_path: str, source_name: str, field_ids: list[str],
                      structured_fn, tmp_dir: str) -> RfpExtraction:
    """Text-layer check, rasterise where needed, pass 1 per page (part), pass
    2 per field. Every requirement and gate across all pages is kept: under-
    collecting those costs a mark in evaluation."""
    import intake_template
    # Only client facts are read from a client document (design 1a): even a
    # caller that asks for proposal_type or the diagram list does not get them.
    field_ids = [f for f in field_ids if intake_template.field_kind(f) == "client_fact"]
    text_pages = read_text_layer(pdf_path)
    use_vision = needs_vision(text_pages)

    ex = RfpExtraction(source_name=source_name, pages_read=len(text_pages) or 0,
                       pages_total=len(text_pages) or 0, used_vision=use_vision)

    # Pages to read as images: all of them when the document has no text layer;
    # otherwise only the pages that have none of their own.
    thin = set() if use_vision else {i for i, t in enumerate(text_pages) if page_is_thin(t)}
    images: list[str] = []
    if use_vision or thin:
        images = rasterise(pdf_path, tmp_dir)
    if use_vision:
        if not images:
            log.error("could not rasterise %s; extraction is EMPTY, not partial",
                     source_name)
            return ex
        ex.pages_read = len(images)
        ex.pages_total = max(ex.pages_total, ex.pages_read)
        if ex.pages_read < ex.pages_total:
            log.warning("RFP %s: read %d of %d pages (SHILPI_MAX_RFP_PAGES=%d)",
                        source_name, ex.pages_read, ex.pages_total, MAX_RFP_PAGES)

    quotes: dict[str, list[tuple[int, str]]] = {}
    seen_requirements: set[tuple[str, str]] = set()

    def take(page_ex, grounding_text: str, page_no: int) -> None:
        for fq in (getattr(page_ex, "quotes", None) or []):
            fid = _match_field(fq.field_id, field_ids)
            quote = " ".join((fq.quote or "").split())
            if not fid or not quote:
                continue
            if not document_qa.quote_is_grounded(quote, grounding_text,
                                                 numbers_must_match=True):
                ex.dropped_quotes += 1
                continue
            if (page_no, quote) not in quotes.setdefault(fid, []):
                quotes[fid].append((page_no, quote))
        for r in (page_ex.requirements or []):
            ref, text = (r.ref or "").strip(), (r.text or "").strip()
            if ref and text:
                # The same requirement restated on a later page (a summary
                # table, a checklist) is one requirement. Only an EXACT repeat
                # is merged: the same reference with different wording may be
                # a different obligation, and stays.
                same = (ref.upper(), re.sub(r"[^a-z0-9]+", " ", text.lower()).strip())
                if same in seen_requirements:
                    continue
                seen_requirements.add(same)
                ex.requirements.append(Requirement(
                    ref=ref, text=text[:MAX_REQUIREMENT_CHARS], page=page_no))
        for g in (page_ex.eligibility_gates or []):
            if g and len(g.split()) >= 5:
                ex.gates.append(EligibilityGate(text=str(g)[:400], page=page_no))
        if page_ex.structure_headings and not ex.mandated_structure:
            ex.mandated_structure = [str(h)[:80] for h in page_ex.structure_headings]

    for i in range(ex.pages_read):
        page_no = i + 1
        if use_vision or (i in thin and i < len(images)):
            page_ex = await extract_page_vision(images[i], field_ids, structured_fn)
            if not use_vision:
                ex.vision_pages.append(page_no)
            take(page_ex, getattr(page_ex, "transcription", "") or "", page_no)
        elif i in thin:
            # No text and no picture of it (rasterising failed, or the page is
            # past the page cap). It may be blank; that cannot be told from
            # here, so it is reported rather than assumed.
            ex.unread_pages.append(page_no)
        else:
            for part in page_parts(text_pages[i]):
                take(await _extract_page_text(part, field_ids, structured_fn), part, page_no)

    gate = asyncio.Semaphore(FIELD_CALLS_IN_FLIGHT)
    stated = await asyncio.gather(*(_state_field(fid, qs, structured_fn, gate)
                                    for fid, qs in quotes.items()))
    ex.fields = sorted((f for f in stated if f),
                       key=lambda f: (f.page or 0, f.field_id))

    log.info("RFP extraction: %s, %d pages, %d fields, %d requirements, %d gates, "
             "%d quotes dropped as not on their page", source_name, ex.pages_read,
             len(ex.fields), len(ex.requirements), len(ex.gates), ex.dropped_quotes)
    return ex


def requirement_coverage(requirements: list[Requirement],
                         answered_refs: set) -> tuple[int, list[str]]:
    """(answered, missing refs). A missing requirement is a lost mark."""
    missing = [r.ref for r in requirements if r.ref not in (answered_refs or set())]
    return len(requirements) - len(missing), missing
