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


def test_value_sanitiser_unwraps_json_and_strips_stray_colons():
    """Both artifacts were observed live and leaked into the visible recap line."""
    assert app._clean_answer_value('{"question_id": "diagram_count", "value": "4"}') == "4"
    assert app._clean_answer_value("{'question_id': 'x', \"value\": \"7\"}") == "7"
    assert app._clean_answer_value(": 2026") == "2026"
    assert app._clean_answer_value("  hybrid  ") == "hybrid"
    assert app._clean_answer_value("") == ""


def test_long_answer_containing_reset_does_not_restart_interview(monkeypatch):
    """REGRESSION at the handler level: the area-15 answer must advance to 16."""
    async def fake_extract(bucket, reply):
        return {"pain_points": "password reset volume"}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)

    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q14 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=14))},
            {"role": "user", "content":
                "pain points: password reset volume overwhelming the helpdesk, no single "
                "view of user access, audit findings on orphaned accounts, slow app "
                "onboarding. decision criteria: technical depth, delivery certainty."},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    state = cs.decode_marker(content)
    assert state is not None, "state lost - fell back to router"
    assert state.mode == cs.MODE_INTERVIEW, f"dropped out of interview into {state.mode}"
    assert state.bucket == 15, f"expected bucket 15, got {state.bucket}"
    assert "What would you like to do?" not in content


def test_extraction_timeout_still_advances_the_interview(monkeypatch):
    """A slow or wedged LLM call must never hang the chat. It advances, keeping
    the raw reply, rather than blocking for the SDK's 600s default."""
    import asyncio as _asyncio

    async def hang(*a, **kw):
        await _asyncio.sleep(60)  # far longer than the extraction budget

    saved = {}

    async def fake_patch(c, sid, answers):
        saved.update(answers)
        return {"id": sid}

    monkeypatch.setattr(app, "_structured_with_fallback", hang)
    monkeypatch.setattr(app, "_EXTRACT_TIMEOUT_S", 0.2)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)

    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q0 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=0))},
            {"role": "user", "content": "AWS, Tech, India"},
        ],
        "stream": False,
    })
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).bucket == 1, "did not advance past a timed-out extraction"
    # The raw reply is preserved so nothing the user typed is lost.
    assert any("AWS" in str(v) for v in saved.values()), f"raw reply not saved: {saved}"


def test_streaming_interview_answer_sends_keepalive_first(monkeypatch):
    async def fake_extract(bucket, reply):
        return {"client_name": "AWS"}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)

    with client.stream("POST", "/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q0 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=0))},
            {"role": "user", "content": "AWS, Tech, India"},
        ],
        "stream": True,
    }) as resp:
        assert resp.status_code == 200
        body = "".join(resp.iter_text())

    assert body.startswith(": "), "no keep-alive comment before the slow work"
    assert body.strip().endswith("data: [DONE]")
    assert "area 2 of" in body


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


# --- OWUI task-prompt guard (handler level) ---------------------------------
def test_owui_task_prompt_does_not_mutate_state():
    """OWUI fires a title/tag request per turn carrying the history. It must NOT
    be read as an interview answer and must NOT advance the bucket."""
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q3 " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=3))},
            {"role": "user", "content":
                "### Task:\nCreate a concise, 3-5 word title summarizing the chat history.\n"
                "### Output:\nJSON format: { \"title\": \"...\" }"},
        ],
        "stream": False,
    })
    assert resp.status_code == 200
    content = resp.json()["choices"][0]["message"]["content"]
    # No state marker emitted at all -> nothing was advanced or overwritten.
    assert cs.decode_marker(content) is None
    assert "area" not in content.lower()


