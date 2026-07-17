"""
Sprint 5 — document-production engine.

Turns chat drafts into a downloadable, formatted Word (.docx) proposal.

Design note (circular-import avoidance):
  This module MUST NOT import app.py. app.py imports `generate_proposal` from
  here, so the dependency flows one way only. The reusable brain helpers
  (embed_query, retrieve_chunks, build_grounded_system, run_compliance_matrix,
  render_matrix_markdown) are PASSED IN as callables by the endpoint. That keeps
  the module importable with no secrets present, which is what lets the smoke
  test exercise it offline by passing stubs.

  The single OpenRouter network call is isolated in ``draft_with_openrouter`` so
  tests can monkeypatch exactly that one function.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

import httpx
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

import branding
from proposal_templates import (
    COMPLIANCE_SECTION_ID,
    SUBSECTION_FACETS,
    DepthTier,
    SectionSpec,
    get_depth_tier,
    get_template,
)

log = logging.getLogger("sarvam-brain.doc-engine")

# --- config (env with safe defaults; NEVER required at import) --------------
# app.py hard-requires OPENROUTER_API_KEY at import; this module must not, so
# that it stays importable in a keyless environment (smoke test / CI).
OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
# Hardcoded LLM models (kept in sync with app.py). document_engine must NOT
# import app (circular-import rule), so the constants are duplicated here.
PRIMARY_LLM_MODEL = "z-ai/glm-5.2"
FALLBACK_LLM_MODEL = "qwen/qwen3-235b-a22b-2507"
TOP_K = int(os.environ.get("TOP_K", "8"))
DOC_CONCURRENCY = int(os.environ.get("DOC_CONCURRENCY", os.environ.get("COMPLIANCE_CONCURRENCY", "3")))

# Below this max-similarity the evidence is considered weak and the section is
# flagged for SME review.
WEAK_EVIDENCE_THRESHOLD = float(os.environ.get("DOC_WEAK_EVIDENCE", "0.55"))

SME_REVIEW_MARKER = "[SME REVIEW]"

# Type aliases for the passed-in brain helpers.
EmbedFn = Callable[[httpx.AsyncClient, str], Awaitable[list[float]]]
RetrieveFn = Callable[..., Awaitable[list[dict]]]
BuildSystemFn = Callable[[list[dict]], str]

_SECTION_SYSTEM_TEMPLATE = """You are Sarvam, InspiritVision's internal proposal assistant (an IAM consulting firm).
You are drafting the "{title}" section of a {proposal_type} proposal for {client_name}{vendor_clause}.

SECTION PURPOSE: {purpose}

HARD RULES (non-negotiable):
1. Ground every material technical claim in the EVIDENCE below. Cite inline like [1], [3] referring to evidence numbers.
2. NEVER invent product versions, compliance/regulatory claims, pricing, SLAs, or client commitments.
   If the evidence does not support something you need, write a literal "{marker}" note explaining what is missing.
3. Draft ONLY this section as clean proposal prose (no markdown headings — the heading is added by the document builder). Be concise and specific; no filler.
4. Prefer specific technical content from the evidence (architectures, connectors, workflows, timelines, volumetrics) over generic methodology boilerplate.
5. This is a DRAFT for human review, never client-ready.

