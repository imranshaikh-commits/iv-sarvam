"""
shilpi-brain — Phase 1 MVP
==========================
OpenAI-compatible API that Open WebUI treats as a model ("Shilpi Architect").

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
import base64
import functools
import io
import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Literal, Optional

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
from intake_template import get_intake_template, iter_questions, missing_required
import supabase_client
import chat_state

# Sprint 5 Pass 4 — architecture diagrams. diagram_engine does NOT import app
# (the structured LLM helper is injected), so this stays a one-way dependency.
import diagram_engine
from diagram_engine import DiagramSpec, InvalidTransition

# Sprint 5 Pass 6 — export pipeline (lite DOCX compression + PDF export). Pure
# stdlib + Pillow, no import of app (one-way dependency), so it stays importable
# keyless. Only used when an export flag is set on /v1/generate-proposal.
import export_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("shilpi-brain")

OPENROUTER_BASE = os.environ.get("OPENROUTER_BASE", "https://openrouter.ai/api/v1")
OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_KEY"]

EMBED_MODEL = "openai/text-embedding-3-small"   # must match scripts/ingest_v2.py
# The drafting/chat chain. Overridable so the primary model can be swapped for a
# measured comparison run without a code change, but the defaults stay here and
# any override is LOGGED AT STARTUP — the original rule was "no env override so
# EC2 cannot SILENTLY pin an old model", and silence was the part that mattered.
# Keep in sync with document_engine.py, which cannot import this module.
PRIMARY_LLM_MODEL = os.environ.get("SHILPI_PRIMARY_MODEL", "").strip() or "z-ai/glm-5.2"
FALLBACK_LLM_MODEL = os.environ.get("SHILPI_FALLBACK_MODEL", "").strip() or "qwen/qwen3-235b-a22b-2507"
TOP_K = int(os.environ.get("TOP_K", "8"))
# Compliance classification is per-requirement and needs the best few pieces of
# evidence, not the whole fan-out. See classify_coverage for the measurement.
COMPLIANCE_EVIDENCE_CHUNKS = int(os.environ.get("SHILPI_COMPLIANCE_EVIDENCE_CHUNKS", "12"))
COMPLIANCE_MAX_TOKENS = int(os.environ.get("SHILPI_COMPLIANCE_MAX_TOKENS", "2000"))
MODEL_ID = "shilpi-architect"

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

app = FastAPI(title="shilpi-brain")

# An override must never be silent — that was the whole point of hardcoding
# these. /health reports the same two values for post-deploy verification.
if os.environ.get("SHILPI_PRIMARY_MODEL", "").strip():
    log.warning("MODEL OVERRIDE ACTIVE: primary=%s fallback=%s (from environment)",
                PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL)
else:
    log.info("model chain: primary=%s fallback=%s (defaults)",
             PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL)

SYSTEM_PROMPT = """You are Shilpi, InspiritVision's internal proposal assistant (an IAM consulting firm).
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


# ---------------------------------------------------------------------------
# Reusable image assets
#
# 341 approved assets (235 corporate, 106 product) recovered from the proposal
# bank. `architecture` assets are never approved and never placed: they depict
# a specific client's zones, node counts and integrations, so one in another
# client's proposal is a leak. That exclusion lives in asset_selection.py, in
# code rather than in a reviewer's judgement.
# ---------------------------------------------------------------------------
ASSET_BUCKET = os.environ.get("SHILPI_ASSET_BUCKET", "visual-assets")
# Off by default. Turning this on changes what lands in a client document, so
# it is an explicit choice rather than something that arrives with a deploy.
ASSETS_ENABLED = os.environ.get("SHILPI_ASSETS_ENABLED", "0") not in ("0", "", "false")


async def fetch_approved_assets(client: httpx.AsyncClient) -> list[dict]:
    """The approved, placeable asset library. Metadata only; bytes come later.

    Fails SOFT: a proposal without images is a worse proposal, not a failed one.
    """
    if not ASSETS_ENABLED:
        return []
    try:
        resp = await client.get(
            f"{SUPABASE_URL}/rest/v1/visual_assets",
            headers=supabase_client._headers(prefer_representation=False),
            params={"select": "id,storage_path,asset_kind,vision_description,"
                              "ocr_text,section_heading,width,height,approved",
                    "approved": "is.true",
                    "asset_kind": "in.(corporate,product)"},
            timeout=30.0)
        resp.raise_for_status()
        assets = resp.json() or []
    except Exception as e:  # noqa: BLE001
        log.warning("asset library unavailable, drafting without images: %s", e)
        return []

    # How many proposals each image appears in. Recurrence is the strongest
    # available evidence that IV reuses an image deliberately, and
    # asset_selection ranks on it.
    counts: dict[str, int] = {}
    for a in assets:
        counts[a["storage_path"]] = counts.get(a["storage_path"], 0) + 1
    for a in assets:
        a["occurrences"] = counts.get(a["storage_path"], 1)
    log.info("asset library: %d approved placeable assets", len(assets))
    return assets


async def download_asset(client: httpx.AsyncClient, storage_path: str):
    """Image bytes as a stream ready for python-docx, or None."""
    try:
        resp = await client.get(
            f"{SUPABASE_URL}/storage/v1/object/{ASSET_BUCKET}/{storage_path}",
            headers=supabase_client._headers(prefer_representation=False), timeout=60.0)
        if resp.status_code != 200:
            log.warning("asset download failed HTTP %s for %s",
                        resp.status_code, storage_path)
            return None
        return io.BytesIO(resp.content)
    except Exception as e:  # noqa: BLE001
        log.warning("asset download error for %s: %s", storage_path, e)
        return None


async def retrieve_chunks(client: httpx.AsyncClient, embedding: list[float], query: str,
                          k: int = TOP_K, proposal_type: Optional[str] = None,
                          section_topic: Optional[str] = None) -> list[dict]:
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
        # filter_proposal_type reserves a share of the slots for chunks from the
        # SAME kind of engagement. Measured on the retrieval scorecard: a
        # credential-migration query returned 1 of 8 chunks from a migration
        # proposal without it and 5 of 8 with it, against a 35.5% base rate.
        # Drafting a migration section from greenfield implementation proposals
        # is the failure this prevents: those documents describe building from
        # nothing, for a client who already runs a working identity platform.
        json={"query_embedding": json.dumps(embedding, separators=(",", ":")),
              "match_count": k * 4,
              "filter_proposal_type": proposal_type,
              # Reserves half the slots for chunks carrying this topic, with a
              # topic-scoped second query for topics too small to appear in a
              # general candidate pool. Measured: why_vendor went from 1 of 8
              # on-topic results to 4 of 8. Company Profile, Why-Vendor and
              # Similar Experience are each 1-3% of the corpus, which is why
              # they were thin in every generated proposal.
              "filter_section_topic": section_topic},
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


def last_user_images(messages: list[dict]) -> list[str]:
    """Image URLs / data URIs attached to the most recent user message.

    ``last_user_text`` deliberately keeps only the ``text`` parts of Open WebUI's
    multimodal content array. That silently discarded every attached image — a
    client logo dropped into the chat reached the brain and was thrown away one
    line before anything could use it. This is the other half of that payload.
    """
    for m in reversed(messages or []):
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if not isinstance(content, list):
            return []
        out: list[str] = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            ref = part.get("image_url")
            # OWUI sends {"url": ...}; some clients send the string directly.
            url = ref.get("url") if isinstance(ref, dict) else ref
            if isinstance(url, str) and url.strip():
                out.append(url.strip())
        return out
    return []


# The one discovery area whose answer can be an attachment rather than text.
LOGO_BUCKET_ID = "branding"

_DATA_URI_RE = re.compile(r"^data:(image/[a-z0-9.+-]+);base64,(.+)$",
                          re.IGNORECASE | re.DOTALL)
_LOGO_SUFFIXES = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/gif": ".gif", "image/bmp": ".bmp", "image/webp": ".webp",
}
_MAX_LOGO_BYTES = 8 * 1024 * 1024


