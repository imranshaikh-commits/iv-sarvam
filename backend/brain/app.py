"""
sarvam-brain — Phase 1 MVP
==========================
OpenAI-compatible API that Open WebUI treats as a model ("Sarvam Architect").

Flow per user message:
  1. Embed the query  (openai/text-embedding-3-small via OpenRouter — MUST match ingest)
  2. Retrieve top-k chunks from Supabase pgvector (match_proposal_chunks RPC)
  3. Build grounded system prompt with numbered evidence + safety rails
  4. Draft with GLM 5.2 (Qwen fallback) via OpenRouter (streaming or non-streaming)

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
from datetime import datetime, timezone
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

# Sprint 5 Pass 1 — structured intake + persistence. Neither module imports app.
from intake_template import get_intake_template, missing_required
import supabase_client

# Sprint 5 Pass 4 — architecture diagrams. diagram_engine does NOT import app
# (the structured LLM helper is injected), so this stays a one-way dependency.
import diagram_engine
from diagram_engine import DiagramSpec, InvalidTransition

# Sprint 5 Pass 6 — export pipeline (lite DOCX compression + PDF export). Pure
# stdlib + Pillow, no import of app (one-way dependency), so it stays importable
# keyless. Only used when an export flag is set on /v1/generate-proposal.
import export_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sarvam-brain")

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "openai/text-embedding-3-small"   # must match scripts/ingest_v2.py
# LLM models are HARDCODED (no env override) so EC2 env cannot silently pin an
# old model. Every LLM call site tries the primary, then falls back to Qwen.
PRIMARY_LLM_MODEL = "z-ai/glm-5.2"
FALLBACK_LLM_MODEL = "qwen/qwen3-235b-a22b-2507"
TOP_K = int(os.environ.get("TOP_K", "8"))
MODEL_ID = "sarvam-architect"

# Single Inspirit Vision organisation. Hard-coded until real multi-tenant auth
# propagates an org id through the request. Used to scope intake sessions and
# persisted proposals server-side (service-role key, RLS bypassed).
IV_ORG_ID = os.environ.get("IV_ORG_ID", "5ec29afe-13ff-4657-a4cd-9a078226cdc2")

# Compliance-matrix (Sprint 4 Phase 2)
COMPLIANCE_CONCURRENCY = int(os.getenv("COMPLIANCE_CONCURRENCY", "3"))
MAX_REQUIREMENTS = int(os.getenv("MAX_REQUIREMENTS", "20"))
COMPLIANCE_TRIGGER = "compliance matrix"
# Structured extraction/classification uses the same hardcoded primary model.
STRUCTURED_MODEL = PRIMARY_LLM_MODEL

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
# Interview gating (Sprint 5 Pass 5)
# When Open WebUI sends a chat with no active intake session, start the Stage 1
# discovery interview instead of a generic RAG reply.
# ---------------------------------------------------------------------------

# OWUI's OpenAI-compatible chat request has no first-class field for our intake
# id, so we accept it in the places a caller can realistically thread it through:
#   * top-level body           — direct API callers / an OWUI pipe that injects it
#   * body["metadata"]         — OWUI forwards a per-request metadata dict
#   * body["extra_body"]       — the OpenAI python client's passthrough convention
# The value is the id returned by POST /v1/intake-sessions. First non-empty wins.
def parse_intake_session_id(body: dict) -> str | None:
    for container in (body, body.get("metadata"), body.get("extra_body")):
        if isinstance(container, dict):
            val = container.get("intake_session_id")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


INTERVIEW_INTRO = (
    "Hi, I'm **Sarvam**, InspiritVision's proposal assistant. Before I draft "
    "anything, I run a short **Stage 1 discovery interview** so the proposal is "
    "grounded in your specifics rather than generic boilerplate."
)


def build_interview_start_message(proposal_type: str | None = None) -> str:
    """First question(s) of the 24-bucket discovery interview, framed as the start
    of the Stage 1 discovery interview. Deterministic (no LLM / retrieval) so the
    no-session path never touches OpenRouter or Supabase."""
    template = get_intake_template(proposal_type)
    buckets = template["buckets"]
    lines = [INTERVIEW_INTRO, "", "Let's start with the basics:"]
    if buckets:
        first = buckets[0]
        lines.append("")
        lines.append(f"**{first['title']}**")
        for q in first["questions"]:
            req = " (required)" if q.get("required") else ""
            lines.append(f"- {q['label']}{req}")
    lines.append("")
    lines.append(
        f"Reply here with these details and I'll walk you through the remaining "
        f"discovery areas ({len(buckets)} in total). Once discovery is complete "
        "I'll draft your proposal grounded in IV's past work."
    )
    return "\n".join(lines)


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
    rationale: str = Field(..., description="Why this evidence is relevant to the requirement. Max ~25 words.")


class CoverageEntry(BaseModel):
    requirement_id: str
    requirement_text: str = ""
    status: Literal["covered", "partial", "missing", "needs-human"]
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    summary: str = Field(..., description="How IV's proposal corpus addresses this requirement, grounded in evidence. Keep it to 2-3 sentences (~80 words max). Never repeat phrases.")
    recommendation: str = Field(..., description="Concrete next step: reuse a cited approach, draft a new section, or escalate to SME. Max ~30 words.")


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
8. LENGTH LIMITS (strict): summary max 80 words (2-3 sentences); recommendation max 30 words; each EvidenceRef.rationale max 25 words; at most 2 evidence_refs. Never repeat the same sentence or phrase — say something once and stop.
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


async def _structured_with_fallback(response_model, messages: list[dict], **kwargs):
    """Instructor structured call: try PRIMARY_LLM_MODEL, then FALLBACK_LLM_MODEL
    on ANY exception (instructor validation, HTTP, schema). Keep max_retries low
    (caller sets it) so instructor doesn't multi-retry a bad model before we fall
    back to the other one."""
    ic = instructor_client()
    for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
        try:
            result = await ic.chat.completions.create(
                model=model, response_model=response_model, messages=messages, **kwargs
            )
            log.info("Structured LLM model=%s", model)
            return result
        except Exception as e:
            if model == FALLBACK_LLM_MODEL:
                raise
            log.warning("Structured call failed on primary %s (%s); falling back to %s",
                        model, e, FALLBACK_LLM_MODEL)


async def extract_requirements(rfp_text: str) -> list[Requirement]:
    resp: ExtractedRequirements = await _structured_with_fallback(
        ExtractedRequirements,
        messages=[
            {"role": "system", "content": _EXTRACT_PROMPT},
            {"role": "user", "content": f"Extract up to {MAX_REQUIREMENTS} compliance requirements from the following RFP text.\n\nRFP TEXT:\n{rfp_text[:12000]}"},
        ],
        temperature=0,
        max_retries=1,
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
    entry: CoverageEntry = await _structured_with_fallback(
        CoverageEntry,
        messages=[
            {"role": "system", "content": _CLASSIFY_PROMPT},
            {"role": "user", "content": f"REQUIREMENT {req.id}:\n{req.text}\n\n=== EVIDENCE (from IV's past proposals) ===\n{build_evidence_block(chunks)}"},
        ],
        temperature=0,
        max_tokens=768,
        # LOW frequency_penalty: caps runaway repetition without penalizing the
        # repeated vendor/product/evidence terms that verbatim-quote grounding
        # relies on. A degenerate spiral shouldn't be retried (it just multiplies
        # latency/cost), so retries are dropped to 1.
        frequency_penalty=0.2,
        max_retries=1,
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


def _truncate_at_sentence(s: str, limit: int) -> str:
    """Belt-and-suspenders clamp: cut a string at the last sentence boundary
    <= limit chars (falling back to a hard cut). Guards against a degenerate
    LLM repetition spiral slipping past the prompt/max_tokens caps."""
    s = s or ""
    if len(s) <= limit:
        return s
    head = s[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= 0:
        return head[: cut + 1].rstrip()
    return head.rstrip()


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
    # Final length clamp (defence in depth against runaway generation).
    entry.summary = _truncate_at_sentence(entry.summary, 600)
    entry.recommendation = _truncate_at_sentence(entry.recommendation, 250)
    for ref in entry.evidence_refs:
        ref.rationale = _truncate_at_sentence(ref.rationale, 250)
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


def _sse_chunk(content: str, resp_id: str = "chatcmpl-sarvam-compliance") -> str:
    return "data: " + json.dumps({
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }) + "\n\n"


def _chat_completion_json(content: str, resp_id: str = "chatcmpl-sarvam-compliance") -> dict:
    return {
        "id": resp_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_ID,
            "primary_model": PRIMARY_LLM_MODEL, "fallback_model": FALLBACK_LLM_MODEL}


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


# =====================================================================
# Sprint 5 Pass 1 — structured intake (discovery interview) endpoints
# =====================================================================

@app.get("/v1/intake-template")
async def intake_template_endpoint(request: Request):
    """Return the discovery-interview schema, optionally tailored to a proposal type.

    Query: ?proposal_type=implementation|mss|migration (optional)
    Returns: {"template_version","proposal_type","buckets":[{id,title,questions}]}
    """
    proposal_type = (request.query_params.get("proposal_type") or "").strip().lower() or None
    return JSONResponse(get_intake_template(proposal_type))


@app.post("/v1/intake-sessions")
async def create_intake_session_endpoint(request: Request):
    """Start a new discovery interview.

    Body (all optional): {"proposal_type","client_name","iam_vendor","answers":{}}
    Returns: {"id", "status":"in_progress"} — REQUIRED op, 502 if Supabase fails.
    """
    body = await request.json()
    answers = body.get("answers") or {}
    if not isinstance(answers, dict):
        return JSONResponse({"error": "answers must be an object"}, status_code=400)
    proposal_type = (body.get("proposal_type") or answers.get("proposal_type") or "").strip().lower() or None
    client_name = (body.get("client_name") or answers.get("client_name") or "").strip() or None
    iam_vendor = (body.get("iam_vendor") or answers.get("iam_vendor") or "").strip() or None
    try:
        async with httpx.AsyncClient() as client:
            session_id = await supabase_client.create_intake_session(
                client,
                org_id=IV_ORG_ID,
                proposal_type=proposal_type,
                client_name=client_name,
                iam_vendor=iam_vendor,
                answers=answers,
            )
    except supabase_client.SupabaseError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse({"id": session_id, "status": "in_progress"}, status_code=201)


@app.patch("/v1/intake-sessions/{session_id}")
async def patch_intake_session_endpoint(session_id: str, request: Request):
    """Save (merge) partial answers into an in-progress session.

    Body: {"answers": {question_id: value, ...}}
    Returns: {"id","status","answers"} or 404/502 on failure.
    """
    body = await request.json()
    answers_partial = body.get("answers")
    if answers_partial is None or not isinstance(answers_partial, dict):
        return JSONResponse({"error": "answers object is required"}, status_code=400)
    async with httpx.AsyncClient() as client:
        row = await supabase_client.patch_intake_answers(client, session_id, answers_partial)
    if row is None:
        return JSONResponse({"error": "intake session not found or update failed"}, status_code=404)
    return JSONResponse({"id": row.get("id"), "status": row.get("status"), "answers": row.get("answers")})


@app.post("/v1/intake-sessions/{session_id}/complete")
async def complete_intake_session_endpoint(session_id: str):
    """Validate required answers and mark the session complete.

    Returns: {"session_id","status","complete":bool,"missing":[ids...]}.
    complete=False with a missing[] list is a normal 200 validation result, not
    an error. Transport failures return 502.
    """
    try:
        async with httpx.AsyncClient() as client:
            result = await supabase_client.complete_intake_session(client, session_id)
    except supabase_client.SupabaseError as e:
        return JSONResponse({"error": str(e)}, status_code=502)
    return JSONResponse(result)


@app.post("/v1/generate-proposal")
async def generate_proposal_endpoint(request: Request):
    """Sprint 5 — turn an RFP + context into a downloadable DOCX proposal draft.

    Body: {"rfp_text","client_name","proposal_type":"implementation|mss",
           "iam_vendor","sections":[optional],"include_compliance_matrix":bool,
           "top_k":int,"intake_session_id":optional,"proposal_depth":optional,
           "generated_proposal_id":optional,
           "lite":bool,"include_pdf":bool,"return_signed_urls":bool}
    Returns:
      * DEFAULT (no export flag): a DOCX attachment (byte-for-byte Pass 1-5).
      * lite / include_pdf / return_signed_urls set: JSON with DOCX/PDF export
        metadata (sizes, lite flag, warnings) and signed URLs where requested.
      * {"error": ...} with 400 on bad input.

    If intake_session_id is supplied, its stored answers backfill any omitted
    core field (client_name/proposal_type/iam_vendor/rfp_text) and enrich the
    retrieval text with scope/objectives. After the DOCX is built the draft is
    persisted to generated_proposals (fail-soft — persistence failure never
    blocks the download) and linked back to the intake session.
    """
    body = await request.json()
    rfp_text = (body.get("rfp_text") or "").strip()
    client_name = (body.get("client_name") or "").strip()
    proposal_type = (body.get("proposal_type") or "").strip().lower()
    iam_vendor = (body.get("iam_vendor") or "").strip() or None
    sections = body.get("sections")
    include_compliance_matrix = bool(body.get("include_compliance_matrix", False))
    intake_session_id = (body.get("intake_session_id") or "").strip() or None
    # Pass 3 — long-form depth tier. Absent/invalid falls back to the safe
    # default inside generate_proposal (get_depth_tier), so existing callers that
    # omit proposal_depth keep their current behaviour.
    proposal_depth = body.get("proposal_depth")
    proposal_depth = proposal_depth if isinstance(proposal_depth, str) else None

    # Pass 6 — opt-in export flags. When NONE are set the response is the raw DOCX
    # binary, byte-for-byte identical to Pass 1-5. When ANY is set the endpoint
    # returns JSON export metadata (+ signed URLs where requested) instead.
    lite = bool(body.get("lite", False))
    include_pdf = bool(body.get("include_pdf", False))
    return_signed_urls = bool(body.get("return_signed_urls", False))
    export_requested = lite or include_pdf or return_signed_urls

    # Load intake answers and backfill any core field the caller omitted.
    intake_answers: dict = {}
    if intake_session_id:
        async with httpx.AsyncClient() as sclient:
            session = await supabase_client.get_intake_session(sclient, intake_session_id)
        if session is None:
            return JSONResponse({"error": "intake_session_id not found"}, status_code=404)
        intake_answers = session.get("answers") or {}
        client_name = client_name or (session.get("client_name") or intake_answers.get("client_name") or "").strip()
        proposal_type = proposal_type or (session.get("proposal_type") or intake_answers.get("proposal_type") or "").strip().lower()
        iam_vendor = iam_vendor or (session.get("iam_vendor") or intake_answers.get("iam_vendor") or "").strip() or None
        rfp_text = rfp_text or (intake_answers.get("rfp_text") or "").strip()
        # Enrich retrieval text with scope/objectives when present (does not
        # change section depth or length — just gives retrieval more to match).
        extra = "\n\n".join(
            str(intake_answers[k]).strip()
            for k in ("business_objectives", "in_scope", "out_of_scope", "current_state")
            if str(intake_answers.get(k) or "").strip()
        )
        if extra:
            rfp_text = (rfp_text + "\n\n" + extra).strip() if rfp_text else extra

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

    # Pass 4 — embed approved architecture diagrams. Opt-in via an existing
    # generated_proposal_id; when omitted the default path is byte-for-byte
    # unchanged (Pass 1-3). Approved diagrams are rendered fail-soft (a missing
    # `dot` binary just skips the embed, never breaks generation).
    embed_diagrams: list[dict] = []
    embed_proposal_id = (body.get("generated_proposal_id") or "").strip() or None
    if embed_proposal_id:
        async with httpx.AsyncClient() as dclient:
            diagram_rows = await supabase_client.list_diagrams_for_proposal(dclient, embed_proposal_id)
        for drow in diagram_rows:
            if (drow.get("status") or "") != "approved":
                continue
            try:
                spec = DiagramSpec.model_validate(drow.get("spec_json") or {})
                image = diagram_engine.render_spec(spec, fmt="png")
            except Exception as e:  # noqa: BLE001 — fail soft per diagram
                log.error("approved diagram render failed (skipping embed): %s", e)
                image = None
            if image:
                embed_diagrams.append(
                    {"title": drow.get("title") or "Architecture Diagram",
                     "status": "approved", "image_bytes": image,
                     "diagram_type": drow.get("diagram_type")}
                )

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
                proposal_depth=proposal_depth,
                diagrams=embed_diagrams or None,
            )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        log.error("generate-proposal failed: %s", e)
        return JSONResponse({"error": f"proposal generation failed: {e}"}, status_code=500)

    filename = result["filename"]
    log.info("Generated proposal %s (%d sections)", filename, len(result["sections_meta"]))

    # Fail-soft persistence: never let a Supabase hiccup block the download.
    draft_markdown = result.get("draft_markdown", "")
    proposal_id = None
    try:
        async with httpx.AsyncClient() as pclient:
            proposal_id = await supabase_client.insert_generated_proposal(
                pclient,
                org_id=IV_ORG_ID,
                client_name=client_name,
                proposal_type=proposal_type,
                iam_vendor=iam_vendor,
                discovery_answers=intake_answers,
                draft_markdown=draft_markdown,
                retrieval_trace=result.get("sections_meta", []),
                intake_session_id=intake_session_id,
            )
            if proposal_id and intake_session_id:
                await supabase_client.link_intake_to_proposal(pclient, intake_session_id, proposal_id)
    except Exception as e:  # noqa: BLE001 — persistence must never break the download
        log.error("generate-proposal persistence failed (returning DOCX anyway): %s", e)

    # DEFAULT path (no export flag): return the DOCX binary byte-for-byte, exactly
    # as Pass 1-5 did. Nothing below runs unless the caller opts in.
    if not export_requested:
        return Response(
            content=result["docx_bytes"],
            media_type=DOCX_MEDIA_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return await _build_export_response(
        docx_bytes=result["docx_bytes"],
        filename=filename,
        proposal_id=proposal_id,
        lite=lite,
        include_pdf=include_pdf,
        return_signed_urls=return_signed_urls,
    )


async def _build_export_response(
    *,
    docx_bytes: bytes,
    filename: str,
    proposal_id: str | None,
    lite: bool,
    include_pdf: bool,
    return_signed_urls: bool,
) -> JSONResponse:
    """Apply the opt-in export steps and return JSON export metadata.

    Every step is fail-soft: lite compression that can't hit the target returns a
    warning, PDF export with no soffice binary returns an error string, and a
    missing storage bucket yields a manual-setup note — none of these crash the
    request. The DOCX/PDF bytes are delivered via signed URLs when requested.
    """
    docx_meta: dict = {"lite": lite}
    if lite:
        docx_bytes, lite_meta = export_engine.compress_docx_lite(docx_bytes)
        docx_meta.update(lite_meta)
    docx_meta["size"] = len(docx_bytes)

    pdf_bytes: bytes | None = None
    pdf_meta: dict | None = None
    if include_pdf:
        pdf_bytes, pdf_error = export_engine.export_pdf(docx_bytes)
        if pdf_bytes is not None:
            pdf_meta = {"size": len(pdf_bytes)}
        else:
            pdf_meta = {"error": pdf_error}

    pdf_filename = re.sub(r"\.docx$", ".pdf", filename)
    response: dict = {
        "filename": filename,
        "docx": docx_meta,
        "pdf": pdf_meta,
        "signed_urls": {},
        "generated_proposal_id": proposal_id,
    }

    if return_signed_urls:
        prefix = (proposal_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"))
        signed: dict = {}
        notes: list[str] = []
        async with httpx.AsyncClient() as sclient:
            docx_path = f"{prefix}/{filename}"
            if await supabase_client.upload_generated_draft(
                sclient, docx_path, docx_bytes, DOCX_MEDIA_TYPE
            ):
                url = await supabase_client.create_signed_url(sclient, docx_path)
                if url:
                    signed["docx"] = url
                else:
                    notes.append("docx uploaded but signed URL creation failed")
            else:
                notes.append(
                    f"could not upload DOCX to storage bucket "
                    f"'{supabase_client.GENERATED_DRAFTS_BUCKET}' — create the bucket manually "
                    "in Supabase Storage to enable signed-URL delivery"
                )
            if pdf_bytes is not None:
                pdf_path = f"{prefix}/{pdf_filename}"
                if await supabase_client.upload_generated_draft(
                    sclient, pdf_path, pdf_bytes, "application/pdf"
                ):
                    url = await supabase_client.create_signed_url(sclient, pdf_path)
                    if url:
                        signed["pdf"] = url
                    else:
                        notes.append("pdf uploaded but signed URL creation failed")
                else:
                    notes.append("could not upload PDF to storage bucket")
        response["signed_urls"] = signed
        if notes:
            response["storage_notes"] = notes

    return JSONResponse(response)


# =====================================================================
# Sprint 5 Pass 4 — architecture diagram approval flow
# =====================================================================
# The LLM emits a structured DiagramSpec (never raw DOT); diagram_engine builds
# DOT deterministically and renders via the local `dot` binary (fail-soft). Only
# 'approved' diagrams are embedded into the proposal DOCX.

def _diagram_public(row: dict) -> dict:
    """Trim a DB row to the fields the API exposes."""
    return {
        "id": row.get("id"),
        "generated_proposal_id": row.get("generated_proposal_id"),
        "intake_session_id": row.get("intake_session_id"),
        "diagram_type": row.get("diagram_type"),
        "title": row.get("title"),
        "status": row.get("status"),
        "approved": row.get("approved"),
        "iteration": row.get("iteration"),
        "renderer": row.get("renderer"),
        "rendered_svg_path": row.get("rendered_svg_path"),
        "rejection_comments": row.get("rejection_comments"),
        "spec_json": row.get("spec_json"),
        "created_at": row.get("created_at"),
    }


@app.post("/v1/proposals/{proposal_id}/diagrams")
async def create_diagram_endpoint(proposal_id: str, request: Request):
    """Generate a structured architecture-diagram spec (LLM) and persist as draft.

    Body: {"title","diagram_type","context_text","intake_session_id"} (all
    optional except a sensible default title). Returns the created diagram row.
    The spec is generated via the shared _structured_with_fallback helper and
    sanitized/capped before storage. No raw DOT is ever accepted.
    """
    body = await request.json()
    title = (body.get("title") or "Solution Architecture").strip() or "Solution Architecture"
    diagram_type = (body.get("diagram_type") or "architecture").strip().lower()
    context_text = (body.get("context_text") or "").strip()
    intake_session_id = (body.get("intake_session_id") or "").strip() or None
    iam_vendor = (body.get("iam_vendor") or "").strip() or None
    client_name = (body.get("client_name") or "the client").strip() or "the client"

    try:
        spec = await diagram_engine.generate_diagram_spec(
            _structured_with_fallback,
            title=title,
            diagram_type=diagram_type,
            context_text=context_text,
            client_name=client_name,
            iam_vendor=iam_vendor,
        )
    except Exception as e:  # noqa: BLE001
        log.error("diagram spec generation failed: %s", e)
        return JSONResponse({"error": f"diagram spec generation failed: {e}"}, status_code=502)

    async with httpx.AsyncClient() as client:
        row = await supabase_client.insert_diagram(
            client,
            org_id=IV_ORG_ID,
            generated_proposal_id=proposal_id,
            diagram_type=spec.diagram_type,
            title=spec.title,
            spec_json=spec.model_dump(),
            status="draft",
            intake_session_id=intake_session_id,
        )
    if row is None:
        return JSONResponse({"error": "could not persist diagram"}, status_code=502)
    return JSONResponse(_diagram_public(row), status_code=201)


@app.get("/v1/proposals/{proposal_id}/diagrams")
async def list_diagrams_endpoint(proposal_id: str):
    """List all diagrams attached to a proposal (newest first)."""
    async with httpx.AsyncClient() as client:
        rows = await supabase_client.list_diagrams_for_proposal(client, proposal_id)
    return JSONResponse({"diagrams": [_diagram_public(r) for r in rows]})


@app.get("/v1/diagrams/{diagram_id}")
async def get_diagram_endpoint(diagram_id: str):
    """Fetch a single diagram by id."""
    async with httpx.AsyncClient() as client:
        row = await supabase_client.get_diagram(client, diagram_id)
    if row is None:
        return JSONResponse({"error": "diagram not found"}, status_code=404)
    return JSONResponse(_diagram_public(row))


@app.patch("/v1/diagrams/{diagram_id}")
async def patch_diagram_endpoint(diagram_id: str, request: Request):
    """Advance a diagram through the approval state machine.

    Body: {"status": "needs_review|approved|rejected|draft",
           "rejection_comment": "..." (required when rejecting)}

    Transitions are validated (draft->needs_review->approved/rejected;
    rejected->draft bumps iteration). On approval the spec is rendered with the
    local `dot` binary and uploaded to the diagram-renders bucket (both
    fail-soft — a missing renderer/bucket never blocks the approval)."""
    body = await request.json()
    target = (body.get("status") or "").strip()
    rejection_comment = body.get("rejection_comment")

    async with httpx.AsyncClient() as client:
        row = await supabase_client.get_diagram(client, diagram_id)
        if row is None:
            return JSONResponse({"error": "diagram not found"}, status_code=404)

        try:
            patch = diagram_engine.apply_transition(
                row, target, rejection_comment=rejection_comment
            )
        except InvalidTransition as e:
            return JSONResponse({"error": str(e)}, status_code=409)

        # On approval, stamp approved_at (approved_by stays NULL until real auth
        # propagates a user id) and render + upload the image (fail-soft).
        if target == "approved":
            patch["approved_at"] = datetime.now(timezone.utc).isoformat()
            try:
                spec = DiagramSpec.model_validate(row.get("spec_json") or {})
                image = diagram_engine.render_spec(spec, fmt="png")
                if image:
                    path = await supabase_client.upload_diagram_render(
                        client, f"{diagram_id}.png", image, content_type="image/png"
                    )
                    if path:
                        patch["rendered_svg_path"] = path
            except Exception as e:  # noqa: BLE001 — render/upload must not block approval
                log.error("diagram render/upload failed on approve (fail-soft): %s", e)

        updated = await supabase_client.update_diagram(client, diagram_id, patch)
    if updated is None:
        return JSONResponse({"error": "could not update diagram"}, status_code=502)
    return JSONResponse(_diagram_public(updated))


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

    # Interview gating: with no active intake session, start the Stage 1 discovery
    # interview instead of a generic RAG reply. Deterministic — no retrieval or
    # proposal drafting on this path. The explicit compliance-matrix command above
    # is handled before this, so it stays usable without a session.
    if not parse_intake_session_id(body):
        content = build_interview_start_message()
        if stream:
            async def interview_sse():
                for i in range(0, len(content), 3000):
                    yield _sse_chunk(content[i:i + 3000], resp_id="chatcmpl-sarvam-interview")
                yield "data: [DONE]\n\n"
            return StreamingResponse(interview_sse(), media_type="text/event-stream")
        return JSONResponse(_chat_completion_json(content, resp_id="chatcmpl-sarvam-interview"))

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
            "model": PRIMARY_LLM_MODEL,
            "messages": out_messages,
            "stream": stream,
            "temperature": body.get("temperature", 0.4),
        }
        headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"}

        if not stream:
            # Try primary, fall back to Qwen on 4xx/5xx or network/timeout error.
            data, status_code = None, 502
            for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
                try:
                    resp = await client.post(f"{OPENROUTER_BASE}/chat/completions", headers=headers,
                                             json={**payload, "model": model}, timeout=180)
                    resp.raise_for_status()
                    data, status_code = resp.json(), resp.status_code
                    log.info("OpenRouter chat model=%s", model)
                    break
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    log.warning("OpenRouter chat failed on %s (%s)%s", model, e,
                                "" if model == FALLBACK_LLM_MODEL else f"; falling back to {FALLBACK_LLM_MODEL}")
                    if model == FALLBACK_LLM_MODEL:
                        if isinstance(e, httpx.HTTPStatusError):
                            data, status_code = e.response.json(), e.response.status_code
                        else:
                            data, status_code = {"error": str(e)}, 502
            if isinstance(data, dict) and "model" in data:
                data["model"] = MODEL_ID
            return JSONResponse(data, status_code=status_code)

    # Streaming: separate client lifecycle inside the generator
    async def sse():
        async with httpx.AsyncClient() as sclient:
            # Try primary, fall back to Qwen if it fails before streaming starts.
            for model in (PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL):
                try:
                    async with sclient.stream("POST", f"{OPENROUTER_BASE}/chat/completions",
                                              headers=headers, json={**payload, "model": model},
                                              timeout=300) as resp:
                        resp.raise_for_status()
                        log.info("OpenRouter chat (stream) model=%s", model)
                        async for line in resp.aiter_lines():
                            if line:
                                yield line + "\n\n"
                    return
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    log.warning("OpenRouter stream failed on %s (%s)%s", model, e,
                                "" if model == FALLBACK_LLM_MODEL else f"; falling back to {FALLBACK_LLM_MODEL}")
                    if model == FALLBACK_LLM_MODEL:
                        yield "data: " + json.dumps({"error": str(e)}) + "\n\n"
                        return

    return StreamingResponse(sse(), media_type="text/event-stream")