{evidence}
"""


def _vendor_clause(iam_vendor: Optional[str]) -> str:
    return f" using {iam_vendor}" if iam_vendor else ""


# Hard ceiling on any single draft call's token budget. Pass 3 depth tiers vary
# the budget DOWN for leaner tiers but must never raise a call above this — depth
# comes from more (fanned-out) calls, not from one runaway call.
MAX_DRAFT_TOKENS = 1500


def _draft_payload(model: str, system_prompt: str, user_prompt: str,
                   include_frequency_penalty: bool = True,
                   max_tokens: int = MAX_DRAFT_TOKENS) -> dict:
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "stream": False,
        "temperature": 0.4,
        # Cap runaway generation (same repetition-spiral risk as the compliance
        # classifier). LOW frequency_penalty preserves grounded citation/vendor terms.
        # Clamp to the hard ceiling so a bad depth config can never inflate a call.
        "max_tokens": min(int(max_tokens), MAX_DRAFT_TOKENS),
    }
    if include_frequency_penalty:
        payload["frequency_penalty"] = 0.2
    return payload


async def _post_draft(client: httpx.AsyncClient, payload: dict) -> str:
    resp = await client.post(
        f"{OPENROUTER_BASE}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=180,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


async def draft_with_openrouter(
    client: httpx.AsyncClient,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = MAX_DRAFT_TOKENS,
) -> str:
    """Isolated OpenRouter chat call with primary->fallback. Monkeypatched by the smoke test.

    Tries PRIMARY_LLM_MODEL, then FALLBACK_LLM_MODEL on HTTP/network/timeout
    error. Defensive: a 400 caused by an unsupported param (frequency_penalty)
    triggers a same-model retry WITHOUT frequency_penalty before falling back.

    ``max_tokens`` is the per-call budget from the active depth tier; it is
    clamped to MAX_DRAFT_TOKENS inside ``_draft_payload``.
    """
    last_exc: Exception | None = None
    for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
        try:
            content = await _post_draft(
                client, _draft_payload(model, system_prompt, user_prompt, max_tokens=max_tokens))
            log.info("OpenRouter draft model=%s", model)
            return content
        except httpx.HTTPStatusError as e:
            last_exc = e
            # A 400 may be an unsupported-param error (e.g. frequency_penalty).
            # Retry the SAME model once without it before falling back.
            if e.response is not None and e.response.status_code == 400:
                try:
                    content = await _post_draft(
                        client, _draft_payload(model, system_prompt, user_prompt,
                                               include_frequency_penalty=False,
                                               max_tokens=max_tokens))
                    log.info("OpenRouter draft model=%s (no frequency_penalty)", model)
                    return content
                except (httpx.HTTPStatusError, httpx.RequestError) as e2:
                    last_exc = e2
            if model == FALLBACK_LLM_MODEL:
                raise
            log.warning("draft failed on primary %s (%s); falling back to %s",
                        model, e, FALLBACK_LLM_MODEL)
        except httpx.RequestError as e:
            last_exc = e
            if model == FALLBACK_LLM_MODEL:
                raise
            log.warning("draft failed on primary %s (%s); falling back to %s",
                        model, e, FALLBACK_LLM_MODEL)
    raise last_exc  # pragma: no cover


def _fanout_queries(section_spec: SectionSpec, context: dict, fanout: int) -> list[str]:
    """Build up to ``fanout`` distinct retrieval queries for a section.

    Query 0 is the section's base query. Extra queries append a subsection facet
    keyword so retrieval surfaces evidence for different aspects of the section.
    """
    base = section_spec.render_query(context)
    if fanout <= 1:
        return [base]
    queries = [base]
    for _title, facet in SUBSECTION_FACETS[: max(0, fanout - 1)]:
        queries.append(f"{base} — {facet}")
    return queries[:fanout]


async def _retrieve_fanout(
    client: httpx.AsyncClient,
    section_spec: SectionSpec,
    context: dict,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    top_k: int,
    fanout: int,
) -> list[dict]:
    """Run fanned-out retrieval, then merge + dedupe chunks by text.

    Deduped set is sorted by similarity (desc) and capped so wider fan-out gives
    richer evidence without an unbounded evidence block.
    """
    seen: set[str] = set()
    merged: list[dict] = []
    for query in _fanout_queries(section_spec, context, fanout):
        try:
            embedding = await embed_fn(client, query)
            chunks = await retrieve_fn(client, embedding, query, k=top_k)
        except Exception as e:  # fail soft: one failed query must not sink the section
            log.error("draft_section retrieval failed for %s: %s", section_spec.id, e)
            chunks = []
        for c in chunks:
            key = (c.get("chunk_text") or c.get("heading") or "")[:160]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(c)
    merged.sort(key=lambda c: float(c.get("similarity") or 0.0), reverse=True)
    # Cap evidence to keep prompts bounded: base top_k, plus headroom per extra query.
    cap = top_k * max(1, fanout)
    return merged[:cap]


async def draft_section(
    client: httpx.AsyncClient,
    section_spec: SectionSpec,
    context: dict,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    build_grounded_system_fn: BuildSystemFn,
    top_k: int = TOP_K,
    fanout: int = 1,
    subsections: int = 1,
    max_tokens: int = MAX_DRAFT_TOKENS,
) -> dict:
    """Draft one proposal section, grounded in retrieved corpus evidence.

    Depth controls (Pass 3):
      fanout      : number of retrieval queries merged for this section's evidence.
      subsections : number of INDEPENDENT drafting calls. >1 splits the section
                    into focused facets (Overview / Detailed Design / ...), each
                    its own LLM call with the per-call ``max_tokens`` budget — so
                    depth grows via more calls, never a bigger single call.

    Returns: {"id","title","content","subsections","citations","max_similarity",
              "needs_sme_review"}.
    """
    chunks = await _retrieve_fanout(
        client, section_spec, context,
        embed_fn=embed_fn, retrieve_fn=retrieve_fn, top_k=top_k, fanout=fanout,
    )

    max_similarity = max((float(c.get("similarity") or 0.0) for c in chunks), default=0.0)
    needs_sme_review = (not chunks) or (max_similarity < WEAK_EVIDENCE_THRESHOLD)

    # Reuse the brain's evidence/system-prompt builder, then layer section-specific
    # drafting instructions on top so the model drafts THIS section.
    evidence_block = build_grounded_system_fn(chunks)
    system_prompt = _SECTION_SYSTEM_TEMPLATE.format(
        title=section_spec.title,
        proposal_type=context.get("proposal_type", "implementation"),
        client_name=context.get("client_name", "the client"),
        vendor_clause=_vendor_clause(context.get("iam_vendor")),
        purpose=section_spec.purpose,
        marker=SME_REVIEW_MARKER,
        evidence=evidence_block,
    )
    rfp_ctx = (context.get("rfp_text") or "")[:4000]

    # Plan the subsection facets. subsections<=1 keeps the original single-call
    # behaviour (facet list empty -> one whole-section draft).
    n_sub = max(1, min(int(subsections), len(SUBSECTION_FACETS)))
    facets = SUBSECTION_FACETS[:n_sub] if n_sub > 1 else []

    async def _draft_once(user_prompt: str) -> str:
        return await draft_with_openrouter(client, system_prompt, user_prompt, max_tokens=max_tokens)

    subsection_results: list[dict] = []
    drafting_failed = False
    if not facets:
        user_prompt = (
            f"Draft the \"{section_spec.title}\" section now, grounded in the EVIDENCE and citing inline as [N].\n\n"
            f"RFP / requirement context:\n{rfp_ctx}"
        )
        try:
            content = (await _draft_once(user_prompt)).strip()
        except Exception as e:
            log.error("draft_section drafting failed for %s: %s", section_spec.id, e)
            content = (
                f"{SME_REVIEW_MARKER}: drafting failed for this section ({e}). "
                "A subject-matter expert must author it manually."
            )
            drafting_failed = True
    else:
        # Independent drafting call per facet (the structured fan-out).
        parts: list[str] = []
        for sub_title, facet in facets:
            user_prompt = (
                f"Draft the \"{sub_title}\" subsection of the \"{section_spec.title}\" section, "
                f"focusing specifically on {facet}. Ground every claim in the EVIDENCE and cite "
                f"inline as [N]. Do not repeat content that belongs in other subsections.\n\n"
                f"RFP / requirement context:\n{rfp_ctx}"
            )
            try:
                sub_content = (await _draft_once(user_prompt)).strip()
            except Exception as e:
                log.error("draft_section subsection %s failed for %s: %s", sub_title, section_spec.id, e)
                sub_content = (
                    f"{SME_REVIEW_MARKER}: drafting failed for this subsection ({e}). "
                    "A subject-matter expert must author it manually."
                )
                drafting_failed = True
            subsection_results.append({"title": sub_title, "content": sub_content})
            parts.append(f"### {sub_title}\n\n{sub_content}")
        content = "\n\n".join(parts).strip()

    if drafting_failed:
        needs_sme_review = True

    content = (content or "").strip()
    if needs_sme_review and not content.startswith("[SME REVIEW"):
        content = (
            f"[SME REVIEW: weak evidence] Retrieval found "
            f"{'no' if not chunks else 'only low-similarity'} supporting evidence "
            f"(max similarity {max_similarity:.2f}). Verify and expand with an SME.\n\n"
            + content
        )

    return {
        "id": section_spec.id,
        "title": section_spec.title,
        "content": content,
        "subsections": subsection_results,
        "citations": chunks,
        "max_similarity": max_similarity,
        "needs_sme_review": needs_sme_review,
    }


# ---------------------------------------------------------------------------
# DOCX assembly
# ---------------------------------------------------------------------------

_DRAFT_COLOR = RGBColor(0xB0, 0x00, 0x00)  # warning red (SME-review flag)
_CITATION_RE = re.compile(r"\[(\d+)\]")



def _add_toc_field(document: Document) -> None:
    """Insert a real, F9-refreshable Word Table of Contents field.

    Until the user updates the field in Word (right-click -> Update Field, or
    F9), Word shows the instructional placeholder text below.
    """
    para = document.add_paragraph()
    run = para.add_run()
    r = run._r
    fld_begin = OxmlElement("w:fldChar")
    fld_begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = r'TOC \o "1-3" \h \z \u'
    fld_sep = OxmlElement("w:fldChar")
    fld_sep.set(qn("w:fldCharType"), "separate")
    placeholder = OxmlElement("w:t")
    placeholder.text = (
        "Right-click here and choose 'Update Field' (or press F9) to build the "
        "table of contents from the section headings below."
    )
    fld_end = OxmlElement("w:fldChar")
    fld_end.set(qn("w:fldCharType"), "end")
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(placeholder)
    r.append(fld_end)


def _add_body_paragraphs(document: Document, text: str) -> None:
    """Add body text, preserving [N] citation markers, splitting on blank lines."""
    for block in re.split(r"\n\s*\n", text.strip()):
        block = block.strip()
        if not block:
            continue
        para = document.add_paragraph()
        # Bullet-ify simple leading dashes/bullets for readability.
        if block.lstrip().startswith(("- ", "* ", "•")):
            for line in block.splitlines():
                line = line.strip().lstrip("-*• ").strip()
                if line:
                    document.add_paragraph(line, style="List Bullet")
            # remove the empty placeholder paragraph we created above
            p = para._element
            p.getparent().remove(p)
            continue
        para.add_run(block)


def assemble_docx(
    metadata: dict,
    sections: list[dict],
    compliance_markdown: Optional[str] = None,
    client_logo_path: Optional[str] = None,
    include_appendices: bool = False,
) -> bytes:
    """Build a professional, IV-branded Word document and return its bytes.

    metadata keys: client_name, proposal_type, iam_vendor (optional),
    generated_at (optional), version (optional).

    client_logo_path: optional path to a client logo image embedded in the
    title-page box. When None (default) a bordered "Client Logo" placeholder is
    drawn instead. Logos are never sourced online in this pass.

    include_appendices: when True (full depth) appends the appendix pack —
    RACI, timeline, sizing, integration inventory and risks — as real DOCX
    tables. Where intake/retrieval data is absent, conservative
    assumption-marked placeholder rows are used (never fabricated specifics).
    """
    metadata = {
        **metadata,
        "generated_at": metadata.get("generated_at")
        or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }

    document = Document()
    branding.configure_base_styles(document)
    branding.apply_header_footer(document, metadata.get("client_name") or "Client")

    # --- Title page (IV branding) ------------------------------------------
    branding.add_title_page(document, metadata, client_logo_path=client_logo_path)
    document.add_page_break()

    # --- Table of contents (real, refreshable Word TOC field) --------------
    branding.add_section_heading(document, "Table of Contents")
    _add_toc_field(document)
    document.add_page_break()

    # --- Sections -----------------------------------------------------------
    aggregated_assumptions: list[str] = []
    for sec in sections:
        heading = branding.add_section_heading(document, sec.get("title", "Untitled"))
        if sec.get("needs_sme_review"):
            flag = heading.add_run("   [SME REVIEW REQUIRED]")
            flag.font.size = Pt(10)
            flag.font.color.rgb = _DRAFT_COLOR
        subs = sec.get("subsections") or []
        if subs:
            # Multi-subsection (full depth): render each facet under an H2 heading.
            for sub in subs:
                document.add_heading(sub.get("title", "Untitled"), level=2)
                _add_body_paragraphs(document, sub.get("content", ""))
        else:
            _add_body_paragraphs(document, sec.get("content", ""))

        # Opportunistically collect assumption-ish lines for the aggregate section.
        if "assumption" not in (sec.get("id") or "").lower():
            for line in (sec.get("content") or "").splitlines():
                s = line.strip().lstrip("-*• ").strip()
                if s and re.match(r"(?i)^(assumption|assume|open question|dependency)", s):
                    aggregated_assumptions.append(s)

    # --- Assumptions & Open Questions (ensure one always exists) ------------
    has_dedicated_assumptions = any("assumption" in (s.get("id") or "").lower() for s in sections)
    if not has_dedicated_assumptions:
        branding.add_section_heading(document, "Assumptions & Open Questions")
        if aggregated_assumptions:
            for a in aggregated_assumptions:
                document.add_paragraph(a, style="List Bullet")
        else:
            document.add_paragraph(
                "No explicit assumptions were captured during drafting. "
                "An SME should confirm scope, dependencies, and open questions before client use.",
                style="List Bullet",
            )

    # --- Compliance Matrix (optional) --------------------------------------
    if compliance_markdown:
        document.add_page_break()
        branding.add_section_heading(document, "Compliance Matrix")
        _add_markdown_ish(document, compliance_markdown)

    # --- Citation Appendix --------------------------------------------------
    document.add_page_break()
    branding.add_section_heading(document, "Citation Appendix")
    intro = document.add_paragraph()
    irun = intro.add_run(
        "Each [N] marker in the draft above maps to a source chunk from Inspirit Vision's "
        "past-proposal corpus. Citations restart per section."
    )
    irun.italic = True
    irun.font.size = Pt(10)

    any_citation = False
    for sec in sections:
        citations = sec.get("citations") or []
        if not citations:
            continue
        any_citation = True
        document.add_heading(sec.get("title", "Untitled"), level=2)
        for i, c in enumerate(citations, 1):
            para = document.add_paragraph(style="List Number")
            src = (
                f"{c.get('client_name') or 'unknown client'} "
                f"({c.get('iam_vendor') or 'n/a'}) — "
                f"{c.get('heading') or 'untitled section'}"
            )
            if c.get("similarity") is not None:
                src += f"  [similarity {float(c.get('similarity') or 0):.2f}]"
            para.add_run(src)
    if not any_citation:
        document.add_paragraph(
            "No corpus citations were available for this draft — all content requires SME sourcing."
        )

    # --- Appendices (full depth only) --------------------------------------
    if include_appendices:
        _add_appendices(document, metadata)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _add_markdown_ish(document: Document, text: str) -> None:
    """Render the compliance matrix markdown into DOCX in a robust, simple way.

    We don't build a full markdown parser: headings (#) become paragraphs, GFM
    tables become native Word tables, everything else becomes plain paragraphs.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        # GFM table block
        if stripped.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("|-: "):
            header_cells = [c.strip() for c in stripped.strip("|").split("|")]
            i += 2  # skip header + separator
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            table = document.add_table(rows=1, cols=len(header_cells))
            table.style = "Light Grid Accent 1"
            for j, h in enumerate(header_cells):
                run = table.rows[0].cells[j].paragraphs[0].add_run(h)
                run.bold = True
            for r in rows:
                cells = table.add_row().cells
                for j in range(len(header_cells)):
                    cells[j].text = r[j] if j < len(r) else ""
            continue
        if stripped.startswith("#"):
            para = document.add_paragraph()
            run = para.add_run(stripped.lstrip("# ").strip())
            run.bold = True
            i += 1
            continue
        if stripped.startswith(">"):
            note = document.add_paragraph()
            nr = note.add_run(stripped.lstrip("> ").strip())
            nr.italic = True
            i += 1
            continue
        document.add_paragraph(stripped)
        i += 1