def save_attached_logo(images: list[str]) -> Optional[str]:
    """Turn the first attached image into something ``_resolve_client_logo`` takes.

    A base64 data URI is decoded to a temp file and the PATH is returned — the
    local-path branch of ``_resolve_client_logo`` then picks it up unchanged, so
    that function needs no modification. An http(s) attachment is passed straight
    through for it to fetch. Returns None when there is nothing usable; a bad
    logo must never fail a proposal.
    """
    for raw in images or []:
        if raw.lower().startswith(("http://", "https://")):
            return raw
        match = _DATA_URI_RE.match(raw)
        if not match:
            continue
        mime = match.group(1).lower()
        if mime not in _LOGO_SUFFIXES:
            log.warning("attached image ignored, unsupported type: %s", mime)
            continue
        try:
            data = base64.b64decode(match.group(2), validate=False)
        except Exception as e:  # noqa: BLE001 — a bad attachment must not wedge the chat
            log.warning("could not decode attached image: %s", e)
            continue
        if not data or len(data) > _MAX_LOGO_BYTES:
            log.warning("attached image rejected (empty or >8MB): %d bytes", len(data))
            continue
        fd, path = tempfile.mkstemp(prefix="client_logo_", suffix=_LOGO_SUFFIXES[mime])
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        log.info("client logo attached in chat (%d bytes) -> %s", len(data), path)
        return path
    return None


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
    "Hi, I'm **Shilpi**, InspiritVision's proposal assistant. Before I draft "
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
# Chat state machine helpers (OWUI unblock)
# ---------------------------------------------------------------------------

def _emit_chat(content: str, stream: bool, resp_id: str = "chatcmpl-shilpi-chat"):
    """Return ``content`` as either an SSE stream or a single chat completion.

    Both shapes are needed because OWUI streams by default while direct API
    callers and the tests use non-streaming.
    """
    if stream:
        async def gen():
            for i in range(0, len(content), 3000):
                yield _sse_chunk(content[i:i + 3000], resp_id=resp_id)
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse(_chat_completion_json(content, resp_id=resp_id))


async def _emit_chat_lazy(make_content, stream: bool, resp_id: str = "chatcmpl-shilpi-chat",
                          heartbeat: float = 10.0):
    """Like ``_emit_chat`` but for content that takes a while to produce.

    On the streaming path a keep-alive comment goes out FIRST and then every
    ``heartbeat`` seconds until the work finishes — so a multi-minute proposal
    generation doesn't look like a dead connection. Same idea as the
    compliance-matrix path, extended to long-running work.
    """
    if stream:
        async def gen():
            yield ": working\n\n"
            task = asyncio.create_task(make_content())
            while True:
                done, _ = await asyncio.wait({task}, timeout=heartbeat)
                if done:
                    break
                yield ": still working\n\n"
            try:
                content = task.result()
            except Exception as e:  # noqa: BLE001 — report, never hang the stream
                log.error("lazy chat content failed: %s", e)
                content = ("Something went wrong producing that response. The details are "
                           "in the brain logs; try again in a moment.")
            for i in range(0, len(content), 3000):
                yield _sse_chunk(content[i:i + 3000], resp_id=resp_id)
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse(_chat_completion_json(await make_content(), resp_id=resp_id))


class _AnswerPair(BaseModel):
    question_id: str
    value: str


class _ExtractedAnswers(BaseModel):
    answers: list[_AnswerPair] = Field(default_factory=list)


_BUCKET_EXTRACT_PROMPT = (
    "You map a consultant's free-text reply onto a fixed set of discovery questions.\n"
    "Return ONLY answers you can support from the reply. Never invent a value, never "
    "guess, and never restate the question as the answer. Omit any question the reply "
    "does not address. Use only the exact question_id values provided. For select/"
    "multiselect questions prefer one of the listed options; for booleans use true/false.\n"
    "If the reply is a bare list with no labels (e.g. 'Acme, Banking, India'), map the "
    "values positionally to the questions in the order they are listed.\n"
    "Each 'value' must be the plain answer text only — never JSON, never a wrapper "
    "object, never a leading colon or the question label repeated back."
)

# Sometimes the model returns the whole pair object, or JSON, as the *value*
# ("{\"question_id\": \"diagram_count\", \"value\": \"4\"}"), or leaves a stray
# leading colon (": 2026"). Both showed up in live testing and leaked into the
# user-visible recap line, so values are normalised before they are stored.
def _clean_answer_value(raw: str) -> str:
    val = (raw or "").strip()
    if val.startswith("{") and "value" in val:
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict) and "value" in parsed:
                val = str(parsed["value"]).strip()
        except (ValueError, TypeError):
            m = re.search(r'"value"\s*:\s*"([^"]*)"', val)
            if m:
                val = m.group(1).strip()
    return val.lstrip(":").strip()


# Extraction is a CONVENIENCE — it maps prose onto question ids. The interview
# must advance whether or not it succeeds, so it gets a hard wall-clock budget.
# Without this the OpenAI SDK's 600s default applies and one slow OpenRouter call
# hangs the whole conversation for ten minutes.
_EXTRACT_TIMEOUT_S = 25.0


