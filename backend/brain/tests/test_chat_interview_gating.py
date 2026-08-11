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

import asyncio
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
def test_discovery_completion_enters_diagram_plan_mode(monkeypatch):
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

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {"client_name": "AWS",
                                       "required_diagram_types": "solution/reference"}}
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)

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
    # Discovery now hands off to the DIAGRAM PLAN step, which generates nothing
    # until the set is agreed — generating everything up front used to time out.
    assert state.mode == cs.MODE_DIAGRAM_PLAN, f"expected plan mode, got {state.mode}"
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

    async def fake_one(session_id, proposal_id, index, *, feedback=None):
        seen["feedback"] = feedback
        seen["index"] = index
        return ("new diagram", proposal_id, 3)

    monkeypatch.setattr(app, "reject_architecture", fake_reject)
    monkeypatch.setattr(app, "propose_one_diagram", fake_one)

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
    # Rejection regenerates the SAME diagram, it does not advance past it.
    assert seen.get("index") == 0
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


# --- deterministic parser (zero-LLM fast path) ------------------------------
def _bucket(i):
    return app.get_intake_template(None)["buckets"][i]


def test_parser_handles_bare_positional_lists():
    """REGRESSION: 'AWS, Tech, India' silently failed to map on two live runs."""
    got = app.parse_bucket_answers(_bucket(0), "AWS, Tech, India")
    assert got == {"client_name": "AWS", "industry": "Tech", "country": "India"}


def test_parser_handles_labelled_answers():
    got = app.parse_bucket_answers(
        _bucket(1),
        "primary IAM vendor: PingIdentity. proposal type: implementation. "
        "deal size: enterprise. engagement year: 2026.")
    assert got["iam_vendor"] == "PingIdentity"
    assert got["proposal_type"] == "implementation"


def test_parser_refuses_prose_so_the_llm_gets_it():
    """A paragraph must NEVER be chopped into fake answers by comma-splitting."""
    for prose in (
        "we have a lot of legacy applications and the helpdesk is overwhelmed, so we "
        "want to consolidate everything onto one platform this year",
        "the estate is fragmented across two forests and nothing is centralised today",
    ):
        assert app.parse_bucket_answers(_bucket(3), prose) == {}, prose


def test_resolver_prefers_the_parser_and_skips_the_llm(monkeypatch):
    called = {"llm": False}

    async def fake_extract(bucket, reply):
        called["llm"] = True
        return {}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    got = asyncio.get_event_loop().run_until_complete(
        app.resolve_bucket_answers(_bucket(0), "AWS, Tech, India"))
    assert got["client_name"] == "AWS"
    assert called["llm"] is False, "parser succeeded but the LLM was still called"


def test_resolver_falls_back_to_the_llm_for_prose(monkeypatch):
    async def fake_extract(bucket, reply):
        return {"business_objectives": "consolidate"}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    got = asyncio.get_event_loop().run_until_complete(
        app.resolve_bucket_answers(
            _bucket(3), "the estate is fragmented and nothing is centralised today"))
    assert got == {"business_objectives": "consolidate"}


# --- gap-fill recovery loop -------------------------------------------------
def test_gap_fill_bucket_contains_only_the_missing_questions():
    gap = app.gap_fill_bucket(["client_name", "industry", "iam_vendor", "proposal_type"])
    assert [q["id"] for q in gap["questions"]] == [
        "client_name", "industry", "iam_vendor", "proposal_type"]


