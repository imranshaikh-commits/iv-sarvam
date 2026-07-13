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

import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

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


async def retrieve_chunks(client: httpx.AsyncClient, embedding: list[float], k: int = TOP_K) -> list[dict]:
    resp = await client.post(
        f"{SUPABASE_URL}/rest/v1/rpc/match_proposal_chunks",
        headers={
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
        },
        json={"query_embedding": embedding, "match_count": k},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


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


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_ID, "draft_model": DRAFT_MODEL}


@app.get("/v1/models")
async def models():
    return {
        "object": "list",
        "data": [{"id": MODEL_ID, "object": "model", "created": int(time.time()), "owned_by": "inspiritvision"}],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    stream = bool(body.get("stream", False))
    query = last_user_text(messages)

    async with httpx.AsyncClient() as client:
        # 1-2. Embed + retrieve (fail soft: draft without evidence rather than 500)
        chunks: list[dict] = []
        if query.strip():
            try:
                emb = await embed_query(client, query)
                chunks = await retrieve_chunks(client, emb)
                log.info("Retrieved %d chunks for query: %.80s", len(chunks), query)
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
