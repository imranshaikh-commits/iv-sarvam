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
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

from proposal_templates import (
    COMPLIANCE_SECTION_ID,
    SectionSpec,
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


def _draft_payload(model: str, system_prompt: str, user_prompt: str,
                   include_frequency_penalty: bool = True) -> dict:
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
        "max_tokens": 1500,
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
) -> str:
    """Isolated OpenRouter chat call with primary->fallback. Monkeypatched by the smoke test.

    Tries PRIMARY_LLM_MODEL, then FALLBACK_LLM_MODEL on HTTP/network/timeout
    error. Defensive: a 400 caused by an unsupported param (frequency_penalty)
    triggers a same-model retry WITHOUT frequency_penalty before falling back.
    """
    last_exc: Exception | None = None
    for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
        try:
            content = await _post_draft(client, _draft_payload(model, system_prompt, user_prompt))
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
                                               include_frequency_penalty=False))
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


async def draft_section(
    client: httpx.AsyncClient,
    section_spec: SectionSpec,
    context: dict,
    *,
    embed_fn: EmbedFn,
    retrieve_fn: RetrieveFn,
    build_grounded_system_fn: BuildSystemFn,
    top_k: int = TOP_K,
) -> dict:
    """Draft one proposal section, grounded in retrieved corpus evidence.

    Returns: {"id", "title", "content", "citations", "max_similarity", "needs_sme_review"}.
    """
    query = section_spec.render_query(context)
    try:
        embedding = await embed_fn(client, query)
        chunks = await retrieve_fn(client, embedding, query, k=top_k)
    except Exception as e:  # fail soft: produce an SME-review stub, not a 500
        log.error("draft_section retrieval failed for %s: %s", section_spec.id, e)
        chunks = []

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
    user_prompt = (
        f"Draft the \"{section_spec.title}\" section now, grounded in the EVIDENCE and citing inline as [N].\n\n"
        f"RFP / requirement context:\n{(context.get('rfp_text') or '')[:4000]}"
    )

    try:
        content = await draft_with_openrouter(client, system_prompt, user_prompt)
    except Exception as e:
        log.error("draft_section drafting failed for %s: %s", section_spec.id, e)
        content = (
            f"{SME_REVIEW_MARKER}: drafting failed for this section ({e}). "
            "A subject-matter expert must author it manually."
        )
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
        "citations": chunks,
        "max_similarity": max_similarity,
        "needs_sme_review": needs_sme_review,
    }


# ---------------------------------------------------------------------------
# DOCX assembly
# ---------------------------------------------------------------------------

_BODY_FONT = "Calibri"
_TITLE_COLOR = RGBColor(0x1F, 0x3A, 0x5F)  # deep navy
_DRAFT_COLOR = RGBColor(0xB0, 0x00, 0x00)  # warning red
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


def _set_base_font(document: Document) -> None:
    style = document.styles["Normal"]
    style.font.name = _BODY_FONT
    style.font.size = Pt(11)
    # Ensure the east-asian font mapping also uses the base font (docx quirk).
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:ascii"), _BODY_FONT)
    rfonts.set(qn("w:hAnsi"), _BODY_FONT)


def _add_page_number_footer(document: Document) -> None:
    """Footer with 'DRAFT — Internal Use Only' and a live Page N field."""
    section = document.sections[0]
    footer = section.footer
    footer.is_linked_to_previous = False
    para = footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para.text = ""

    run = para.add_run("DRAFT — Internal Use Only  |  Page ")
    run.font.size = Pt(8)
    run.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    # PAGE field: <w:fldSimple w:instr="PAGE">
    fld = para.add_run()
    fld.font.size = Pt(8)
    fld.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
    fldchar_begin = fld._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "begin"})
    instr = fld._r.makeelement(qn("w:instrText"), {qn("xml:space"): "preserve"})
    instr.text = "PAGE"
    fldchar_end = fld._r.makeelement(qn("w:fldChar"), {qn("w:fldCharType"): "end"})
    fld._r.append(fldchar_begin)
    fld._r.append(instr)
    fld._r.append(fldchar_end)


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
) -> bytes:
    """Build a professional Word document and return its bytes.

    metadata keys: client_name, proposal_type, iam_vendor (optional), generated_at (optional).
    """
    client_name = metadata.get("client_name") or "Client"
    proposal_type = metadata.get("proposal_type") or "implementation"
    iam_vendor = metadata.get("iam_vendor") or ""
    generated_at = metadata.get("generated_at") or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    document = Document()
    _set_base_font(document)
    _add_page_number_footer(document)

    # --- Title page ---------------------------------------------------------
    for _ in range(3):
        document.add_paragraph()
    title = document.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    trun = title.add_run(f"{proposal_type.upper()} PROPOSAL")
    trun.bold = True
    trun.font.size = Pt(28)
    trun.font.color.rgb = _TITLE_COLOR

    subtitle = document.add_paragraph()
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    srun = subtitle.add_run(f"Prepared for {client_name}")
    srun.font.size = Pt(18)
    srun.font.color.rgb = _TITLE_COLOR

    if iam_vendor:
        vend = document.add_paragraph()
        vend.alignment = WD_ALIGN_PARAGRAPH.CENTER
        vrun = vend.add_run(f"IAM Platform: {iam_vendor}")
        vrun.font.size = Pt(13)

    document.add_paragraph()
    draft = document.add_paragraph()
    draft.alignment = WD_ALIGN_PARAGRAPH.CENTER
    drun = draft.add_run("DRAFT — FOR INTERNAL REVIEW ONLY")
    drun.bold = True
    drun.font.size = Pt(14)
    drun.font.color.rgb = _DRAFT_COLOR

    ts = document.add_paragraph()
    ts.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tsrun = ts.add_run(f"Generated by Sarvam on {generated_at}")
    tsrun.italic = True
    tsrun.font.size = Pt(10)

    prep = document.add_paragraph()
    prep.alignment = WD_ALIGN_PARAGRAPH.CENTER
    prun = prep.add_run("Inspirit Vision — AI Proposal Architect")
    prun.font.size = Pt(10)
    prun.font.color.rgb = RGBColor(0x80, 0x80, 0x80)

    document.add_page_break()

    # --- Table of contents (real, refreshable Word TOC field) --------------
    document.add_heading("Table of Contents", level=1)
    _add_toc_field(document)
    document.add_page_break()

    # --- Sections -----------------------------------------------------------
    aggregated_assumptions: list[str] = []
    for sec in sections:
        heading = document.add_heading(sec.get("title", "Untitled"), level=1)
        if sec.get("needs_sme_review"):
            flag = heading.add_run("   [SME REVIEW REQUIRED]")
            flag.font.size = Pt(10)
            flag.font.color.rgb = _DRAFT_COLOR
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
        document.add_heading("Assumptions & Open Questions", level=1)
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
        document.add_heading("Compliance Matrix", level=1)
        _add_markdown_ish(document, compliance_markdown)

    # --- Citation Appendix --------------------------------------------------
    document.add_page_break()
    document.add_heading("Citation Appendix", level=1)
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
) -> dict:
    """Orchestrate: pick template, draft sections concurrently, assemble DOCX.

    The brain helpers are injected so this module never imports app.py.
    Returns {"docx_bytes", "sections_meta", "filename"}.
    """
    template = get_template(proposal_type)  # raises ValueError on bad type

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
    docx_bytes = assemble_docx(metadata, list(drafted), compliance_markdown)

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
    return {
        "docx_bytes": docx_bytes,
        "sections_meta": sections_meta,
        "filename": filename,
        "included_compliance_matrix": compliance_markdown is not None,
    }