def test_gap_fill_accepts_answers_and_reaches_the_architecture_gate(monkeypatch):
    """REGRESSION: answering the 'still missing' prompt used to be discarded and
    dumped the user into vault mode, skipping the approval gate entirely."""
    saved = {}

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {"business_objectives": "x", "proposal_depth": "full"}}

    async def fake_patch(c, sid, answers):
        saved.update(answers)
        return {"id": sid}

    async def fake_complete(c, sid):
        return {"complete": True, "missing": []}

    async def fake_propose(session_id, proposal_id, *, feedback=None):
        return ("## Proposed Architecture — for your approval", "prop-9")

    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    monkeypatch.setattr(app.supabase_client, "complete_intake_session", fake_complete)
    monkeypatch.setattr(app, "propose_architecture", fake_propose)

    total = cs.bucket_count(app.get_intake_template(None))
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "missing " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=total))},
            {"role": "user", "content":
                "client_name: AWS. industry: Tech. iam_vendor: PingIdentity. "
                "proposal_type: implementation"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert saved.get("client_name") == "AWS", f"gap answers not saved: {saved}"
    assert saved.get("proposal_type") == "implementation"
    state = cs.decode_marker(content)
    # Gap-fill completion also hands off to the plan step, not straight to
    # generating every diagram.
    assert state.mode == cs.MODE_DIAGRAM_PLAN, f"expected plan mode, got {state.mode}"


def test_gap_fill_reprompts_when_still_incomplete(monkeypatch):
    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {}}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    called = {"proposed": False}

    async def fake_propose(session_id, proposal_id, *, feedback=None):
        called["proposed"] = True
        return ("arch", "p")

    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    monkeypatch.setattr(app, "propose_architecture", fake_propose)

    total = cs.bucket_count(app.get_intake_template(None))
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "missing " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=total))},
            {"role": "user", "content": "client_name: AWS"},
        ],
        "stream": False,
    })
    content = resp.json()["choices"][0]["message"]["content"]
    assert "still missing" in content.lower()
    assert cs.decode_marker(content).mode == cs.MODE_INTERVIEW
    assert called["proposed"] is False, "proposed architecture with gaps still open"


# --- architecture generation must be time-bounded ---------------------------
def test_slow_diagram_spec_is_skipped_not_hung(monkeypatch):
    """REGRESSION: spec generation had NO timeout, so four diagrams could hang the
    chat for up to 40 minutes (the OpenAI SDK's 600s default, per diagram)."""
    import asyncio as _a

    calls = {"n": 0}

    async def slow_spec(*a, **kw):
        calls["n"] += 1
        await _a.sleep(30)  # far longer than the patched budget

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {"client_name": "AWS",
                                       "required_diagram_types": "solution/reference, deployment",
                                       "diagram_count": "2"}}

    async def fake_insert_gp(c, **kw):
        return "prop-t"

    async def no_evidence(c, answers):
        return ""

    monkeypatch.setattr(app.diagram_engine, "generate_diagram_spec", slow_spec)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "insert_generated_proposal", fake_insert_gp)
    monkeypatch.setattr(app, "_architecture_evidence", no_evidence)
    monkeypatch.setattr(app, "_DIAGRAM_SPEC_TIMEOUT_S", 0.2)
    monkeypatch.setattr(app, "_ARCH_ROUND_BUDGET_S", 5.0)

    msg, pid = asyncio.get_event_loop().run_until_complete(
        app.propose_architecture("s1", None))
    assert pid == "prop-t"
    assert "regenerate" in msg.lower(), msg
    assert calls["n"] >= 1, "never attempted a spec"


def test_round_budget_bounds_total_time_not_just_each_call(monkeypatch):
    """With concurrent generation the budget caps the WHOLE round.

    Diagrams are independent so they all start together (bounded by the
    semaphore); what must be guaranteed is that the round returns promptly
    instead of hanging, even when every spec call is slow.
    """
    import asyncio as _a
    import time as _t

    async def slow(*a, **kw):
        await _a.sleep(30)

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {
            "client_name": "AWS", "diagram_count": "6",
            "required_diagram_types": ("solution/reference, deployment, security, "
                                       "auth/customer journey, integration, network")}}

    async def fake_insert_gp(c, **kw):
        return "prop-b"

    async def no_evidence(c, answers):
        return ""

    monkeypatch.setattr(app.diagram_engine, "generate_diagram_spec", slow)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "insert_generated_proposal", fake_insert_gp)
    monkeypatch.setattr(app, "_architecture_evidence", no_evidence)
    monkeypatch.setattr(app, "_DIAGRAM_SPEC_TIMEOUT_S", 10.0)
    monkeypatch.setattr(app, "_ARCH_ROUND_BUDGET_S", 0.5)

    started = _t.monotonic()
    msg, pid = asyncio.get_event_loop().run_until_complete(
        app.propose_architecture("s1", None))
    elapsed = _t.monotonic() - started

    assert elapsed < 10, f"round did not honour its budget (took {elapsed:.1f}s)"
    assert pid == "prop-b"
    assert "regenerate" in msg.lower()


