"""
Pass 5 — /v1/chat/completions interview-gating tests. Keyless, NO network.

Strategy:
  * Set dummy env before importing app (app reads OPENROUTER/SUPABASE keys at
    import). The no-session interview path is deterministic and makes NO network
    calls, so it runs fully keyless.
  * For the session-present RAG path we stub the two network helpers
    (embed_query / retrieve_chunks) and monkeypatch httpx.AsyncClient.post so the
    existing behaviour is exercised without touching OpenRouter/Supabase.

Run directly (`python tests/test_chat_interview_gating.py`) or via pytest.
"""

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

# Dummy secrets so `import app` succeeds without real credentials or network.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.invalid")
os.environ.setdefault("SUPABASE_KEY", "test-key")

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app  # noqa: E402
import chat_state as cs  # noqa: E402

client = TestClient(app.app)


# --- unit: intake_session_id parsing ---------------------------------------
def test_parse_intake_session_id_locations():
    assert app.parse_intake_session_id({"intake_session_id": "abc"}) == "abc"
    assert app.parse_intake_session_id({"metadata": {"intake_session_id": "m1"}}) == "m1"
    assert app.parse_intake_session_id({"extra_body": {"intake_session_id": "e1"}}) == "e1"
    # whitespace trimmed, empty ignored
    assert app.parse_intake_session_id({"intake_session_id": "  x  "}) == "x"
    assert app.parse_intake_session_id({"intake_session_id": "   "}) is None
    assert app.parse_intake_session_id({}) is None
    # top-level wins over nested
    assert app.parse_intake_session_id(
        {"intake_session_id": "top", "metadata": {"intake_session_id": "nested"}}
    ) == "top"


# --- fresh thread: the ROUTER, not a straight-to-interview dive -------------
def test_fresh_thread_shows_router_not_interview():
    """Regression test for the OWUI dead-end.

    A brand-new thread must offer the three paths (new proposal / search the
    vault / something else) instead of dropping the user straight into the
    discovery interview.
    """
    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "Hi"}],
        "stream": False,
    })
    assert resp.status_code == 200
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    assert data["choices"][0]["finish_reason"] == "stop"
    assert "new proposal" in content.lower()
    assert "past proposals" in content.lower()
    # It must NOT immediately ask the Stage-1 questions.
    assert "Client / organisation name" not in content
    assert "EVIDENCE" not in content
    # Carries a router state marker for the next turn.
    assert cs.decode_marker(content).mode == cs.MODE_ROUTER


def test_router_reprompts_on_ambiguous_reply_without_advancing():
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "menu " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ROUTER))},
            {"role": "user", "content": "hmm"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert "didn't catch" in content.lower()
    assert cs.decode_marker(content).mode == cs.MODE_ROUTER


def test_choosing_option_1_creates_session_and_asks_first_bucket(monkeypatch):
    async def fake_create(c, **kwargs):
        return "sess-new-1"

    monkeypatch.setattr(app.supabase_client, "create_intake_session", fake_create)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "menu " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ROUTER))},
            {"role": "user", "content": "1"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert "Client / organisation name" in content
    assert "area 1 of" in content
    state = cs.decode_marker(content)
    assert state.mode == cs.MODE_INTERVIEW
    assert state.session == "sess-new-1"
    assert state.bucket == 0


def test_choosing_option_2_switches_to_vault_mode():
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "menu " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ROUTER))},
            {"role": "user", "content": "2"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_VAULT
    assert "Client / organisation name" not in content


def test_interview_advances_to_the_next_bucket(monkeypatch):
    """THE core regression: answering bucket 0 must produce bucket 1, not bucket 0."""
    async def fake_extract(bucket, reply):
        return {"client_name": "AWS", "industry": "Tech", "country": "India"}

    async def fake_patch(c, sid, answers):
        return {"id": sid, "status": "in_progress", "answers": answers}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)

    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q0 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=0))},
            {"role": "user", "content": "client is AWS, Industry is Tech, Country is India"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    state = cs.decode_marker(content)
    assert state.bucket == 1, "interview did not advance — the original bug"
    assert state.session == "s1"
    assert "area 2 of" in content
    assert "AWS" in content  # recap of what was captured


def test_interview_skip_advances_without_recording(monkeypatch):
    called = {"patched": False}

    async def fake_patch(c, sid, answers):
        called["patched"] = True
        return {"id": sid}

    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q0 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=0))},
            {"role": "user", "content": "skip"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).bucket == 1
    assert called["patched"] is False