# --- architecture gate ------------------------------------------------------
def test_discovery_completion_enters_architecture_mode(monkeypatch):
    async def fake_extract(bucket, reply):
        return {"case_studies_include": "x"}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    async def fake_complete(c, sid):
        return {"session_id": sid, "status": "complete", "complete": True, "missing": []}

    async def fake_propose(session_id, proposal_id, *, feedback=None):
        return ("## Proposed Architecture — for your approval\n(diagram here)", "prop-1")

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    monkeypatch.setattr(app.supabase_client, "complete_intake_session", fake_complete)
    monkeypatch.setattr(app, "propose_architecture", fake_propose)

    total = cs.bucket_count(app.get_intake_template(None))
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "last " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=total - 1))},
            {"role": "user", "content": "case studies to include: PingIdentity deployments"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    state = cs.decode_marker(content)
    assert state.mode == cs.MODE_ARCHITECTURE, f"expected architecture mode, got {state.mode}"
    assert state.proposal == "prop-1"
    assert "approval" in content.lower()


def test_missing_required_keeps_interview_mode(monkeypatch):
    async def fake_extract(bucket, reply):
        return {}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    async def fake_complete(c, sid):
        return {"complete": False, "missing": ["client_name"]}

    called = {"proposed": False}

    async def fake_propose(session_id, proposal_id, *, feedback=None):
        called["proposed"] = True
        return ("arch", "prop-1")

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    monkeypatch.setattr(app.supabase_client, "complete_intake_session", fake_complete)
    monkeypatch.setattr(app, "propose_architecture", fake_propose)

    total = cs.bucket_count(app.get_intake_template(None))
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "last " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=total - 1))},
            {"role": "user", "content": "something"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_INTERVIEW
    assert "client_name" in content
    assert called["proposed"] is False, "must not propose architecture with gaps open"


def test_approval_unlocks_drafting(monkeypatch):
    async def fake_approve(pid):
        assert pid == "prop-1"
        return (True, cs.ARCHITECTURE_APPROVED_MESSAGE)

    monkeypatch.setattr(app, "approve_architecture", fake_approve)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "arch " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1", proposal="prop-1"))},
            {"role": "user", "content": "approve"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_DRAFTING
    assert "approved" in content.lower()


def test_failed_approval_stays_at_the_gate(monkeypatch):
    async def fake_approve(pid):
        return (False, "nothing awaiting approval")

    monkeypatch.setattr(app, "approve_architecture", fake_approve)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "arch " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1", proposal="prop-1"))},
            {"role": "user", "content": "approve"},
        ],
        "stream": False,
    })
    assert cs.decode_marker(
        resp.json()["choices"][0]["message"]["content"]).mode == cs.MODE_ARCHITECTURE


def test_drafting_is_blocked_before_approval(monkeypatch):
    """THE V1 CONTRACT: no drafting until the architecture is approved."""
    called = {"drafted": False}

    async def fake_draft(sid, pid):
        called["drafted"] = True
        return "document"

    monkeypatch.setattr(app, "generate_proposal_from_chat", fake_draft)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "arch " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1", proposal="prop-1"))},
            {"role": "user", "content": "generate the proposal"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert called["drafted"] is False, "drafting ran without architecture approval"
    assert "sign-off" in content.lower() or "gate" in content.lower()
    assert cs.decode_marker(content).mode == cs.MODE_ARCHITECTURE


def test_rejection_regenerates_with_feedback(monkeypatch):
    seen = {}

    async def fake_reject(pid, comment):
        seen["comment"] = comment
        return "logged rejection"

    async def fake_propose(session_id, proposal_id, *, feedback=None):
        seen["feedback"] = feedback
        return ("new architecture", proposal_id)

    monkeypatch.setattr(app, "reject_architecture", fake_reject)
    monkeypatch.setattr(app, "propose_architecture", fake_propose)

    fb = ("the DMZ should sit in front of the load balancer and PingDirectory belongs in "
          "the secure zone, please also add the SuccessFactors feed to the diagram")
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "arch " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1", proposal="prop-1"))},
            {"role": "user", "content": fb},
        ],
        "stream": False,
    })
    assert seen.get("feedback") == fb, "feedback not passed to regeneration"
    assert "DMZ" in seen.get("comment", "")
    assert cs.decode_marker(
        resp.json()["choices"][0]["message"]["content"]).mode == cs.MODE_ARCHITECTURE


def test_drafting_mode_generates_the_document(monkeypatch):
    async def fake_draft(sid, pid):
        assert sid == "s1" and pid == "prop-1"
        return "## Proposal generated\n- [Download DOCX](https://example.invalid/d.docx)"

    monkeypatch.setattr(app, "generate_proposal_from_chat", fake_draft)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "ok " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_DRAFTING, session="s1", proposal="prop-1"))},
            {"role": "user", "content": "generate the proposal"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert "Download DOCX" in content
    assert cs.decode_marker(content).mode == cs.MODE_DRAFTING


def test_drafting_mode_ignores_unrelated_chatter(monkeypatch):
    called = {"drafted": False}

    async def fake_draft(sid, pid):
        called["drafted"] = True
        return "doc"

    monkeypatch.setattr(app, "generate_proposal_from_chat", fake_draft)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "ok " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_DRAFTING, session="s1", proposal="prop-1"))},
            {"role": "user", "content": "thanks"},
        ],
        "stream": False,
    })
    assert called["drafted"] is False
    assert cs.decode_marker(
        resp.json()["choices"][0]["message"]["content"]).mode == cs.MODE_DRAFTING


# --- keepalive --------------------------------------------------------------
def test_keepalive_ok(monkeypatch):
    async def fake_ping(c):
        return True
    monkeypatch.setattr(app.supabase_client, "ping", fake_ping)
    r = client.get("/v1/keepalive")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_keepalive_reports_unreachable_database(monkeypatch):
    async def fake_ping(c):
        return False
    monkeypatch.setattr(app.supabase_client, "ping", fake_ping)
    r = client.get("/v1/keepalive")
    assert r.status_code == 503
    assert r.json()["database"] == "unreachable"


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