# ---------------------------------------------------------------------------
# Appendices (Pass 3 — full depth). Deterministic, no LLM calls, no fabrication.
# Missing intake/retrieval data yields conservative assumption-marked rows.
# ---------------------------------------------------------------------------

_APPENDIX_ASSUMPTION = "[ASSUMPTION]"


def _appendix_note(document: Document, text: str) -> None:
    note = document.add_paragraph()
    run = note.add_run(text)
    run.italic = True
    run.font.size = Pt(10)


def _appendix_table(document: Document, headers: list[str], rows: list[list[str]]) -> None:
    """Render a native Word table with a bold header row."""
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Light Grid Accent 1"
    for j, h in enumerate(headers):
        run = table.rows[0].cells[j].paragraphs[0].add_run(h)
        run.bold = True
    for row in rows:
        cells = table.add_row().cells
        for j in range(len(headers)):
            cells[j].text = row[j] if j < len(row) else ""


def _add_appendices(document: Document, metadata: dict) -> None:
    """Append the full-depth appendix pack as real DOCX sections/tables.

    Every row is either grounded in supplied metadata or an explicit
    ``[ASSUMPTION]`` placeholder — SMEs must confirm before client use. No
    specific figures, dates or SLAs are fabricated.
    """
    vendor = (metadata.get("iam_vendor") or "the selected IAM platform").strip() or "the selected IAM platform"
    ptype = (metadata.get("proposal_type") or "implementation").strip().lower()

    document.add_page_break()
    branding.add_section_heading(document, "Appendices")
    _appendix_note(
        document,
        "The following appendices are structured planning artefacts. Rows marked "
        f"{_APPENDIX_ASSUMPTION} are conservative placeholders to be confirmed with the "
        "client and an SME during discovery — they are not commitments.",
    )

    # A. RACI matrix
    branding.add_section_heading(document, "Appendix A — RACI Matrix")
    _appendix_table(
        document,
        ["Activity / Workstream", "InspiritVision", "Client", "Vendor"],
        [
            ["Solution design & architecture", "R/A", "C", "C"],
            ["Environment provisioning", "C", "R/A", "C"],
            ["Configuration & build", "R/A", "C", "I"],
            ["Integration & connector setup", "R/A", "C", "C"],
            ["Testing & UAT", "R", "A", "I"],
            ["Go-live & cutover", "R/A", "C", "I"],
            ["Knowledge transfer & handover", "R/A", "C", "I"],
            [f"{_APPENDIX_ASSUMPTION} Additional workstreams", "TBC", "TBC", "TBC"],
        ],
    )
    _appendix_note(document, "R = Responsible, A = Accountable, C = Consulted, I = Informed.")

    # B. Timeline / phasing
    branding.add_section_heading(document, "Appendix B — Indicative Timeline")
    _appendix_table(
        document,
        ["Phase", "Key Activities", "Indicative Duration"],
        [
            ["Discovery & Design", "Requirements, current-state review, target design", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Build & Configure", "Platform config, connectors, workflows", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Test & Validate", "SIT, UAT, remediation", f"{_APPENDIX_ASSUMPTION} TBC"],
            ["Deploy & Stabilise", "Cutover, hypercare, handover", f"{_APPENDIX_ASSUMPTION} TBC"],
        ],
    )
    _appendix_note(document, "Durations are confirmed once scope and volumetrics are baselined in discovery.")

    # C. Sizing
    branding.add_section_heading(document, "Appendix C — Sizing & Volumetrics")
    _appendix_table(
        document,
        ["Dimension", "Value", "Source"],
        [
            ["Identities / users", f"{_APPENDIX_ASSUMPTION} TBC", "Client to confirm"],
            ["Target applications", f"{_APPENDIX_ASSUMPTION} TBC", "Client to confirm"],
            ["Environments", f"{_APPENDIX_ASSUMPTION} TBC", "Client to confirm"],
            ["Peak transaction volume", f"{_APPENDIX_ASSUMPTION} TBC", "Client to confirm"],
        ],
    )

    # D. Integration inventory
    branding.add_section_heading(document, "Appendix D — Integration Inventory")
    _appendix_table(
        document,
        ["System / Application", "Integration Type", f"{vendor} Connector", "Notes"],
        [
            ["Directory / HR source", "Authoritative source", f"{_APPENDIX_ASSUMPTION} TBC", "System of record for identities"],
            ["Core business applications", "Provisioning target", f"{_APPENDIX_ASSUMPTION} TBC", "Confirm inventory in discovery"],
            ["Downstream / custom apps", f"{_APPENDIX_ASSUMPTION} TBC", f"{_APPENDIX_ASSUMPTION} TBC", "May require custom connector"],
        ],
    )

    # E. Risks
    branding.add_section_heading(document, "Appendix E — Risk Register")
    _appendix_table(
        document,
        ["Risk", "Likelihood", "Impact", "Mitigation"],
        [
            ["Scope / requirements change", "Medium", "High", "Change control, phased delivery"],
            ["Application onboarding delays", "Medium", "Medium", "Early inventory, prioritised backlog"],
            ["Data quality in source systems", "Medium", "High", "Data-quality assessment in discovery"],
            ["Resource / SME availability", "Medium", "Medium", "Agreed RACI and governance cadence"],
            [f"{_APPENDIX_ASSUMPTION} {ptype}-specific risks", "TBC", "TBC", "To be assessed with client"],
        ],
    )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