def test_restart_returns_to_router_from_mid_interview():
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q5 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=5))},
            {"role": "user", "content": "actually let's start over"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_ROUTER
    assert "new proposal" in content.lower()


def test_vault_mode_reaches_the_rag_path(monkeypatch):
    """The path that was unreachable from the UI before this fix."""
    async def fake_embed(c, text):
        return [0.0] * 1536

    async def fake_retrieve(c, emb, query, k=app.TOP_K):
        return [{"chunk_text": "x", "heading": "h", "similarity": 0.5,
                 "client_name": "Acme", "iam_vendor": "sailpoint", "industry": "Banking"}]

    async def fake_post(self, url, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(app, "embed_query", fake_embed)
    monkeypatch.setattr(app, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "ack " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_VAULT))},
            {"role": "user", "content": "what did we do for Acme?"},
        ],
        "stream": False,
    })
    assert resp.json()["choices"][0]["message"]["content"] == "RAG grounded answer [1]."


# --- no-session, streaming: valid SSE ending in [DONE] ---------------------
def test_fresh_thread_stream_returns_valid_sse():
    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "hello"}],
        "stream": True,
    }) as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = "".join(resp.iter_text())

    assert body.strip().endswith("data: [DONE]")
    data_lines = [ln[len("data: "):] for ln in body.splitlines()
                  if ln.startswith("data: ") and ln.strip() != "data: [DONE]"]
    assert data_lines, "expected at least one SSE data chunk"
    reassembled = ""
    for raw in data_lines:
        chunk = json.loads(raw)
        assert chunk["object"] == "chat.completion.chunk"
        reassembled += chunk["choices"][0]["delta"]["content"]
    assert "new proposal" in reassembled.lower()


# --- session present: existing RAG path preserved --------------------------
class _FakeResp:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "id": "or-123",
            "object": "chat.completion",
            "model": app.PRIMARY_LLM_MODEL,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": "RAG grounded answer [1]."},
                         "finish_reason": "stop"}],
        }


def test_session_present_uses_rag(monkeypatch):
    async def fake_embed(c, text):
        return [0.0] * 1536

    async def fake_retrieve(c, emb, query, k=app.TOP_K):
        return [{"chunk_text": "x", "heading": "h", "similarity": 0.5,
                 "client_name": "Acme", "iam_vendor": "sailpoint", "industry": "Banking"}]

    async def fake_post(self, url, **kwargs):
        return _FakeResp()

    monkeypatch.setattr(app, "embed_query", fake_embed)
    monkeypatch.setattr(app, "retrieve_chunks", fake_retrieve)
    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)

    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "Summarise our IAM approach"}],
        "stream": False,
        "intake_session_id": "sess-1",
    })
    assert resp.status_code == 200
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    assert content == "RAG grounded answer [1]."
    assert "discovery interview" not in content.lower()
    # app rewrites the upstream model id to the public model id.
    assert data["model"] == app.MODEL_ID


# --- compliance command still works without a session ----------------------
def test_compliance_command_bypasses_interview_gate(monkeypatch):
    async def fake_matrix(c, rfp_text, reqs, top_k=app.TOP_K):
        return app.ComplianceMatrix(entries=[], overall_notes="ok", truncated=False)

    monkeypatch.setattr(app, "run_compliance_matrix", fake_matrix)
    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "compliance matrix:\nREQ: MFA required"}],
        "stream": False,
    })
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    assert "Compliance Matrix" in content
    assert "discovery interview" not in content.lower()


class _MonkeyPatch:
    """Minimal setattr-only monkeypatch for bare (non-pytest) execution."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value):
        old = getattr(target, name)
        self._undo.append((target, name, old))
        setattr(target, name, value)

    def undo(self):
        for target, name, old in reversed(self._undo):
            setattr(target, name, old)
        self._undo.clear()


def main():
    import inspect
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        if "monkeypatch" in inspect.signature(t).parameters:
            mp = _MonkeyPatch()
            try:
                t(mp)
            finally:
                mp.undo()
        else:
            t()
        print(f"  PASS {t.__name__}")
        passed += 1
    print(f"ALL {passed} CHAT INTERVIEW GATING TESTS PASSED")


if __name__ == "__main__":
    main()