def test_diagrams_are_generated_concurrently(monkeypatch):
    """Sequential generation blew the round budget and dropped 2 of 4 diagrams."""
    import asyncio as _a
    concurrent = {"now": 0, "peak": 0}

    async def tracked(*a, **kw):
        concurrent["now"] += 1
        concurrent["peak"] = max(concurrent["peak"], concurrent["now"])
        await _a.sleep(0.15)
        concurrent["now"] -= 1
        raise RuntimeError("spec unavailable")  # we only care about scheduling

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {
            "client_name": "AWS", "diagram_count": "3",
            "required_diagram_types": "solution/reference, deployment, security"}}

    async def fake_insert_gp(c, **kw):
        return "prop-c"

    async def no_evidence(c, answers):
        return ""

    monkeypatch.setattr(app.diagram_engine, "generate_diagram_spec", tracked)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "insert_generated_proposal", fake_insert_gp)
    monkeypatch.setattr(app, "_architecture_evidence", no_evidence)

    asyncio.get_event_loop().run_until_complete(app.propose_architecture("s1", None))
    assert concurrent["peak"] > 1, "diagrams ran sequentially"
    assert concurrent["peak"] <= app._ARCH_CONCURRENCY, "exceeded the concurrency limit"


# --- diagram plan -> one-at-a-time approval ---------------------------------
def _plan_state(session="s1", proposal="p1"):
    return cs.encode_marker(cs.ChatState(mode=cs.MODE_DIAGRAM_PLAN,
                                         session=session, proposal=proposal))


def test_discovery_completion_proposes_a_plan_not_four_diagrams(monkeypatch):
    """REGRESSION: generating the whole set up front timed out and silently
    dropped diagrams. The plan must come first, and generate nothing."""
    generated = {"n": 0}

    async def fake_extract(bucket, reply):
        return {"x": "y"}

    async def fake_patch(c, sid, answers):
        return {"id": sid}

    async def fake_complete(c, sid):
        return {"complete": True, "missing": []}

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {
            "client_name": "AWS", "diagram_count": "4",
            "required_diagram_types": "solution/reference, deployment, security"}}

    async def spy_one(*a, **kw):
        generated["n"] += 1
        return ("diagram", "p1", 3)

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    monkeypatch.setattr(app.supabase_client, "complete_intake_session", fake_complete)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app, "propose_one_diagram", spy_one)

    total = cs.bucket_count(app.get_intake_template(None))
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "last " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=total - 1))},
            {"role": "user", "content": "case studies: Ping deployments"}],
        "stream": False})
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_DIAGRAM_PLAN
    assert "for your approval" in content.lower()
    assert generated["n"] == 0, "generated diagrams before the plan was approved"


def test_plan_edit_drops_a_diagram_without_generating(monkeypatch):
    saved = {}

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {
            "client_name": "AWS", "diagram_count": "3",
            "required_diagram_types": "solution/reference, deployment, security"}}

    async def fake_patch(c, sid, answers):
        saved.update(answers)
        return {"id": sid}

    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)

    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "assistant", "content": "plan " + _plan_state()},
                     {"role": "user", "content": "drop the security diagram"}],
        "stream": False})
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_DIAGRAM_PLAN
    assert "Security" not in content
    assert app.PLAN_KEY in saved, "edited plan was not persisted"


def test_plan_approval_generates_only_the_first_diagram(monkeypatch):
    calls = []

    async def spy_one(session_id, proposal_id, index, *, feedback=None):
        calls.append(index)
        return (f"## Diagram {index + 1} of 3", "p1", 3)

    monkeypatch.setattr(app, "propose_one_diagram", spy_one)
    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "assistant", "content": "plan " + _plan_state()},
                     {"role": "user", "content": "approve"}],
        "stream": False})
    content = resp.json()["choices"][0]["message"]["content"]
    st = cs.decode_marker(content)
    assert st.mode == cs.MODE_ARCHITECTURE and st.dindex == 0
    assert calls == [0], f"expected only diagram 0, got {calls}"


def test_approving_a_diagram_advances_to_the_next(monkeypatch):
    calls = []

    async def fake_approve(pid):
        return (True, "ok")

    async def spy_one(session_id, proposal_id, index, *, feedback=None):
        calls.append(index)
        return (f"## Diagram {index + 1} of 3", "p1", 3)

    async def fake_load_plan(sid, answers=None):
        return [("A", "architecture"), ("B", "network"), ("C", "flow")]

    monkeypatch.setattr(app, "approve_architecture", fake_approve)
    monkeypatch.setattr(app, "propose_one_diagram", spy_one)
    monkeypatch.setattr(app, "load_plan", fake_load_plan)

    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "assistant", "content": "d " + cs.encode_marker(
            cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1",
                         proposal="p1", dindex=0))},
            {"role": "user", "content": "approve"}],
        "stream": False})
    st = cs.decode_marker(resp.json()["choices"][0]["message"]["content"])
    assert st.dindex == 1, f"did not advance, dindex={st.dindex}"
    assert calls == [1]