async def generate_proposal(
    client: httpx.AsyncClient,
    rfp_text: str,
    client_name: str,
    proposal_type: str,
    iam_vendor: Optional[str] = None,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    build_grounded_system_fn: BuildSystemFn,
    run_compliance_matrix_fn: Optional[Callable[..., Awaitable[object]]] = None,
    render_matrix_markdown_fn: Optional[Callable[[object], str]] = None,
    sections: Optional[list[str]] = None,
    include_compliance_matrix: bool = False,
    top_k: int = TOP_K,
    proposal_depth: Optional[str] = None,
) -> dict:
    """Orchestrate: pick template, draft sections concurrently, assemble DOCX.

    The brain helpers are injected so this module never imports app.py.

    ``proposal_depth`` (brief|standard|full) controls long-form depth via
    STRUCTURED fan-out — retrieval queries + independent drafting calls per
    section, plus (full) an appendix pack. Unknown/absent values fall back to the
    safe ``standard`` tier, preserving existing behaviour.
    Returns {"docx_bytes", "sections_meta", "filename", ...}.
    """
    template = get_template(proposal_type)  # raises ValueError on bad type
    tier: DepthTier = get_depth_tier(proposal_depth)

    # Choose which sections to draft. Compliance is opt-in and handled separately.
    if sections:
        wanted = {s.strip().lower() for s in sections}
        chosen = [s for s in template if s.id in wanted or s.title.lower() in wanted]
        if not chosen:
            chosen = [s for s in template if not s.optional]
    else:
        chosen = [s for s in template if not s.optional]

    draft_specs = [s for s in chosen if s.id != COMPLIANCE_SECTION_ID]

    context = {
        "client_name": client_name,
        "iam_vendor": iam_vendor or "",
        "proposal_type": proposal_type,
        "rfp_text": rfp_text or "",
    }

    sem = asyncio.Semaphore(DOC_CONCURRENCY)

    async def _draft(spec: SectionSpec) -> dict:
        async with sem:
            return await draft_section(
                client, spec, context,
                embed_fn=embed_fn,
                retrieve_fn=retrieve_fn,
                build_grounded_system_fn=build_grounded_system_fn,
                top_k=top_k,
                fanout=tier.retrieval_fanout,
                subsections=tier.subsections_per_section,
                max_tokens=tier.per_call_max_tokens,
            )

    drafted = await asyncio.gather(*[_draft(s) for s in draft_specs])

    # Optional compliance matrix.
    compliance_markdown: Optional[str] = None
    want_compliance = include_compliance_matrix or (
        sections is not None and COMPLIANCE_SECTION_ID in {s.strip().lower() for s in sections}
    )
    if want_compliance and run_compliance_matrix_fn and render_matrix_markdown_fn:
        try:
            matrix = await run_compliance_matrix_fn(client, rfp_text, None, top_k)
            compliance_markdown = render_matrix_markdown_fn(matrix)
        except Exception as e:
            log.error("Compliance matrix generation failed: %s", e)
            compliance_markdown = (
                f"{SME_REVIEW_MARKER}: compliance matrix generation failed ({e}). "
                "Run the /v1/compliance-matrix endpoint separately."
            )

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    metadata = {
        "client_name": client_name,
        "proposal_type": proposal_type,
        "iam_vendor": iam_vendor,
        "generated_at": generated_at,
    }
    docx_bytes = assemble_docx(
        metadata, list(drafted), compliance_markdown,
        include_appendices=tier.include_appendices,
    )

    safe_client = re.sub(r"[^A-Za-z0-9]+", "_", client_name).strip("_") or "Client"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"Sarvam_Proposal_{safe_client}_{stamp}.docx"

    sections_meta = [
        {
            "id": d["id"],
            "title": d["title"],
            "max_similarity": d["max_similarity"],
            "needs_sme_review": d["needs_sme_review"],
            "citation_count": len(d.get("citations") or []),
        }
        for d in drafted
    ]
    draft_markdown = "\n\n".join(
        f"## {d['title']}\n\n{d.get('content', '')}".rstrip() for d in drafted
    )

    return {
        "docx_bytes": docx_bytes,
        "sections_meta": sections_meta,
        "draft_markdown": draft_markdown,
        "filename": filename,
        "included_compliance_matrix": compliance_markdown is not None,
        "proposal_depth": tier.name,
        "included_appendices": tier.include_appendices,
    }