async def extract_bucket_answers(bucket: dict, reply_text: str) -> dict[str, str]:
    """Map one free-text reply onto this bucket's question ids.

    Fail-soft AND fail-fast by design: on timeout or any extraction failure the
    caller still advances the interview and preserves the raw reply, because a
    slow or broken LLM call must never wedge the conversation.
    """
    questions = bucket.get("questions") or []
    if not questions or not reply_text.strip():
        return {}
    valid_ids = {q["id"] for q in questions}

    schema_lines = []
    for q in questions:
        bits = [f"- question_id: {q['id']} | label: {q['label']} | type: {q.get('type')}"]
        if q.get("options"):
            bits.append(f" | options: {', '.join(str(o) for o in q['options'])}")
        schema_lines.append("".join(bits))

    try:
        resp: _ExtractedAnswers = await asyncio.wait_for(
            _structured_with_fallback(
                _ExtractedAnswers,
                messages=[
                    {"role": "system", "content": _BUCKET_EXTRACT_PROMPT},
                    {"role": "user", "content": (
                        f"QUESTIONS:\n" + "\n".join(schema_lines)
                        + f"\n\nCONSULTANT'S REPLY:\n{reply_text[:4000]}"
                    )},
                ],
                temperature=0,
                max_retries=1,
            ),
            timeout=_EXTRACT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        log.warning("bucket answer extraction timed out after %ss for %s — advancing anyway",
                    _EXTRACT_TIMEOUT_S, bucket.get("id"))
        return {}
    except Exception as e:  # noqa: BLE001 — fail soft, never wedge the chat
        log.warning("bucket answer extraction failed for %s: %s", bucket.get("id"), e)
        return {}

    out: dict[str, str] = {}
    for pair in resp.answers:
        qid = (pair.question_id or "").strip()
        val = _clean_answer_value(pair.value)
        if qid in valid_ids and val:
            out[qid] = val
    return out


def _norm_label(text: str) -> str:
    """Loose key for matching a user's label against a question label/id.

    "and", "&" and "/" all collapse to nothing, because people write the same
    field five ways: the schema says "HA / DR requirements" and the answer says
    "HA and DR requirements". Squashing punctuation alone left those as
    'hadrrequirements' vs 'haanddrrequirements', which matched neither exactly
    nor by containment — so five supplied answers were silently discarded in the
    Amlak run. The word must be removed BEFORE punctuation is stripped, or
    "brand" becomes "br".
    """
    s = (text or "").lower().replace("&", " ").replace("/", " ")
    s = re.sub(r"\band\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _label_keys(label: str) -> list[str]:
    """Every normalised form a question label might be typed as.

    Includes the form with any parenthetical dropped, so the schema's
    "Segregation of duties (SoD) required?" also matches a reply that just says
    "segregation of duties required".
    """
    keys = [_norm_label(label)]
    bare = re.sub(r"\([^)]*\)", " ", label or "")
    if _norm_label(bare) != keys[0]:
        keys.append(_norm_label(bare))
    return [k for k in keys if k]


def parse_bucket_answers(bucket: dict, reply_text: str) -> dict[str, str]:
    """Map a reply onto question ids WITHOUT an LLM call.

    Handles the two shapes people actually type:

      * labelled  — "primary IAM vendor: PingIdentity. proposal type: implementation"
      * bare list — "AWS, Tech, India" (positional, in question order)

    This exists because the LLM extraction was both the slowest step in the
    interview and the least reliable — the first two areas of a live run silently
    failed to map while later ones worked. A parser is instant and deterministic;
    the model is now only a fallback for genuine prose.

    Returns {} when it cannot parse confidently, so the caller falls back.
    """
    questions = bucket.get("questions") or []
    reply = (reply_text or "").strip()
    if not questions or not reply:
        return {}

    # Index the bucket's questions by several loose keys.
    lookup: dict[str, str] = {}
    for q in questions:
        qid = q["id"]
        lookup[_norm_label(qid)] = qid
        for key in _label_keys(q.get("label", "")):
            lookup.setdefault(key, qid)
        # "HA / DR requirements" -> also try without a trailing "requirements"/"details"
        base = re.sub(r"(requirements|details|count|name)$", "",
                      _norm_label(q.get("label", "")))
        if base and base not in lookup:
            lookup[base] = qid

    def _resolve(raw_label: str) -> Optional[str]:
        key = _norm_label(raw_label)
        if not key:
            return None
        qid = lookup.get(key)
        if qid is not None:
            return qid
        # Loose containment both ways, longest match wins.
        cands = [(len(k), v) for k, v in lookup.items()
                 if k and (k in key or key in k) and len(k) > 3]
        return max(cands)[1] if cands else None

    out: dict[str, str] = {}

    # --- labelled form -----------------------------------------------------
    # Find where each ANSWER starts, then take everything up to the next answer
    # as its value.
    #
    # This used to split the reply on `. `, newline or `;` and then partition
    # each fragment on its first colon. A semicolon inside a VALUE was therefore
    # read as an answer boundary, and everything after it was dropped: in the
    # Amlak run that silently removed 9 of 10 out-of-scope items, five of six
    # pain points (including off-network password reset), the DR/UAT/dev sizing,
    # and Tranches 1-3 of the delivery plan. Multi-item answers separated by
    # semicolons are the normal way a consultant writes, so the parser has to
    # keep them.
    boundaries: list[tuple[int, int, str]] = []  # (label_start, value_start, qid)
    for m in re.finditer(r":", reply):
        colon = m.start()
        # The label is whatever sits between the previous hard break and here.
        # `;` counts as a break so "a; b; label: value" still finds `label`,
        # but a semicolon NOT followed by a label simply never becomes a
        # boundary and stays inside the preceding value.
        prev = max(reply.rfind(". ", 0, colon), reply.rfind("\n", 0, colon),
                   reply.rfind(";", 0, colon))
        label_start = prev + 1 if prev >= 0 else 0
        raw_label = reply[label_start:colon]
        # A label is short. This stops a colon deep inside prose from being
        # matched against a question by loose containment.
        if len(raw_label.split()) > 8:
            continue
        qid = _resolve(raw_label)
        if qid is not None:
            boundaries.append((label_start, colon + 1, qid))

    for i, (_, value_start, qid) in enumerate(boundaries):
        value_end = boundaries[i + 1][0] if i + 1 < len(boundaries) else len(reply)
        value = reply[value_start:value_end].strip().strip(".;").strip()
        if value and qid not in out:
            out[qid] = value[:2000]

    if out:
        return out

    # --- bare positional list ---------------------------------------------
    # Only when there are no colons at all AND every comma-separated item looks
    # like a short value rather than a clause. Character length alone is not
    # enough: "we have a lot of legacy applications and the helpdesk is
    # overwhelmed" is 67 chars but is obviously prose, so word count decides.
    if ":" in reply:
        return {}
    values = [v.strip() for v in reply.split(",") if v.strip()]
    looks_like_values = (
        2 <= len(values) <= len(questions)
        and all(len(v.split()) <= 4 and len(v) <= 40 for v in values)
    )
    if looks_like_values:
        for q, value in zip(questions, values):
            out[q["id"]] = value
    return out


async def resolve_bucket_answers(bucket: dict, reply_text: str) -> dict[str, str]:
    """Parser first (instant), LLM only if the parser can't do it."""
    parsed = parse_bucket_answers(bucket, reply_text)
    if parsed:
        return parsed
    return await extract_bucket_answers(bucket, reply_text)


def gap_fill_bucket(missing_ids: list[str], proposal_type: str | None = None) -> dict:
    """Build a pseudo-bucket containing only the still-missing required questions.

    Used by the recovery loop when discovery finishes with required gaps, so the
    user's follow-up answer is mapped against exactly those fields.
    """
    wanted = set(missing_ids or [])
    questions = [q for q in iter_questions(proposal_type) if q["id"] in wanted]
    return {"id": "gap_fill", "title": "Missing details", "questions": questions}


# ---------------------------------------------------------------------------
# Architecture review (chat) — the V1 human-in-loop gate
# ---------------------------------------------------------------------------
# Everything here reuses the Pass 4 engine: generate_diagram_spec -> insert ->
# render -> approve. The only new part is driving it from the chat state machine
# so a diagram actually appears without hand-calling the REST endpoints.


# Spec generation is a structured LLM call per diagram, and GLM is known to be
# slow/flaky at structured output. Without these budgets the OpenAI SDK's 600s
# default applies PER DIAGRAM — four diagrams could hang the chat for the better
# part of an hour with no partial result. Same lesson as answer extraction:
# bound the wait, keep whatever succeeded, tell the user what is missing.
# This wraps generate_diagram_spec, which may now make TWO model calls (the
# original plus one corrective retry when the spec comes back edgeless). Sized
# for both: a complex deployment spec measured ~50s per attempt, so 75s killed
# the retry path exactly when it was needed. Diagrams are produced one at a time
# with SSE heartbeats, so a longer ceiling costs nothing but patience.
_DIAGRAM_SPEC_TIMEOUT_S = float(os.environ.get("SHILPI_DIAGRAM_SPEC_TIMEOUT_S", "180"))

# Diagram specs are the hardest structured output we ask for (nested node/edge
# arrays with validators) and GLM has proven marginal at them. This points that
# ONE call at a stronger model without touching any other path. MUST be passed
# through to every generate_diagram_spec call site — defining it and forgetting
# to pass it made the whole override silently decorative.
DIAGRAM_LLM_MODELS = [
    m.strip() for m in os.environ.get("SHILPI_DIAGRAM_MODELS", "").split(",") if m.strip()
]
_ARCH_ROUND_BUDGET_S = float(os.environ.get("SHILPI_ARCH_ROUND_BUDGET_S", "240"))
# Diagrams are independent, so they run concurrently. Bounded to stay polite to
# OpenRouter rather than firing six structured calls at once.
_ARCH_CONCURRENCY = int(os.environ.get("SHILPI_ARCH_CONCURRENCY", "3"))


def _answers_summary(answers: dict, limit: int = 12000) -> str:
    """Flatten discovery answers into context text for spec generation.

    The limit was 2500, which silently discarded ~30% of a full 22-area discovery
    — and because truncation takes the tail, the fields lost were exactly the
    infrastructure ones (network zones, regions, environments, monitoring). That
    is why the first generated deployment diagram had no DMZ and no zones.
    """
    parts = []
    for k, v in (answers or {}).items():
        if k.startswith("_raw_") or v in (None, "", []):
            continue
        parts.append(f"{k}: {v}")
    return "\n".join(parts)[:limit]


async def _architecture_evidence(client: httpx.AsyncClient, answers: dict) -> str:
    """Retrieve IV's own architecture write-ups to ground the spec.

    IV's proposals describe architecture as an 'Architecture Flow (Text
    Representation)' — a node/edge narrative. Grounding on those makes the
    generated spec look like IV's house style instead of generic boxes.
    """
    vendor = answers.get("iam_vendor") or ""
    query = (f"solution architecture deployment architecture text representation "
             f"nodes components integration {vendor}").strip()
    try:
        emb = await embed_query(client, query)
        chunks = await retrieve_chunks(client, emb, query, k=6)
    except Exception as e:  # noqa: BLE001 — grounding is best-effort
        log.warning("architecture evidence retrieval failed (fail-soft): %s", e)
        return ""
    return build_evidence_block(chunks)[:6000]


async def _resolve_client_logo(answers: dict) -> str | None:
    """Turn the intake's client-logo answer into a local file path.

    The interview asks for a client logo (area 10) and assemble_docx accepts a
    path, but nothing joined them up — every document rendered the bordered
    "Client Logo" placeholder even when a logo had been supplied. The answer may
    be an https URL or a path already on disk; anything else is ignored.
    """
    raw = ""
    for key in ("client_logo", "logo_url", "logo"):
        val = answers.get(key)
        if val:
            raw = " ".join(str(val).split())
            break
    if not raw or raw.lower() in ("skip", "none", "n/a", "na", "-"):
        return None

    # A bare local path (rare, but supported).
    if not raw.lower().startswith(("http://", "https://")):
        return raw if os.path.exists(raw) else None

    # Pull the first URL out of a possibly-prose answer.
    match = re.search(r"https?://\S+", raw)
    if not match:
        return None
    url = match.group(0).rstrip(").,;\"'")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            resp = await c.get(url)
            resp.raise_for_status()
            data = resp.content
        if not data or len(data) > 8 * 1024 * 1024:
            log.warning("client logo rejected (empty or >8MB): %s", url)
            return None
        suffix = os.path.splitext(url.split("?")[0])[1].lower()
        if suffix not in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
            suffix = ".png"
        fd, path = tempfile.mkstemp(prefix="client_logo_", suffix=suffix)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        log.info("client logo downloaded (%d bytes) -> %s", len(data), path)
        return path
    except Exception as e:  # noqa: BLE001 — a missing logo must never fail a proposal
        log.warning("could not fetch client logo (%s): %s", url, e)
        return None


PLAN_KEY = "_diagram_plan"


async def load_plan(session_id: str | None, answers: dict | None = None
                    ) -> list[tuple[str, str]]:
    """The agreed diagram plan, or a freshly derived one if none is stored."""
    if answers is None and session_id:
        async with httpx.AsyncClient() as c:
            row = await supabase_client.get_intake_session(c, session_id)
        answers = (row or {}).get("answers") or {}
    answers = answers or {}
    stored = answers.get(PLAN_KEY)
    if isinstance(stored, list) and stored:
        out = []
        for item in stored:
            if isinstance(item, (list, tuple)) and len(item) == 2:
                out.append((str(item[0]), str(item[1])))
        if out:
            return out
    return chat_state.plan_diagrams(answers)


async def save_plan(session_id: str | None, plan: list[tuple[str, str]]) -> None:
    if not session_id:
        return
    try:
        async with httpx.AsyncClient() as c:
            await supabase_client.patch_intake_answers(
                c, session_id, {PLAN_KEY: [list(p) for p in plan]})
    except Exception as e:  # noqa: BLE001 — plan is re-derivable, never fatal
        log.warning("could not persist diagram plan for %s: %s", session_id, e)


async def propose_one_diagram(
    session_id: str | None,
    proposal_id: str | None,
    index: int,
    *,
    feedback: str | None = None,
) -> tuple[str, str | None, int]:
    """Generate, persist and render ONE diagram from the agreed plan.

    Returns ``(message, proposal_id, total_in_plan)``. Producing the whole set in
    one turn repeatedly exhausted the time budget and dropped diagrams silently;
    one at a time is both faster to first result and genuinely reviewable.
    """
    async with httpx.AsyncClient() as client:
        answers: dict = {}
        if session_id:
            row = await supabase_client.get_intake_session(client, session_id)
            answers = (row or {}).get("answers") or {}

        plan = await load_plan(session_id, answers)
        total = len(plan)
        if not plan or index >= total:
            return ("", proposal_id, total)

        client_name = str(answers.get("client_name") or "the client")
        proposal_type = str(answers.get("proposal_type") or "implementation")
        iam_vendor = answers.get("iam_vendor") or None

        if not proposal_id:
            proposal_id = await supabase_client.insert_generated_proposal(
                client, org_id=IV_ORG_ID, client_name=client_name,
                proposal_type=proposal_type, iam_vendor=iam_vendor,
                discovery_answers=answers, intake_session_id=session_id,
                status="architecture_review")
            if not proposal_id:
                return ("I couldn't open a proposal record to attach the architecture "
                        "to (the database didn't respond). Try again in a moment.",
                        None, total)

        evidence = await _architecture_evidence(client, answers)
        context = _answers_summary(answers)
        if feedback:
            context += f"\n\nREVIEWER FEEDBACK TO ADDRESS:\n{feedback[:1500]}"

        title, dtype = plan[index]
        full_title = f"{client_name} — {title}"
        guidance = chat_state.deployment_guidance_for(title, dtype)
        try:
            spec = await asyncio.wait_for(
                diagram_engine.generate_diagram_spec(
                    _structured_with_fallback, title=full_title, diagram_type=dtype,
                    context_text=context, client_name=client_name,
                    iam_vendor=iam_vendor, guidance=guidance, evidence_text=evidence,
                    models=DIAGRAM_LLM_MODELS or None),
                timeout=_DIAGRAM_SPEC_TIMEOUT_S)
        except asyncio.TimeoutError:
            log.warning("diagram spec timed out for %s", title)
            return (f"**{title}** didn't come back in time. Say **regenerate** to retry "
                    "it, or **skip** to move to the next diagram.", proposal_id, total)
        except Exception as e:  # noqa: BLE001
            log.error("diagram spec generation failed for %s: %s", title, e)
            return (f"I couldn't produce a usable **{title}** spec. Say **regenerate** "
                    "to retry, or **skip** to move on.", proposal_id, total)

        spec_json = spec.model_dump()
        row = await supabase_client.insert_diagram(
            client, org_id=IV_ORG_ID, generated_proposal_id=proposal_id,
            diagram_type=spec.diagram_type, title=spec.title, spec_json=spec_json,
            status="needs_review", intake_session_id=session_id)

        url = None
        if row:
            try:
                image = await asyncio.to_thread(diagram_engine.render_spec, spec, "png")
                if image:
                    path = await supabase_client.upload_diagram_render(
                        client, f"{row['id']}.png", image, content_type="image/png")
                    if path:
                        await supabase_client.update_diagram(
                            client, row["id"], {"rendered_svg_path": path})
                        url = await supabase_client.create_signed_url(
                            client, path, bucket=supabase_client.DIAGRAM_BUCKET)
            except Exception as e:  # noqa: BLE001
                log.error("diagram render/upload failed for %s: %s", title, e)

        d = {"title": spec.title, "diagram_type": spec.diagram_type,
             "text_representation": chat_state.build_spec_text_representation(spec_json),
             "url": url}
        return (chat_state.build_single_diagram_message(
            d, index, total, attempt=2 if feedback else 1), proposal_id, total)


async def propose_architecture(
    session_id: str | None,
    proposal_id: str | None,
    *,
    feedback: str | None = None,
) -> tuple[str, str | None]:
    """Generate, persist and render the proposed architecture for approval.

    Returns ``(chat_message, generated_proposal_id)``. Fail-soft throughout: a
    render or upload failure still yields a reviewable text representation,
    because the approval gate matters more than the picture.
    """
    async with httpx.AsyncClient() as client:
        answers: dict = {}
        if session_id:
            row = await supabase_client.get_intake_session(client, session_id)
            answers = (row or {}).get("answers") or {}

        client_name = str(answers.get("client_name") or "the client")
        proposal_type = str(answers.get("proposal_type") or "implementation")
        iam_vendor = answers.get("iam_vendor") or None

        # One generated_proposal row carries the whole engagement. Status starts
        # at architecture_review — the schema already models this gate.
        if not proposal_id:
            proposal_id = await supabase_client.insert_generated_proposal(
                client,
                org_id=IV_ORG_ID,
                client_name=client_name,
                proposal_type=proposal_type,
                iam_vendor=iam_vendor,
                discovery_answers=answers,
                intake_session_id=session_id,
                status="architecture_review",
            )
            if not proposal_id:
                return ("I couldn't open a proposal record to attach the architecture to "
                        "(the database didn't respond). Try again in a moment.", None)

        evidence = await _architecture_evidence(client, answers)
        context = _answers_summary(answers)
        if feedback:
            context += f"\n\nREVIEWER FEEDBACK TO ADDRESS:\n{feedback[:1500]}"

        planned = chat_state.plan_diagrams(answers)
        skipped: list[str] = []
        sem = asyncio.Semaphore(_ARCH_CONCURRENCY)

        async def _one(title: str, dtype: str) -> Optional[dict]:
            """Generate, persist and render a single diagram. None if it fails."""
            full_title = f"{client_name} — {title}"
            guidance = chat_state.deployment_guidance_for(title, dtype)
            async with sem:
                try:
                    spec = await asyncio.wait_for(
                        diagram_engine.generate_diagram_spec(
                            _structured_with_fallback,
                            title=full_title,
                            diagram_type=dtype,
                            context_text=context,
                            client_name=client_name,
                            iam_vendor=iam_vendor,
                            guidance=guidance,
                            evidence_text=evidence,
                            models=DIAGRAM_LLM_MODELS or None,
                        ),
                        timeout=_DIAGRAM_SPEC_TIMEOUT_S,
                    )
                except asyncio.TimeoutError:
                    log.warning("diagram spec timed out after %ss for %s — skipping",
                                _DIAGRAM_SPEC_TIMEOUT_S, title)
                    return None
                except Exception as e:  # noqa: BLE001 — skip this one, keep the rest
                    log.error("diagram spec generation failed for %s: %s", title, e)
                    return None

            spec_json = spec.model_dump()
            row = await supabase_client.insert_diagram(
                client,
                org_id=IV_ORG_ID,
                generated_proposal_id=proposal_id,
                diagram_type=spec.diagram_type,
                title=spec.title,
                spec_json=spec_json,
                status="needs_review",   # so it can be approved in one step
                intake_session_id=session_id,
            )

            url = None
            if row:
                try:
                    image = await asyncio.to_thread(
                        diagram_engine.render_spec, spec, "png")
                    if image:
                        path = await supabase_client.upload_diagram_render(
                            client, f"{row['id']}.png", image, content_type="image/png")
                        if path:
                            await supabase_client.update_diagram(
                                client, row["id"], {"rendered_svg_path": path})
                            url = await supabase_client.create_signed_url(
                                client, path, bucket=supabase_client.DIAGRAM_BUCKET)
                except Exception as e:  # noqa: BLE001 — never block the gate on a render
                    log.error("diagram render/upload failed (fail-soft) for %s: %s", title, e)

            return {
                "title": spec.title,
                "diagram_type": spec.diagram_type,
                "text_representation": chat_state.build_spec_text_representation(spec_json),
                "url": url,
            }

        # Run the diagrams CONCURRENTLY. Sequentially, four specs at up to 75s each
        # blew straight through the round budget and two of four were dropped —
        # the calls are independent, so there was never a reason to serialise them.
        try:
            results = await asyncio.wait_for(
                asyncio.gather(*(_one(t, d) for t, d in planned)),
                timeout=_ARCH_ROUND_BUDGET_S,
            )
        except asyncio.TimeoutError:
            log.warning("architecture round exceeded %ss overall", _ARCH_ROUND_BUDGET_S)
            results = []

        rendered = [r for r in results if r]
        got = {r["title"] for r in rendered}
        skipped = [t for t, _ in planned if f"{client_name} — {t}" not in got]

        if not rendered:
            return ("I couldn't generate an architecture spec just now — the model didn't "
                    "return a usable design within the time budget. Say **regenerate** to "
                    "try again.", proposal_id)

        iteration = 2 if feedback else 1
        msg = chat_state.build_architecture_message(rendered, iteration=iteration)
        if skipped:
            listed = ", ".join(f"**{t}**" for t in skipped)
            msg += (f"\n\n_Note: {listed} did not generate in time and "
                    f"{'is' if len(skipped) == 1 else 'are'} not shown. Approve what is "
                    "here, or say **regenerate** to retry the full set._")
        return (msg, proposal_id)


async def approve_architecture(proposal_id: str | None) -> tuple[bool, str]:
    """Approve every diagram under review for this proposal.

    Returns ``(approved_any, message)``. Approval IS the gate, so a DB failure is
    reported honestly rather than silently letting drafting proceed.
    """
    if not proposal_id:
        return (False, "I've lost track of which proposal this is — say **start over** "
                       "and we'll re-run discovery.")
    async with httpx.AsyncClient() as client:
        rows = await supabase_client.list_diagrams_for_proposal(client, proposal_id)
        approved = 0
        for row in rows or []:
            if (row.get("status") or "") != "needs_review":
                continue
            try:
                patch = diagram_engine.apply_transition(row, "approved")
            except InvalidTransition as e:
                log.warning("cannot approve diagram %s: %s", row.get("id"), e)
                continue
            patch["approved_at"] = datetime.now(timezone.utc).isoformat()
            if await supabase_client.update_diagram(client, row["id"], patch):
                approved += 1

        if approved:
            return (True, chat_state.ARCHITECTURE_APPROVED_MESSAGE)
    return (False, "There was nothing awaiting approval — say **regenerate** to produce "
                   "a fresh architecture for review.")


async def reject_architecture(proposal_id: str | None, comment: str) -> str:
    """Record a rejection with the reviewer's comment and reopen for redraft."""
    if not proposal_id:
        return chat_state.ARCHITECTURE_REJECTED_MESSAGE
    async with httpx.AsyncClient() as client:
        rows = await supabase_client.list_diagrams_for_proposal(client, proposal_id)
        for row in rows or []:
            if (row.get("status") or "") != "needs_review":
                continue
            try:
                patch = diagram_engine.apply_transition(
                    row, "rejected", rejection_comment=comment or "rejected in chat")
                await supabase_client.update_diagram(client, row["id"], patch)
                # rejected -> draft so a regeneration is a clean new iteration
                fresh = await supabase_client.get_diagram(client, row["id"])
                if fresh:
                    await supabase_client.update_diagram(
                        client, row["id"], diagram_engine.apply_transition(fresh, "draft"))
            except InvalidTransition as e:
                log.warning("cannot reject diagram %s: %s", row.get("id"), e)
    return chat_state.ARCHITECTURE_REJECTED_MESSAGE


_SELF_BASE = os.environ.get("SHILPI_SELF_BASE", "http://127.0.0.1:8000")
_DRAFT_TIMEOUT_S = float(os.environ.get("SHILPI_DRAFT_TIMEOUT_S", "1500"))


async def generate_proposal_from_chat(session_id: str | None,
                                      proposal_id: str | None) -> str:
    """Produce the full DOCX/PDF for an approved architecture, from chat.

    Deliberately calls our own /v1/generate-proposal over localhost rather than
    duplicating that pipeline: it is long, tested, and already handles depth
    tiers, appendices, export/compression and signed URLs. Passing
    ``generated_proposal_id`` is what embeds the APPROVED diagrams in the DOCX.
    """
    if not session_id:
        return ("I've lost the discovery session for this proposal — say **start over** "
                "to re-run discovery.")

    async with httpx.AsyncClient(timeout=_DRAFT_TIMEOUT_S) as client:
        row = await supabase_client.get_intake_session(client, session_id)
        answers = (row or {}).get("answers") or {}

        payload = {
            "intake_session_id": session_id,
            "client_name": str(answers.get("client_name") or "the client"),
            "proposal_type": str(answers.get("proposal_type") or "implementation"),
            "iam_vendor": answers.get("iam_vendor") or None,
            "rfp_text": str(answers.get("rfp_text") or answers.get("business_objectives") or ""),
            "proposal_depth": str(answers.get("proposal_depth") or "full"),
            "include_compliance_matrix": True,
            "lite": True,
            "include_pdf": True,
            "return_signed_urls": True,
        }
        if proposal_id:
            payload["generated_proposal_id"] = proposal_id

        try:
            resp = await client.post(f"{_SELF_BASE}/v1/generate-proposal", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            log.error("chat-driven proposal generation failed: %s", e)
            return ("The proposal generation didn't complete — details are in the brain "
                    "logs. Say **generate the proposal** to retry.")

    urls = (data or {}).get("signed_urls") or {}
    docx_url, pdf_url = urls.get("docx"), urls.get("pdf")
    depth = payload["proposal_depth"]
    lines = [
        f"## Proposal generated — {data.get('filename', 'draft')}",
        "",
        f"Depth: **{depth}**. The approved architecture diagram(s) are embedded.",
        "",
    ]
    if docx_url:
        lines.append(f"- [Download DOCX]({docx_url})")
    if pdf_url:
        lines.append(f"- [Download PDF]({pdf_url})")
    if docx_url or pdf_url:
        lines += ["", "_Links expire in 1 hour._"]
    else:
        lines.append("_The document was produced but no download link came back — check "
                     "the generated-drafts bucket._")
    lines += [
        "",
        "This is a **draft for human review**, not client-ready: check the "
        "`[ASSUMPTION]` and `[SME REVIEW]` markers, and fill in commercials yourself.",
    ]
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


async def _structured_with_fallback(response_model, messages: list[dict],
                                    models: list[str] | None = None, **kwargs):
    """Instructor structured call: try each model in turn, falling back on ANY
    exception (instructor validation, HTTP, schema). Keep max_retries low (caller
    sets it) so instructor doesn't multi-retry a bad model before we fall back.

    NOTE: falling back on exception alone is not sufficient for diagram specs —
    an EMPTY spec is schema-valid, so it counts as success here and the caller
    must check the content itself (see generate_diagram_spec)."""
    ic = instructor_client()
    chain = list(models) if models else [PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL]
    for model in chain:
        try:
            result = await ic.chat.completions.create(
                model=model, response_model=response_model, messages=messages, **kwargs
            )
            log.info("Structured LLM model=%s", model)
            return result
        except Exception as e:
            if model == chain[-1]:
                raise
            log.warning("Structured call failed on %s (%s); falling back.", model, e)


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
        # Was 768, tuned when the corpus was 11 proposals and evidence was thin.
        # At 114 proposals a requirement can match several quotable sources, and
        # the model then truncates mid-JSON: Instructor reports "The output is
        # incomplete due to a max_tokens length limit" and the requirement fails
        # entirely. The response is a small object with a handful of quotes, so
        # the headroom is cheap; the evidence cap above is the substantive fix.
        max_tokens=COMPLIANCE_MAX_TOKENS,
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
                # Classifying ONE requirement does not need the full fan-out.
                # retrieve_chunks returns k*4 (32 at TOP_K=8), which was ~70,000
                # characters of evidence -- roughly 18,000 tokens of input for a
                # yes/no/partial coverage decision. That volume also pushes the
                # model to cite more sources than the response budget allows,
                # which is what truncated the structured output mid-JSON and
                # failed the whole requirement. Retrieval is better targeted now
                # (content dedup, per-proposal cap, proposal-type reservation),
                # so the best evidence is at the top and the tail is noise.
                chunks = chunks[:COMPLIANCE_EVIDENCE_CHUNKS]
                log.info("Compliance %s: classifying against %d chunks", req.id, len(chunks))
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


def render_matrix_markdown(matrix: ComplianceMatrix, *, client_facing: bool = False) -> str:
    """Render the compliance matrix.

    Two audiences, two renders. The internal one (chat, default) keeps the
    Evidence column and the downgrade banners, because that is the whole point
    of it as a review aid.

    The CLIENT-FACING one drops both. In Amlak run 3 this table shipped inside
    the proposal carrying 4 cells of verbatim quotes from other clients'
    proposals and 11 cells of an internal QA note beginning "[DOWNGRADED to
    needs-human...]". A status of "Needs human" is also internal vocabulary;
    a client reads "To be confirmed".
    """
    header = ["| Req | Requirement | Status | Evidence | Summary | Next step |",
              "|---|---|---|---|---|---|"]
    if client_facing:
        # Req / Requirement / Status is a genuine client-facing compliance
        # matrix. Summary and Next step are NOT: they are IV's drafting notes.
        # Run 5 shipped "Reuse MOEnergy SailPoint joiner-workflow and DFCC Ping
        # JML table as base" and "The evidence corpus shows..." to a client.
        # Naming past clients is fine (IV's own proposals do it); narrating how
        # this document was assembled from them is not.
        header = ["| Req | Requirement | Status |", "|---|---|---|"]
    title = "Compliance Matrix" if client_facing else "# DRAFT Compliance Matrix"
    notes = "" if client_facing else matrix.overall_notes
    out = [title, "", notes, ""] + header
    label = {"covered": "Covered", "partial": "Partial", "missing": "Missing",
             "needs-human": "To be confirmed" if client_facing else "Needs human"}
    for e in matrix.entries:
        clean = lambda s: (s or "").replace("|", "/").replace("\n", " ").strip()
        req = clean(e.requirement_text)[:100]
        summary = clean(e.summary)
        recommendation = clean(e.recommendation)
        if client_facing:
            out.append(f"| {e.requirement_id} | {req} | {label[e.status]} |")
            continue
        ev = "; ".join(f'[{r.evidence_id}] \"{clean(r.quote)[:90]}\"'
                       for r in e.evidence_refs) or "—"
        out.append(f"| {e.requirement_id} | {req} | {label[e.status]} | {ev} | "
                   f"{summary[:200]} | {recommendation[:200]} |")
    if not client_facing:
        out += ["", "> Draft internal aid. Every 'covered'/'partial' must be verified by a human "
                    "against the cited evidence before any client-facing use."]
    return "\n".join(out)


# Internal QA annotations that must never reach a client document.
_INTERNAL_NOTE_RE = re.compile(r"\[\s*(?:DOWNGRADED|UNVERIFIED|INTERNAL)\b[^\]]*\]", re.I)
_ESCALATE_RE = re.compile(
    r"Escalate to SME[^.]*\.\s*|the model could not[^.]*\.\s*|"
    r"re-check retrieval[^.]*\.\s*", re.I)


def _strip_internal_notes(text: str) -> str:
    """Remove bracketed internal annotations and SME-escalation instructions."""
    out = _INTERNAL_NOTE_RE.sub("", text or "")
    out = _ESCALATE_RE.sub("", out)
    return re.sub(r"\s{2,}", " ", out).strip()


def _sse_chunk(content: str, resp_id: str = "chatcmpl-shilpi-compliance") -> str:
    return "data: " + json.dumps({
        "id": resp_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }) + "\n\n"


def _chat_completion_json(content: str, resp_id: str = "chatcmpl-shilpi-compliance") -> dict:
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
    # Every runtime switch that changes what lands in a client document is
    # reported here. A silent config difference makes a scored run meaningless:
    # the model override is visible for exactly that reason, and image placement
    # was enabled once with no way to confirm it had taken effect.
    return {"status": "ok", "model": MODEL_ID,
            "primary_model": PRIMARY_LLM_MODEL, "fallback_model": FALLBACK_LLM_MODEL,
            "assets_enabled": ASSETS_ENABLED,
            "asset_bucket": ASSET_BUCKET if ASSETS_ENABLED else None}


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
                # client_facing=True: this render goes into a DOCX that leaves
                # the building. It drops the Evidence column (verbatim quotes
                # from OTHER clients' proposals) and the internal downgrade
                # banners. The chat path above keeps both deliberately.
                render_matrix_markdown_fn=functools.partial(
                    render_matrix_markdown, client_facing=True),
                sections=sections,
                include_compliance_matrix=include_compliance_matrix,
                top_k=top_k,
                proposal_depth=proposal_depth,
                diagrams=embed_diagrams or None,
                # The full 22-area discovery. Previously only rfp_text reached
                # drafting, so sizing, timeline, integrations, commercials and
                # NFRs were captured, stored, and then silently discarded.
                discovery_answers=intake_answers or None,
                client_logo_path=await _resolve_client_logo(intake_answers or {}),
                # Image placement. Selection is pure (asset_selection.py); these
                # two callables are the only part that touches Supabase, which
                # keeps document_engine free of a database dependency.
                asset_fns={"library": fetch_approved_assets,
                           "download": download_asset},
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
            models=DIAGRAM_LLM_MODELS or None,
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


@app.on_event("startup")
async def _log_model_chains() -> None:
    log.info("model chains — general: %s -> %s | diagram specs: %s",
             PRIMARY_LLM_MODEL, FALLBACK_LLM_MODEL,
             " -> ".join(DIAGRAM_LLM_MODELS) if DIAGRAM_LLM_MODELS
             else f"{PRIMARY_LLM_MODEL} -> {FALLBACK_LLM_MODEL} (no override set)")


@app.get("/v1/keepalive")
async def keepalive_endpoint():
    """Touch Postgres so a Supabase free-tier project never idles into a pause.

    Free projects pause after 7 days without activity — which already happened
    once, silently breaking every DB-backed path. Cron this daily on the host:

        54 0 * * * curl -fsS -m 30 http://127.0.0.1:8000/v1/keepalive >/dev/null

    A plain /health cron is NOT enough: health touches no database.
    """
    async with httpx.AsyncClient() as client:
        ok = await supabase_client.ping(client)
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "database": "reachable" if ok else "unreachable"},
        status_code=200 if ok else 503,
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

    # Open WebUI fires extra completions against this same endpoint to generate
    # the chat title, tags and follow-up suggestions — two POSTs per user turn in
    # practice. Those payloads include the conversation history, so without this
    # guard we would read our own state marker, treat OWUI's instruction as the
    # user's answer, and advance a discovery bucket spuriously. Answer harmlessly
    # and mutate nothing.
    if chat_state.is_owui_task_prompt(q):
        return _emit_chat("Shilpi", stream, resp_id="chatcmpl-shilpi-task")

    # ---- Conversation state machine -------------------------------------
    # Previously this was an unconditional gate: parse_intake_session_id(body)
    # is ALWAYS None for Open WebUI (it has no field to carry our id, and the
    # "OWUI pipe" the old comment assumed was never built), so the handler
    # returned the same Stage-1 opener on every turn and the retrieval path
    # below was unreachable from the UI. State now travels in an invisible
    # marker inside our own assistant replies, which OWUI echoes back to us.
    #
    # An explicit intake_session_id in the body still short-circuits straight to
    # RAG, preserving the documented contract for direct API callers.
    explicit_session = parse_intake_session_id(body)
    if not explicit_session:
        state = chat_state.find_chat_state(messages)

        # Fresh thread, or an explicit request to start over -> show the router.
        if state is None or chat_state.wants_restart(q):
            return _emit_chat(
                chat_state.ROUTER_MESSAGE + "\n\n"
                + chat_state.encode_marker(chat_state.ChatState(mode=chat_state.MODE_ROUTER)),
                stream,
            )

        # --- awaiting a router choice ---
        if state.mode == chat_state.MODE_ROUTER:
            choice = chat_state.classify_router_choice(q)

            if choice is None:
                return _emit_chat(
                    chat_state.ROUTER_REPROMPT + "\n\n"
                    + chat_state.encode_marker(chat_state.ChatState(mode=chat_state.MODE_ROUTER)),
                    stream,
                )

            if choice == chat_state.CHOICE_NEW_PROPOSAL:
                # Create the intake session up front so every subsequent answer
                # has somewhere durable to land.
                try:
                    async with httpx.AsyncClient() as sclient:
                        session_id = await supabase_client.create_intake_session(
                            sclient, org_id=IV_ORG_ID, proposal_type=None,
                            client_name=None, iam_vendor=None, answers={},
                        )
                except supabase_client.SupabaseError as e:
                    log.error("could not create intake session from chat: %s", e)
                    return _emit_chat(
                        "I couldn't open a new discovery session just now (the database "
                        "didn't respond). Try again in a moment, or say **2** to search "
                        "past proposals instead.\n\n"
                        + chat_state.encode_marker(chat_state.ChatState(mode=chat_state.MODE_ROUTER)),
                        stream,
                    )
                tpl = get_intake_template(None)
                return _emit_chat(
                    chat_state.build_bucket_message(tpl, 0, first=True) + "\n\n"
                    + chat_state.encode_marker(chat_state.ChatState(
                        mode=chat_state.MODE_INTERVIEW, session=session_id, bucket=0)),
                    stream,
                )

            # Vault lookup or open discussion: acknowledge and switch to the RAG
            # path for the NEXT turn. The marker persists via the backward scan,
            # so follow-ups stay grounded without re-asking.
            ack = (
                "Sure — what would you like to know? I'll answer from the proposals "
                "already in the vault and cite what I'm drawing on."
                if choice == chat_state.CHOICE_VAULT else
                "Go ahead — what's on your mind? I'll pull on IV's past proposals "
                "where they're relevant."
            )
            return _emit_chat(
                ack + "\n\n"
                + chat_state.encode_marker(chat_state.ChatState(mode=chat_state.MODE_VAULT)),
                stream,
            )

        # --- interview in progress ---
        if state.mode == chat_state.MODE_INTERVIEW:
            tpl = get_intake_template(None)
            total = chat_state.bucket_count(tpl)
            bucket = chat_state.get_bucket(tpl, state.bucket)

            # Past the last area — this is the gap-fill loop. Discovery finished
            # with required fields still missing (an extraction that failed
            # earlier, or a skipped area), so the user's reply here is mapped
            # against exactly those fields rather than being discarded.
            if bucket is None:
                async def _fill_gaps() -> str:
                    answers: dict = {}
                    if state.session:
                        async with httpx.AsyncClient() as sclient:
                            row = await supabase_client.get_intake_session(
                                sclient, state.session)
                        answers = (row or {}).get("answers") or {}
                    ptype = answers.get("proposal_type")
                    missing_now = missing_required(answers, ptype)

                    if not missing_now:
                        plan = await load_plan(state.session, answers)
                        await save_plan(state.session, plan)
                        return (chat_state.build_plan_message(plan, answers) + "\n\n"
                                + chat_state.encode_marker(chat_state.ChatState(
                                    mode=chat_state.MODE_DIAGRAM_PLAN,
                                    session=state.session, proposal=state.proposal)))

                    gap = gap_fill_bucket(missing_now, ptype)
                    recorded = await resolve_bucket_answers(gap, q)
                    if recorded and state.session:
                        try:
                            async with httpx.AsyncClient() as sclient:
                                await supabase_client.patch_intake_answers(
                                    sclient, state.session, recorded)
                        except Exception as e:  # noqa: BLE001
                            log.warning("gap-fill patch failed for %s: %s", state.session, e)

                    still = [m for m in missing_now if m not in recorded]
                    if still:
                        listed = ", ".join(f"`{m}`" for m in still)
                        return (chat_state.build_recap_line(recorded) + "\n\n"
                                + f"Still missing: {listed}. Give me those in "
                                  "`field: value` form and I'll move on to the "
                                  "architecture proposal.\n\n"
                                + chat_state.encode_marker(state))

                    if state.session:
                        try:
                            async with httpx.AsyncClient() as sclient:
                                await supabase_client.complete_intake_session(
                                    sclient, state.session)
                        except Exception as e:  # noqa: BLE001
                            log.warning("complete_intake_session failed: %s", e)

                    plan = await load_plan(state.session)
                    await save_plan(state.session, plan)
                    return (chat_state.build_recap_line(recorded) + "\n\n"
                            + "Discovery is complete and saved.\n\n"
                            + chat_state.build_plan_message(plan, answers) + "\n\n"
                            + chat_state.encode_marker(chat_state.ChatState(
                                mode=chat_state.MODE_DIAGRAM_PLAN,
                                session=state.session, proposal=state.proposal)))

                return await _emit_chat_lazy(_fill_gaps, stream)

            async def _handle_answer() -> str:
                recorded: dict[str, str] = {}

                # On the branding area an ATTACHED IMAGE is the answer. It has to
                # be read here because last_user_text drops image parts, and the
                # is_skip() guard below would otherwise discard an attachment
                # sent with no accompanying text at all.
                attached_logo = (
                    save_attached_logo(last_user_images(messages))
                    if bucket.get("id") == LOGO_BUCKET_ID else None
                )

                if not chat_state.is_skip(q):
                    recorded = await resolve_bucket_answers(bucket, q)
                if attached_logo:
                    recorded["client_logo"] = attached_logo

                if recorded or not chat_state.is_skip(q):
                    payload = dict(recorded) if recorded else {f"_raw_{bucket['id']}": q[:4000]}
                    if state.session:
                        try:
                            async with httpx.AsyncClient() as sclient:
                                await supabase_client.patch_intake_answers(
                                    sclient, state.session, payload)
                        except Exception as e:  # noqa: BLE001 — never wedge the chat
                            log.warning("patch_intake_answers failed for %s: %s",
                                        state.session, e)

                next_index = state.bucket + 1

                # More areas to walk.
                if next_index < total:
                    parts = ([chat_state.build_recap_line(recorded)]
                             if recorded or not chat_state.is_skip(q) else [])
                    parts.append("")
                    parts.append(chat_state.build_bucket_message(tpl, next_index))
                    parts.append("")
                    parts.append(chat_state.encode_marker(chat_state.ChatState(
                        mode=chat_state.MODE_INTERVIEW, session=state.session,
                        bucket=next_index)))
                    return "\n".join(parts)

                # Last area answered -> validate required fields, then either
                # chase the gaps or move to the architecture approval gate.
                missing: list[str] = []
                if state.session:
                    try:
                        async with httpx.AsyncClient() as sclient:
                            result = await supabase_client.complete_intake_session(
                                sclient, state.session)
                        missing = list(result.get("missing") or [])
                    except Exception as e:  # noqa: BLE001
                        log.warning("complete_intake_session failed for %s: %s",
                                    state.session, e)

                if missing:
                    return (chat_state.build_recap_line(recorded) + "\n\n"
                            + chat_state.build_interview_complete_message(missing) + "\n\n"
                            + chat_state.encode_marker(chat_state.ChatState(
                                mode=chat_state.MODE_INTERVIEW, session=state.session,
                                bucket=next_index, proposal=state.proposal)))

                # Discovery complete -> agree the DIAGRAM PLAN first. Generating
                # the whole set in one turn repeatedly timed out and silently
                # dropped diagrams; the plan is instant and gives a review point.
                plan = await load_plan(state.session)
                await save_plan(state.session, plan)
                fresh: dict = {}
                if state.session:
                    async with httpx.AsyncClient() as sc:
                        r = await supabase_client.get_intake_session(sc, state.session)
                    fresh = (r or {}).get("answers") or {}
                return (chat_state.build_recap_line(recorded) + "\n\n"
                        + "Discovery is complete and saved.\n\n"
                        + chat_state.build_plan_message(plan, fresh) + "\n\n"
                        + chat_state.encode_marker(chat_state.ChatState(
                            mode=chat_state.MODE_DIAGRAM_PLAN, session=state.session,
                            proposal=state.proposal)))

            return await _emit_chat_lazy(_handle_answer, stream)

        # --- diagram plan: agree the set before generating any of it ---
        if state.mode == chat_state.MODE_DIAGRAM_PLAN:
            intent = chat_state.classify_architecture_intent(q)

            if intent == chat_state.INTENT_APPROVE:
                async def _start() -> str:
                    msg, pid, total = await propose_one_diagram(
                        state.session, state.proposal, 0)
                    if not msg:
                        return ("The diagram plan is empty — say **regenerate** to "
                                "rebuild it.\n\n" + chat_state.encode_marker(state))
                    return msg + "\n\n" + chat_state.encode_marker(
                        chat_state.ChatState(mode=chat_state.MODE_ARCHITECTURE,
                                             session=state.session,
                                             proposal=pid or state.proposal, dindex=0))
                return await _emit_chat_lazy(_start, stream)

            async def _edit() -> str:
                answers: dict = {}
                if state.session:
                    async with httpx.AsyncClient() as sc:
                        r = await supabase_client.get_intake_session(sc, state.session)
                    answers = (r or {}).get("answers") or {}
                current = await load_plan(state.session, answers)
                edited = chat_state.apply_plan_edit(current, q)
                if edited == current:
                    return chat_state.PLAN_REPROMPT + "\n\n" + chat_state.encode_marker(state)
                await save_plan(state.session, edited)
                return (chat_state.build_plan_message(edited, answers) + "\n\n"
                        + chat_state.encode_marker(state))
            return await _emit_chat_lazy(_edit, stream)

        # --- architecture review: the V1 approval gate, one diagram at a time ---
        if state.mode == chat_state.MODE_ARCHITECTURE:
            intent = chat_state.classify_architecture_intent(q)
            skip = chat_state.is_skip(q)

            if intent == chat_state.INTENT_APPROVE or skip:
                async def _approve_and_next() -> str:
                    prefix = ""
                    if not skip:
                        ok, _ = await approve_architecture(state.proposal)
                        if not ok:
                            return ("There was nothing awaiting approval — say "
                                    "**regenerate** to produce this diagram again.\n\n"
                                    + chat_state.encode_marker(state))
                        prefix = "Approved.\n\n"
                    else:
                        prefix = "Skipped.\n\n"

                    nxt = state.dindex + 1
                    plan = await load_plan(state.session)
                    if nxt >= len(plan):
                        return (chat_state.build_all_diagrams_approved_message(len(plan))
                                + "\n\n" + chat_state.encode_marker(chat_state.ChatState(
                                    mode=chat_state.MODE_DRAFTING, session=state.session,
                                    proposal=state.proposal)))
                    msg, pid, _ = await propose_one_diagram(
                        state.session, state.proposal, nxt)
                    return (prefix + msg + "\n\n" + chat_state.encode_marker(
                        chat_state.ChatState(mode=chat_state.MODE_ARCHITECTURE,
                                             session=state.session,
                                             proposal=pid or state.proposal, dindex=nxt)))
                return await _emit_chat_lazy(_approve_and_next, stream)

            if intent == chat_state.INTENT_REGENERATE:
                async def _regen() -> str:
                    msg, pid, _ = await propose_one_diagram(
                        state.session, state.proposal, state.dindex)
                    return msg + "\n\n" + chat_state.encode_marker(
                        chat_state.ChatState(mode=chat_state.MODE_ARCHITECTURE,
                                             session=state.session,
                                             proposal=pid or state.proposal,
                                             dindex=state.dindex))
                return await _emit_chat_lazy(_regen, stream)

            if intent == chat_state.INTENT_REJECT:
                async def _reject() -> str:
                    await reject_architecture(state.proposal, q)
                    msg, pid, _ = await propose_one_diagram(
                        state.session, state.proposal, state.dindex, feedback=q)
                    return ("Noted — regenerating with your feedback.\n\n" + msg + "\n\n"
                            + chat_state.encode_marker(chat_state.ChatState(
                                mode=chat_state.MODE_ARCHITECTURE, session=state.session,
                                proposal=pid or state.proposal, dindex=state.dindex)))
                return await _emit_chat_lazy(_reject, stream)

            if intent == chat_state.INTENT_DRAFT:
                return _emit_chat(
                    "The architecture still needs sign-off before I draft anything — "
                    "that's the V1 gate. Reply **approve** for the diagram above.\n\n"
                    + chat_state.encode_marker(state), stream)

            return _emit_chat(
                "Let me know how to proceed: **approve** this diagram, **regenerate** "
                "for another attempt, **skip** it, or tell me what to change.\n\n"
                + chat_state.encode_marker(state), stream)

        # --- drafting: architecture approved, full document can be produced ---
        if state.mode == chat_state.MODE_DRAFTING:
            if chat_state.classify_architecture_intent(q) != chat_state.INTENT_DRAFT:
                return _emit_chat(
                    chat_state.DRAFTING_PROMPT_MESSAGE + "\n\n"
                    + chat_state.encode_marker(state), stream)

            async def _draft() -> str:
                msg = await generate_proposal_from_chat(state.session, state.proposal)
                return msg + "\n\n" + chat_state.encode_marker(state)
            return await _emit_chat_lazy(_draft, stream)

        # MODE_VAULT falls through to the retrieval path below.

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
