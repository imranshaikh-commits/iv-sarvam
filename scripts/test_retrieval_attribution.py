"""Offline check of retrieval_attribution.py: it runs end to end on synthetic
rows and attributes each change to the fix that caused it."""
import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "test-key")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "brain"))
sys.path.insert(0, str(Path(__file__).parent))

import app  # noqa: E402
import document_engine  # noqa: E402
import retrieval_attribution as A  # noqa: E402

# 192 characters shared by both chunks, then bodies that differ. Longer shared
# openings never reach the fan-out merge: retrieve_chunks' own near-duplicate
# pass compares the first 400 characters and drops one of the two first.
_OPENING = "Inspirit Vision proposes the following sizing for the platform. " * 3
_PROD = "Production runs three nodes of eight vCPU and thirty-two gigabytes each. " * 5
_DR = "Disaster recovery keeps two smaller nodes warm in the second data centre. " * 5


class _Resp:
    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


class _FakeCorpus:
    """Stands in for OpenRouter embeddings and the two retrieval RPCs."""

    def __init__(self):
        self.calls = 0

    async def post(self, url, **kwargs):
        self.calls += 1
        body = kwargs.get("json") or {}
        if url.endswith("/embeddings"):
            return _Resp({"data": [{"embedding": [0.0]}]})
        if "match_partner_product_chunks" in url:
            vendor = body.get("filter_vendor") or ""
            score = 0.9 if vendor.startswith("Ping") else 0.5
            return _Resp([{"chunk_id": f"{vendor[:4]}-{i}", "chunk_text": f"{vendor} doc {i}",
                           "similarity": score - i / 100, "vendor": vendor} for i in range(8)])
        # Two different chunks that open with the same 160 characters.
        return _Resp([
            {"chunk_id": "c-prod", "chunk_text": _OPENING + _PROD,
             "heading": "Sizing", "similarity": 0.7, "iam_vendor": "SailPoint"},
            {"chunk_id": "c-dr", "chunk_text": _OPENING + _DR,
             "heading": "Sizing DR", "similarity": 0.6, "iam_vendor": "SailPoint"}])


def test_each_fix_is_attributed_to_its_own_step():
    new_key, new_cap, new_vendor = (document_engine._evidence_key,
                                    document_engine._cap_product_evidence, app.detect_vendor)
    steps = asyncio.run(A.measure(_FakeCorpus()))
    assert [s["step"] for s in steps] == [name for name, _ in A.STEPS] and len(steps) == 4
    before, f26, f27, f62 = (s["sections"] for s in steps)
    section = "proposed_solution"

    # F26: the second chunk, merged away on its opening before, is back.
    assert before[section]["evidence"] == ["c-prod"]
    assert sorted(f26[section]["evidence"]) == ["c-dr", "c-prod"]
    assert f26[section]["product"] == before[section]["product"]      # and nothing else moved

    # F27: each vendor gets its own share. At the default top_k of 8 the old
    # pooled cut gave the better-matching vendor all 8 of its chunks and the
    # other the 4 slots left over; it starved a vendor outright only at a
    # higher top_k (document_engine's own test covers that case).
    def share(sections):
        ids = sections[section]["product"]
        return (sum(i.startswith("Ping") for i in ids), sum(i.startswith("Savi") for i in ids))
    assert share(f26) == (8, 4)
    assert share(f27) == (6, 6)
    assert f27[section]["evidence"] == f26[section]["evidence"]

    text = A.report(steps)
    assert "+ F26" in text and "+ F27" in text and "+ F62" in text
    json.dumps({"steps": steps})                                      # it is printable
    # The brain is left as it was found.
    assert (document_engine._evidence_key, document_engine._cap_product_evidence,
            app.detect_vendor) == (new_key, new_cap, new_vendor)


def test_each_distinct_request_is_sent_once(monkeypatch):
    """Four passes over the same queries must not cost four times the calls."""
    import httpx
    sent = []

    async def fake_send(self, url, **kwargs):
        sent.append(url)
        return _Resp({"ok": True})

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_send)

    async def run():
        async with A._Once() as client:
            for _ in range(4):
                await client.post("https://x/embeddings", json={"input": ["q"]})
            await client.post("https://x/embeddings", json={"input": ["other"]})
    asyncio.run(run())
    assert len(sent) == 2