def test_approving_the_last_diagram_unlocks_drafting(monkeypatch):
    async def fake_approve(pid):
        return (True, "ok")

    async def fake_load_plan(sid, answers=None):
        return [("A", "architecture"), ("B", "network")]

    monkeypatch.setattr(app, "approve_architecture", fake_approve)
    monkeypatch.setattr(app, "load_plan", fake_load_plan)

    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "assistant", "content": "d " + cs.encode_marker(
            cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1",
                         proposal="p1", dindex=1))},
            {"role": "user", "content": "approve"}],
        "stream": False})
    content = resp.json()["choices"][0]["message"]["content"]
    assert cs.decode_marker(content).mode == cs.MODE_DRAFTING
    assert "approved" in content.lower()


def test_drafting_still_blocked_mid_review(monkeypatch):
    called = {"n": 0}

    async def fake_draft(sid, pid):
        called["n"] += 1
        return "doc"

    monkeypatch.setattr(app, "generate_proposal_from_chat", fake_draft)
    resp = client.post("/v1/chat/completions", json={
        "messages": [{"role": "assistant", "content": "d " + cs.encode_marker(
            cs.ChatState(mode=cs.MODE_ARCHITECTURE, session="s1",
                         proposal="p1", dindex=0))},
            {"role": "user", "content": "generate the proposal"}],
        "stream": False})
    assert called["n"] == 0, "drafted before every diagram was approved"
    assert cs.decode_marker(
        resp.json()["choices"][0]["message"]["content"]).mode == cs.MODE_ARCHITECTURE


# --- model override must actually reach the model call ----------------------
def test_diagram_model_override_is_passed_through(monkeypatch):
    """REGRESSION: DIAGRAM_LLM_MODELS was parsed from the environment and then
    never passed to generate_diagram_spec, so the override was decorative and
    every diagram silently ran on the default chain."""
    seen = {}

    async def spy_spec(structured_fn, **kw):
        seen["models"] = kw.get("models")
        raise RuntimeError("stop here — we only care about the arguments")

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {"client_name": "AWS",
                                       "required_diagram_types": "solution/reference"}}

    async def fake_insert_gp(c, **kw):
        return "prop-m"

    async def no_evidence(c, answers):
        return ""

    monkeypatch.setattr(app.diagram_engine, "generate_diagram_spec", spy_spec)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "insert_generated_proposal", fake_insert_gp)
    monkeypatch.setattr(app, "_architecture_evidence", no_evidence)
    monkeypatch.setattr(app, "DIAGRAM_LLM_MODELS", ["vendor/strong", "vendor/weak"])

    asyncio.get_event_loop().run_until_complete(
        app.propose_one_diagram("s1", None, 0))
    assert seen.get("models") == ["vendor/strong", "vendor/weak"], \
        f"override did not reach the model call: {seen.get('models')!r}"


def test_no_override_falls_back_to_the_default_chain(monkeypatch):
    seen = {}

    async def spy_spec(structured_fn, **kw):
        seen["models"] = kw.get("models")
        raise RuntimeError("stop")

    async def fake_get_session(c, sid):
        return {"id": sid, "answers": {"client_name": "AWS",
                                       "required_diagram_types": "solution/reference"}}

    async def fake_insert_gp(c, **kw):
        return "prop-m"

    async def no_evidence(c, answers):
        return ""

    monkeypatch.setattr(app.diagram_engine, "generate_diagram_spec", spy_spec)
    monkeypatch.setattr(app.supabase_client, "get_intake_session", fake_get_session)
    monkeypatch.setattr(app.supabase_client, "insert_generated_proposal", fake_insert_gp)
    monkeypatch.setattr(app, "_architecture_evidence", no_evidence)
    monkeypatch.setattr(app, "DIAGRAM_LLM_MODELS", [])

    asyncio.get_event_loop().run_until_complete(
        app.propose_one_diagram("s1", None, 0))
    assert seen.get("models") is None, "empty override should mean the default chain"


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




# ---------------------------------------------------------------------------
# Client logo attached in chat (area 10 / branding).
#
# OWUI sends attachments in the multimodal content array. last_user_text keeps
# only the "text" parts, so the image reached the brain and was discarded one
# line before anything could use it. These tests assert the CALL SITE threads it
# through, not merely that the helpers work in isolation — the failure mode in
# this project has repeatedly been a mechanism built and never wired.
# ---------------------------------------------------------------------------

