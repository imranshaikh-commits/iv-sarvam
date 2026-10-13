#!/usr/bin/env python3
"""What each of the three retrieval fixes changed, on the live corpus.

WHY THIS EXISTS
---------------
Deep audit F26, F27 and F62 each change which evidence reaches a drafted
section. They land together, and one paid run cannot say which of them moved
what. This runs the brain's own section retrieval (document_engine.
_retrieve_fanout, through app.retrieve_chunks) four times over the same
queries: with all three fixes undone, then adding them back one at a time. It
reports, per section, the chunk ids each step added and removed.

scripts/eval_retrieval.py cannot do this: it calls the SQL function directly
and never sees the de-duplication, vendor preference or fan-out merge.

No model drafts anything. Each distinct embedding and database request is made
once and shared by the four passes, so the cost is a few dozen embedding calls.
Reranking is switched off for the measurement so the passes are comparable.

HOW TO RUN (on the host, inside the running brain, which has the keys):

    docker exec -i sarvam-brain python - < scripts/retrieval_attribution.py

The queries are the implementation template's own, for a two-vendor engagement
shaped like ESNAD (Ping for access management and CIAM, Saviynt for IGA and
PAM). No client content is used or printed: only section ids and chunk ids.
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx

import app
import document_engine
from proposal_templates import (COMPLIANCE_SECTION_ID, engagement_domains,
                                get_template, split_vendors)

VENDOR = "Ping Identity for Access Management and CIAM, Saviynt for IGA and PAM"
CONTEXT = {"client_name": "the client", "iam_vendor": VENDOR,
           "iam_vendors": split_vendors(VENDOR), "proposal_type": "implementation",
           "rfp_text": "", "discovery_answers": {}, **engagement_domains(VENDOR, {})}
FANOUT = 3          # the "full" depth tier


# --- the three behaviours as they were before the fixes -----------------------

def _old_key(chunk: dict) -> str:                              # before F26
    return (chunk.get("chunk_text") or chunk.get("heading") or "")[:160]


def _old_cap(by_vendor: list[list[dict]]) -> list[dict]:       # before F27
    pooled = sorted((c for mine in by_vendor for c in mine),
                    key=lambda c: float(c.get("similarity") or 0.0), reverse=True)
    return pooled[:document_engine.PRODUCT_EVIDENCE_PER_VENDOR * max(1, len(by_vendor))]


def _old_vendor(query: str):                                   # before F62
    q = (query or "").lower()
    return next((kw for kw in ("sailpoint", "ping", "forgerock", "ibm", "keycloak",
                               "okta", "microsoft") if kw in q), None)


_NEW = {"key": document_engine._evidence_key, "cap": document_engine._cap_product_evidence,
        "vendor": app.detect_vendor}
STEPS = (
    ("before F26, F27 and F62", {"key": _old_key, "cap": _old_cap, "vendor": _old_vendor}),
    ("+ F26 (merge by chunk id)", {"cap": _old_cap, "vendor": _old_vendor}),
    ("+ F27 (product cap per vendor)", {"vendor": _old_vendor}),
    ("+ F62 (whole-word vendor detection) = current code", {}),
)


class _Once(httpx.AsyncClient):
    """Sends each distinct POST once; the four passes share the answers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._answers: dict = {}

    async def post(self, url, **kwargs):
        key = (str(url), json.dumps(kwargs.get("json"), sort_keys=True))
        if key not in self._answers:
            self._answers[key] = await super().post(url, **kwargs)
        return self._answers[key]


async def measure(client) -> list[dict]:
    """[{"step", "sections": {section id: {"evidence": [ids], "product": [ids]}}}]"""
    app.RERANK_ENABLED = False
    specs = [s for s in get_template("implementation")
             if not s.optional and s.id != COMPLIANCE_SECTION_ID]
    out = []
    for name, undo in STEPS:
        use = {**_NEW, **undo}
        document_engine._evidence_key = use["key"]
        document_engine._cap_product_evidence = use["cap"]
        app.detect_vendor = use["vendor"]
        try:
            sections = {}
            for spec in specs:
                evidence, product = await document_engine._retrieve_fanout(
                    client, spec, CONTEXT, embed_fn=app.embed_query,
                    retrieve_fn=app.retrieve_chunks, top_k=app.TOP_K, fanout=FANOUT,
                    retrieve_product_fn=app.retrieve_product_chunks)
                sections[spec.id] = {
                    "evidence": [c.get("chunk_id") for c in evidence if c.get("chunk_id")],
                    "product": [c.get("chunk_id") for c in product if c.get("chunk_id")]}
        finally:
            document_engine._evidence_key = _NEW["key"]
            document_engine._cap_product_evidence = _NEW["cap"]
            app.detect_vendor = _NEW["vendor"]
        out.append({"step": name, "sections": sections})
    return out


def report(steps: list[dict]) -> str:
    """Per step, against the step before it: what was added and removed."""
    lines = []
    for before, after in zip(steps, steps[1:]):
        lines.append(after["step"])
        moved = 0
        for sid, now in after["sections"].items():
            was = before["sections"].get(sid, {"evidence": [], "product": []})
            for kind in ("evidence", "product"):
                added = [i for i in now[kind] if i not in was[kind]]
                removed = [i for i in was[kind] if i not in now[kind]]
                if added or removed:
                    moved += len(added) + len(removed)
                    lines.append(f"  {sid:28s} {kind:8s} +{len(added)} -{len(removed)} "
                                 f"(now {len(now[kind])})")
        if not moved:
            lines.append("  no section changed")
        lines.append("")
    return "\n".join(lines)


async def main() -> int:
    async with _Once() as client:
        steps = await measure(client)
    print(report(steps))
    print("JSON (chunk ids per step and section):")
    print(json.dumps({"steps": steps}))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
