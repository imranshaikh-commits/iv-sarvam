"""
sarvam-brain — Phase 1 MVP
==========================
OpenAI-compatible API that Open WebUI treats as a model ("Sarvam Architect").

Flow per user message:
  1. Embed the query  (openai/text-embedding-3-small via OpenRouter — MUST match ingest)
  2. Retrieve top-k chunks from Supabase pgvector (match_proposal_chunks RPC)
  3. Build grounded system prompt with numbered evidence + safety rails
  4. Draft with DeepSeek via OpenRouter (streaming or non-streaming)

Endpoints:
  GET  /health
  GET  /v1/models
  POST /v1/chat/completions
"""

import asyncio
import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Literal

import httpx
import instructor
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# Sprint 5 document-production engine. Safe top-level import: document_engine
# does NOT import app (it receives the brain helpers as parameters), so there
# is no circular dependency.
from document_engine import generate_proposal

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sarvam-brain")

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "openai/text-embedding-3-small"   # must match scripts/ingest_v2.py
DRAFT_MODEL = os.environ.get("DRAFT_MODEL", "deepseek/deepseek-v3.2-exp")
TOP_K = int(os.environ.get("TOP_K", "8"))
MODEL_ID = "sarvam-architect"

# Compliance-matrix (Sprint 4 Phase 2)
COMPLIANCE_CONCURRENCY = int(os.getenv("COMPLIANCE_CONCURRENCY", "3"))
MAX_REQUIREMENTS = int(os.getenv("MAX_REQUIREMENTS", "20"))
COMPLIANCE_TRIGGER = "compliance matrix"
# Separate model for structured extraction/classification so it can be swapped via env
# (without a code rebuild) if DeepSeek structured outputs misbehave on OpenRouter.
STRUCTURED_MODEL = os.getenv("STRUCTURED_MODEL", DRAFT_MODEL)

app = FastAPI(title="sarvam-brain")

SYSTEM_PROMPT = """You are Sarvam, InspiritVision's internal proposal assistant (an IAM consulting firm).
You answer questions and draft proposal content grounded in IV's past proposals, provided below as EVIDENCE.

HARD RULES (non-negotiable):
1. Ground every material technical claim in the EVIDENCE. Cite inline like [1], [3] referring to evidence numbers.
2. NEVER invent product versions, compliance/regulatory claims, pricing, or client commitments.
   If the evidence does not support a claim you need, write: (needs SME confirmation).
3. If the evidence is irrelevant to the question, say so plainly rather than forcing citations.
4. When drafting proposal sections, end with an "Assumptions & Open Questions" list if any exist.
5. You are the chief of staff, not the final author: your output is a draft for human review, never client-ready.

QUALITY RULES:
- Prioritize SPECIFIC technical content from the evidence — architectures, product capabilities, connectors,
  workflows, configurations, timelines, volumetrics, integration points — over generic methodology steps.
- Do NOT pad answers with generic project-management phases (Analysis / Testing / Knowledge Transfer) unless
  they are directly and specifically supported by cited evidence with real detail.
- If the retrieved evidence is mostly generic methodology boilerplate rather than specific technical detail for
  the question asked, SAY SO explicitly and note what specific content would be needed to answer properly.
- Be concrete: quote specific requirements, table contents, milestones, or configuration details from the evidence.

Write in clear, professional consulting English. Be concise; no filler.
"""