import base64 as _b64  # noqa: E402

_BRANDING_BUCKET = 9  # area 10

# Smallest valid 1x1 PNG.
_PNG_BYTES = _b64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_PNG_URI = "data:image/png;base64," + _b64.b64encode(_PNG_BYTES).decode()


def _branding_turn(user_content, monkeypatch):
    """Drive one chat turn on the branding area, returning (content, patched)."""
    seen = {}

    async def fake_patch(c, sid, answers):
        seen.update(answers)
        return {"id": sid, "answers": answers}

    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    resp = client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1",
                             bucket=_BRANDING_BUCKET))},
            {"role": "user", "content": user_content},
        ],
        "stream": False,
    })
    return resp.json()["choices"][0]["message"]["content"], seen


def test_attached_png_is_recorded_as_the_client_logo(monkeypatch):
    content, patched = _branding_turn(
        [{"type": "text", "text": ""},
         {"type": "image_url", "image_url": {"url": _PNG_URI}}],
        monkeypatch,
    )
    assert "client_logo" in patched, "attached logo never reached the answers"
    path = patched["client_logo"]
    assert os.path.exists(path), f"logo file not written: {path}"
    assert open(path, "rb").read(4) == b"\x89PNG", "decoded bytes are not a PNG"
    assert cs.decode_marker(content).bucket == _BRANDING_BUCKET + 1, \
        "an image-only turn must still advance the interview"


def test_attached_logo_resolves_for_document_assembly(monkeypatch):
    """The recorded value must be something _resolve_client_logo accepts."""
    _, patched = _branding_turn(
        [{"type": "image_url", "image_url": {"url": _PNG_URI}}], monkeypatch)
    # A private loop: asyncio.run() closes the default one, and a later test in
    # this file still uses get_event_loop().run_until_complete().
    loop = asyncio.new_event_loop()
    try:
        resolved = loop.run_until_complete(app._resolve_client_logo(patched))
    finally:
        loop.close()
    assert resolved == patched["client_logo"]


def test_text_answer_on_branding_still_works(monkeypatch):
    import tempfile as _tf
    fd, existing = _tf.mkstemp(suffix=".png")
    os.close(fd)
    _, patched = _branding_turn(f"Client logo: {existing}", monkeypatch)
    assert patched.get("client_logo") == existing


def test_skip_on_branding_records_nothing(monkeypatch):
    content, patched = _branding_turn("skip", monkeypatch)
    assert patched == {}, "skip must not record a logo"
    assert cs.decode_marker(content).bucket == _BRANDING_BUCKET + 1


def test_attachment_on_a_non_branding_area_is_ignored(monkeypatch):
    """Only area 10 treats an image as the answer."""
    seen = {}

    async def fake_patch(c, sid, answers):
        seen.update(answers)
        return {"id": sid}

    async def fake_extract(bucket, reply):
        return {"client_name": "Acme"}

    monkeypatch.setattr(app, "extract_bucket_answers", fake_extract)
    monkeypatch.setattr(app.supabase_client, "patch_intake_answers", fake_patch)
    client.post("/v1/chat/completions", json={
        "messages": [
            {"role": "assistant", "content": "q " + cs.encode_marker(
                cs.ChatState(mode=cs.MODE_INTERVIEW, session="s1", bucket=0))},
            {"role": "user", "content": [
                {"type": "text", "text": "client is Acme"},
                {"type": "image_url", "image_url": {"url": _PNG_URI}},
            ]},
        ],
        "stream": False,
    })
    assert "client_logo" not in seen


def test_bad_attachments_are_ignored_not_fatal():
    assert app.save_attached_logo([]) is None
    assert app.save_attached_logo(["data:image/png;base64,!!!not-base64!!!"]) is None
    assert app.save_attached_logo(["data:application/pdf;base64,AAAA"]) is None
    assert app.save_attached_logo(["not-a-uri"]) is None
    # An http attachment is passed through for _resolve_client_logo to fetch.
    assert app.save_attached_logo(["https://x.test/logo.png"]) == "https://x.test/logo.png"


def test_last_user_images_ignores_plain_string_content():
    assert app.last_user_images([{"role": "user", "content": "hello"}]) == []
    assert app.last_user_images([]) == []


if __name__ == "__main__":
    main()
