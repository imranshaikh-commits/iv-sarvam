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
import re
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("shilpi-brain.rfp")

# Below this many characters per page, the PDF has no usable text layer and must
# be read visually. The ESNAD SOW yielded 468 characters across 20 pages -- all
# of them page numbers -- and both pdftotext and pypdf returned nothing else.
# Without this check the extractor would run happily on an empty string and
# report that the document contained no requirements.
MIN_CHARS_PER_PAGE = 200


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


def requirement_coverage(requirements: list[Requirement],
                         answered_refs: set) -> tuple[int, list[str]]:
    """(answered, missing refs). A missing requirement is a lost mark."""
    missing = [r.ref for r in requirements if r.ref not in (answered_refs or set())]
    return len(requirements) - len(missing), missing