async def embed_query(client: httpx.AsyncClient, text: str) -> list[float]:
    resp = await client.post(
        f"{OPENROUTER_BASE}/embeddings",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={"model": EMBED_MODEL, "input": [text[:8000]]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["data"][0]["embedding"]


# Vendor keywords → normalized token used to match proposals.iam_vendor (case-insensitive).
VENDOR_KEYWORDS = ["sailpoint", "ping", "forgerock", "ibm", "keycloak", "okta", "microsoft"]


def detect_vendor(query: str) -> str | None:
    q = query.lower()
    for kw in VENDOR_KEYWORDS:
        if kw in q:
            return kw
    return None


async def retrieve_chunks(client: httpx.AsyncClient, embedding: list[float], query: str, k: int = TOP_K) -> list[dict]:
    # Over-fetch then improve signal:
    #  - exclude "Inspirit Vision" company-overview boilerplate (identical marketing text in every proposal;
    #    never the specific answer)
    #  - fuzzy-dedup near-duplicates (e.g. "specialisation" vs "specialization" variants across proposals)
    #  - vendor-aware preference: if the query names a vendor, surface that vendor's chunks first so specific
    #    functional content (tables, requirements, milestones) ranks above cross-proposal methodology.
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_proposal_chunks",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json={"query_embedding": json.dumps(embedding, separators=(",", ":")), "match_count": k * 4},
        timeout=30,
    )
    resp.raise_for_status()
    rows = resp.json() or []

    # 1. Exclude pure company-overview boilerplate
    rows = [r for r in rows if not (r.get("heading") or "").lower().startswith("inspirit vision")]

    # 2. Fuzzy near-duplicate dedup on normalized text
    def _norm(t: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", t.lower())).strip()
    seen: list[str] = []
    deduped: list[dict] = []
    for r in rows:
        norm = _norm((r.get("chunk_text") or "")[:400])
        if not norm:
            continue
        dup = False
        for s in seen:
            if SequenceMatcher(None, norm, s).quick_ratio() > 0.9 and SequenceMatcher(None, norm, s).ratio() > 0.85:
                dup = True
                break
        if not dup:
            seen.append(norm)
            deduped.append(r)

    # 3. Vendor-aware preference (keeps similarity order within each group)
    vendor = detect_vendor(query)
    if vendor:
        deduped.sort(key=lambda r: (0 if vendor in (r.get("iam_vendor") or "").lower() else 1, -float(r.get("similarity") or 0)))
    return deduped[:k]


def build_grounded_system(chunks: list[dict]) -> str:
    lines = [SYSTEM_PROMPT, "\n=== EVIDENCE (from IV's past proposals) ===\n"]
    for i, c in enumerate(chunks, 1):
        head = c.get("heading") or "untitled section"
        lines.append(
            f"[{i}] (client: {c.get('client_name')}, vendor: {c.get('iam_vendor')}, "
            f"industry: {c.get('industry')}, section: {head}, similarity: {c.get('similarity', 0):.2f})\n"
            f"{c.get('chunk_text', '')}\n"
        )
    if not chunks:
        lines.append("(no relevant evidence found in the proposal corpus)")
    return "\n".join(lines)


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):  # multimodal format
                return " ".join(p.get("text", "") for p in content if p.get("type") == "text")
            return str(content)
    return ""


# ---------------------------------------------------------------------------
# Compliance matrix (Sprint 4 Phase 2)
# Paste RFP text -> structured requirement extraction (Instructor) ->
# per-requirement coverage check against the proposal corpus.
# ---------------------------------------------------------------------------

class Requirement(BaseModel):
    id: str = Field(..., description="Requirement identifier, e.g. REQ-001. Preserve RFP numbering if present.")
    text: str = Field(..., description="The requirement statement, lightly cleaned, single testable claim.")
    category: str | None = Field(None, description="Optional category: Security, Integration, Compliance, Performance, Support, etc.")


class ExtractedRequirements(BaseModel):
    requirements: list[Requirement]


class EvidenceRef(BaseModel):
    evidence_id: int = Field(..., description="The [N] evidence number from the provided EVIDENCE block (1-based).")
    quote: str = Field(..., description="Short verbatim quote from that evidence chunk supporting the assessment.")
    rationale: str = Field(..., description="Why this evidence is relevant to the requirement.")


class CoverageEntry(BaseModel):
    requirement_id: str
    requirement_text: str = ""
    status: Literal["covered", "partial", "missing", "needs-human"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    summary: str = Field(..., description="How IV's proposal corpus addresses this requirement, grounded in evidence.")
    recommendation: str = Field(..., description="Concrete next step: reuse a cited approach, draft a new section, or escalate to SME.")


class ComplianceMatrix(BaseModel):
    entries: list[CoverageEntry]
    overall_notes: str
    truncated: bool = False


_EXTRACT_PROMPT = (
    "You extract compliance requirements from RFP / tender text as a structured list. "
    "Preserve original numbering where present; otherwise assign REQ-001, REQ-002, ... "
    "Each requirement must be a single, testable statement. Do NOT merge multiple requirements into one. "
    "Output at most the requested number; if more exist, keep the most material ones."
)

_CLASSIFY_PROMPT = """You assess whether InspiritVision's past-proposal corpus COVERS a given RFP requirement.

STATUS DEFINITIONS (choose exactly one):
- covered: Direct internal evidence in the EVIDENCE clearly addresses the requirement.
- partial: Related evidence exists, but a gap, version difference, or assumption remains.
- missing: No relevant internal evidence found in the EVIDENCE.
- needs-human: Ambiguous, or high-risk (regulatory, certification, pricing, product-version, legal, SLA), or evidence is insufficient to make a confident claim.

HARD RULES (non-negotiable):
1. To mark a requirement "covered" or "partial" you MUST include at least one EvidenceRef. The quote field MUST be an EXACT substring copied character-for-character from the EVIDENCE block — do NOT paraphrase, reword, summarize, or conflate multiple chunks. Paraphrased quotes will be rejected.
2. If you cannot find an exact verbatim quote that supports the requirement, set status to "needs-human" and leave evidence_refs empty. Do NOT argue for "covered" in the summary if you have no verbatim quote.
3. evidence_id MUST be the [N] number of the chunk you copied the quote from. Do NOT invent or mismatch IDs.
4. If the only matching evidence is generic methodology boilerplate (project phases, generic KT/testing steps) rather than specific technical content, prefer "needs-human" or "partial" — never "covered".
5. For regulatory / compliance / certification / pricing / product-version / SLA claims, prefer "needs-human" unless the evidence states it explicitly (then "covered" is allowed, still pending human verification).
6. The summary and recommendation must reflect ONLY what the evidence supports. Do not invent capabilities, connectors, or commitments not in the evidence. The recommendation must be a concrete next step, NOT a status word.
7. This is a DRAFT internal compliance matrix for human review — never a client-ready commitment.
"""


_instructor_client = None


def instructor_client():
    """Lazy OpenAI-compatible client wrapped by Instructor.
    OPENROUTER_STRUCTURED_OUTPUTS = OpenRouter's native structured-output path
    (more reliable than generic JSON mode; OpenRouter handles model translation)."""
    global _instructor_client
    if _instructor_client is None:
        oa = AsyncOpenAI(base_url=OPENROUTER_BASE, api_key=OPENROUTER_API_KEY)
        _instructor_client = instructor.from_openai(oa, mode=instructor.Mode.OPENROUTER_STRUCTURED_OUTPUTS)
    return _instructor_client


async def extract_requirements(rfp_text: str) -> list[Requirement]:
    ic = instructor_client()
    resp: ExtractedRequirements = await ic.chat.completions.create(
        model=STRUCTURED_MODEL,
        temperature=0,
        max_retries=2,
        response_model=ExtractedRequirements,
        messages=[
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": f"Extract up to {MAX_REQUIREMENTS} compliance requirements from the following RFP text.\n\nRFP TEXT:\n{rfp_text[:12000]}"},
        ],
    )
    return resp.requirements


def build_evidence_block(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        head = c.get("heading") or "untitled section"
        lines.append(
            f"[{i}] (client: {c.get('client_name')}, vendor: {c.get('iam_vendor')}, section: {head})\n{c.get('chunk_text', '')}"
        )
    return "\n\n".join(lines) if lines else "(no relevant evidence found in the proposal corpus)"


async def classify_coverage(req: Requirement, chunks: list[dict]) -> CoverageEntry:
    ic = instructor_client()
    entry: CoverageEntry = await ic.chat.completions.create(
        model=STRUCTURED_MODEL,
        temperature=0,
        max_retries=2,
        response_model=CoverageEntry,
        messages=[
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": f"REQUIREMENT {req.id}:\n{req.text}\n\n=== EVIDENCE (from IV's past proposals) ===\n{build_evidence_block(chunks)}"},
        ],
    )
    entry.requirement_id = req.id
    entry.requirement_text = req.text
    return validate_coverage(entry, chunks)


def _norm_for_quote(s: str) -> str:
    """Normalize for fuzzy quote-containment checks (lowercase, alnum+space, collapsed)."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", (s or "").lower())).strip()


def _significant_tokens(s: str) -> set:
    """Content-bearing tokens (len>=4) of a string, for overlap matching."""
    return {w for w in _norm_for_quote(s).split() if len(w) >= 4}


def validate_coverage(entry: CoverageEntry, chunks: list[dict]) -> CoverageEntry:
    """Keep only evidence_refs whose quote is actually grounded in the cited chunk.

    Core anti-hallucination rail: a requirement can be 'covered'/'partial' only if the
    cited quote is verifiably drawn from the chunk at that evidence_id. We accept a ref if
    EITHER (a) the normalized quote appears verbatim in the chunk, OR (b) a strong
    majority (>=0.6) of the quote's significant tokens occur in the chunk — this catches
    faithful paraphrases while still blocking fabrication (a quote about terms absent from
    the chunk has ~0 overlap and is rejected). Retrieval already ensured the chunk is
    topically relevant; this check ensures the quote is grounded in its actual text."""
    valid: list[EvidenceRef] = []
    for ref in entry.evidence_refs:
        idx = (ref.evidence_id - 1) if isinstance(ref.evidence_id, int) else -1
        if not (0 <= idx < len(chunks)):
            continue
        quote = _norm_for_quote(ref.quote)
        if len(quote) < 12:
            continue
        sig = _significant_tokens(ref.quote)
        if not sig:
            continue
        chunk_text = _norm_for_quote(chunks[idx].get("chunk_text", ""))
        overlap = len(sig & set(chunk_text.split())) / len(sig)
        if quote in chunk_text or overlap >= 0.6:
            valid.append(ref)
    entry.evidence_refs = valid
    if entry.status in ("covered", "partial") and not valid:
        entry.status = "needs-human"
        banner = ("[DOWNGRADED to needs-human: the cited quotes could not be verified "
                   "against the retrieved evidence — treat as unverified.]")
        entry.summary = f"{banner} {entry.summary}".strip()
        entry.recommendation = (
            "Escalate to SME: the model could not produce a verbatim evidence quote — "
            "re-check retrieval for this requirement and confirm coverage manually."
        )
    return entry


async def run_compliance_matrix(
    client: httpx.AsyncClient, rfp_text: str, requirements: list[str] | None = None, top_k: int = TOP_K
) -> ComplianceMatrix:
    truncated = False
    if requirements:
        reqs = [Requirement(id=f"REQ-{i:03d}", text=t) for i, t in enumerate(requirements[:MAX_REQUIREMENTS], 1)]
        truncated = len(requirements) > MAX_REQUIREMENTS
    else:
        reqs = await extract_requirements(rfp_text)
        if len(reqs) > MAX_REQUIREMENTS:
            reqs = reqs[:MAX_REQUIREMENTS]
            truncated = True

    sem = asyncio.Semaphore(COMPLIANCE_CONCURRENCY)

    async def process(req: Requirement) -> CoverageEntry:
        async with sem:
            try:
                emb = await embed_query(client, req.text)
                chunks = await retrieve_chunks(client, emb, req.text, k=top_k)
                log.info("Compliance %s: retrieved %d chunks", req.id, len(chunks))
                return await classify_coverage(req, chunks)
            except Exception as e:
                log.error("Compliance classify failed for %s: %s", req.id, e)
                return CoverageEntry(
                    requirement_id=req.id, requirement_text=req.text, status="needs-human",
                    evidence_refs=[], summary=f"Classification failed: {e}",
                    recommendation="Escalate to SME and retry.",
                )

    entries = await asyncio.gather(*[process(r) for r in reqs])

    counts = {s: sum(1 for e in entries if e.status == s) for s in ("covered", "partial", "missing", "needs-human")}
    overall = (
        f"DRAFT internal compliance matrix — {len(entries)} requirements assessed. "
        f"Covered {counts['covered']}, partial {counts['partial']}, missing {counts['missing']}, needs-human {counts['needs-human']}. "
        f"For human review only; not a client-ready compliance commitment."
    )
    if truncated:
        overall += f" (Truncated: only the first {MAX_REQUIREMENTS} requirements assessed.)"
    return ComplianceMatrix(entries=list(entries), overall_notes=overall, truncated=truncated)


def render_matrix_markdown(matrix: ComplianceMatrix) -> str:
    out = ["# DRAFT Compliance Matrix", "", matrix.overall_notes, "",
           "| Req | Requirement | Status | Evidence | Summary | Next step |",
           "|---|---|---|---|---|---|"]
    label = {"covered": "Covered", "partial": "Partial", "missing": "Missing", "needs-human": "Needs human"}
    for e in matrix.entries:
        clean = lambda s: (s or "").replace("|", "/").replace("\n", " ").strip()
        req = clean(e.requirement_text)[:100]
        ev = "; ".join(f'[{r.evidence_id}] \"{clean(r.quote)[:90]}\"' for r in e.evidence_refs) or "—"
        out.append(f"| {e.requirement_id} | {req} | {label[e.status]} | {ev} | {clean(e.summary)[:200]} | {clean(e.recommendation)[:200]} |")
    out += ["", "> Draft internal aid. Every 'covered'/'partial' must be verified by a human against the cited evidence before any client-facing use."]
    return "\n".join(out)


def _sse_chunk(content: str) -> str:
    return "data: " + json.dumps({
        "id": "chatcmpl-sarvam-compliance",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }) + "\n\n"


def _chat_completion_json(content: str) -> dict:
    return {
        "id": "chatcmpl-sarvam-compliance",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_ID, "draft_model": DRAFT_MODEL}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": int(time.time()), "owned_by": "inspiritvision"}],
    }


@app.post("/v1/compliance-matrix")
async def compliance_matrix_endpoint(request: Request):
    """Paste RFP text (or explicit requirements) -> structured compliance matrix.
    Body: {"rfp_text": "...", "requirements": ["..."], "top_k": 8}
    Returns: {"matrix": <ComplianceMatrix>, "markdown": "..."}
    """
    body = await request.json()
    rfp_text = body.get("rfp_text", "") or ""
    requirements = body.get("requirements")
    top_k = int(body.get("top_k", TOP_K))
    async with httpx.AsyncClient() as client:
        matrix = await run_compliance_matrix(client, rfp_text, requirements, top_k=top_k)
    return JSONResponse({"matrix": matrix.model_dump(), "markdown": render_matrix_markdown(matrix)})


DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@app.post("/v1/generate-proposal")
async def generate_proposal_endpoint(request: Request):
    """Sprint 5 — turn an RFP + context into a downloadable DOCX proposal draft.

    Body: {"rfp_text","client_name","proposal_type":"implementation|mss",
           "iam_vendor","sections":[optional],"include_compliance_matrix":bool,"top_k":int}
    Returns: a DOCX attachment, or {"error": ...} with 400 on bad input.

    NOTE: does NOT persist to the generated_proposals table (auth/user id
    unresolved). It only streams the DOCX back for human review.
    """
    body = await request.json()
    rfp_text = (body.get("rfp_text") or "").strip()
    client_name = (body.get("client_name") or "").strip()
    proposal_type = (body.get("proposal_type") or "").strip().lower()
    iam_vendor = (body.get("iam_vendor") or "").strip() or None
    sections = body.get("sections")
    include_compliance_matrix = bool(body.get("include_compliance_matrix", False))
    try:
        top_k = int(body.get("top_k", TOP_K))
    except (TypeError, ValueError):
        return JSONResponse({"error": "top_k must be an integer"}, status_code=400)
    if top_k < 1 or top_k > 20:
        return JSONResponse({"error": "top_k must be between 1 and 20"}, status_code=400)
    if sections is not None and not isinstance(sections, list):
        return JSONResponse({"error": "sections must be a list of strings"}, status_code=400)

    if proposal_type not in {"implementation", "mss"}:
        return JSONResponse({"error": "proposal_type must be 'implementation' or 'mss'"}, status_code=400)
    if not client_name:
        return JSONResponse({"error": "client_name is required"}, status_code=400)
    if not rfp_text:
        return JSONResponse({"error": "rfp_text is required"}, status_code=400)

    try:
        async with httpx.AsyncClient() as client:
            result = await generate_proposal(
                client,
                rfp_text=rfp_text,
                client_name=client_name,
                proposal_type=proposal_type,
                iam_vendor=iam_vendor,
                embed_fn=embed_query,
                retrieve_fn=retrieve_chunks,
                build_grounded_system_fn=build_grounded_system,
                run_compliance_matrix_fn=run_compliance_matrix,
                render_matrix_markdown_fn=render_matrix_markdown,
                sections=sections,
                include_compliance_matrix=include_compliance_matrix,
                top_k=top_k,
            )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.error("generate-proposal failed: %s", e)
        return JSONResponse({"error": f"proposal generation failed: {e}"}, status_code=500)

    filename = result["filename"]
    log.info("Generated proposal %s (%d sections)", filename, len(result["sections_meta"]))
    return Response(
        content=result["docx_bytes"],
        media_type=DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    query = last_user_text(messages)

    # Compliance-matrix chat trigger (explicit prefix, no fuzzy detection):
    #   compliance matrix:
    #   <paste RFP text / requirements>
    q = query.strip()
    if q.lower().startswith(COMPLIANCE_TRIGGER):
        rfp_text = q[len(COMPLIANCE_TRIGGER):].lstrip(": \n\t")
        instruction = ("Paste your RFP text after `compliance matrix:`. For example:\n"
                       "````\ncompliance matrix:\n<RFP requirements here>\n````\n"
                       "Or POST to /v1/compliance-matrix with JSON {\"rfp_text\": \"...\"}.")

        if stream:
            # Send the keep-alive comment FIRST, then run the (multi-call) pipeline
            # inside the generator, so the client doesn't sit silently waiting.
            async def sse():
                yield ": building compliance matrix\n\n"
                if not rfp_text.strip():
                    content = instruction
                else:
                    async with httpx.AsyncClient() as cm_client:
                        matrix = await run_compliance_matrix(cm_client, rfp_text, None)
                    content = render_matrix_markdown(matrix)
                for i in range(0, len(content), 3000):
                    yield _sse_chunk(content[i:i + 3000])
                yield "data: [DONE]\n\n"
            return StreamingResponse(sse(), media_type="text/event-stream")

        # Non-streaming: run pipeline, return as a single chat completion.
        if not rfp_text.strip():
            return JSONResponse(_chat_completion_json(instruction))
        async with httpx.AsyncClient() as cm_client:
            matrix = await run_compliance_matrix(cm_client, rfp_text, None)
        return JSONResponse(_chat_completion_json(render_matrix_markdown(matrix)))

    async with httpx.AsyncClient() as client:
        # 1-2. Embed + retrieve (fail soft: draft without evidence rather than 500)
        chunks: list[dict] = []
        if query.strip():
            try:
                emb = await embed_query(client, query)
                chunks = await retrieve_chunks(client, emb, query)
                log.info("Retrieved %d chunks for query: %.80s", len(chunks), query)
                for i, c in enumerate(chunks, 1):
                    log.info("  [%d] sim=%.2f %s / %s", i, c.get("similarity", 0),
                             c.get("client_name"), c.get("heading"))
            except Exception as e:
                log.error("Retrieval failed (drafting ungrounded): %s", e)

        grounded_system = build_grounded_system(chunks)
        out_messages = [{"role": "system", "content": grounded_system}] + [
            m for m in messages if m.get("role") != "system"
        ]
        payload = {
            "model": DRAFT_MODEL,
            "messages": out_messages,
            "stream": stream,
            "temperature": body.get("temperature", 0.4),
        }
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}

        if not stream:
            resp = await client.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers,
                                     json=payload, timeout=180)
            data = resp.json()
            if "model" in data:
                data["model"] = MODEL_ID
            return JSONResponse(data, status_code=resp.status_code)

    # Streaming: separate client lifecycle inside the generator
    async def sse():
        async with httpx.AsyncClient() as sclient:
            async with sclient.stream("POST", f"{OPENROUTER_BASE}/chat/completions",
                                      headers=headers, json=payload, timeout=300) as resp:
                async for line in resp.aiter_lines():
                    if line:
                        yield line + "\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
