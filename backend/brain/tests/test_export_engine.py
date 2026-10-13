"""
Sprint 5 Pass 6 — export pipeline tests. Keyless, no network, no live soffice.

Covers:
  (a) lite DOCX compression reduces size + preserves a valid DOCX
  (b) fail-soft: no images / already small / cannot reach target
  (c) PDF export via a MOCKED soffice subprocess + graceful missing-binary handling
  (d) Supabase upload + signed-URL (MOCKED), incl. missing-bucket / failure cases
  (e) legacy /v1/generate-proposal response unchanged by default (DOCX binary)
  (f) opt-in export returns JSON with DOCX/PDF metadata + signed URLs

soffice / OpenRouter / Supabase are all mocked — nothing real is invoked.
"""

import asyncio
import io
import os
import subprocess
import sys

# Make the brain package importable when run from any cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BRAIN = os.path.dirname(_HERE)
if _BRAIN not in sys.path:
    sys.path.insert(0, _BRAIN)

# Dummy (non-secret) env so app.py + supabase_client import without real creds.
os.environ.setdefault("OPENROUTER_API_KEY", "test-key")
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")

from docx import Document
from PIL import Image

import export_engine


# --- helpers ----------------------------------------------------------------

def _docx_bytes(document: Document) -> bytes:
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def _image_stream(size, fmt="JPEG") -> io.BytesIO:
    """A high-entropy image so JPEG can't trivially compress it (forces the
    downscale/quality loop to do real work)."""
    w, h = size
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    bio = io.BytesIO()
    if fmt == "JPEG":
        img.save(bio, format="JPEG", quality=95)
    else:
        img.save(bio, format="PNG")
    bio.seek(0)
    return bio


def _docx_with_image(size=(1200, 1200), fmt="JPEG") -> bytes:
    from docx.shared import Inches

    doc = Document()
    doc.add_heading("Proposal", level=1)
    doc.add_paragraph("Body text.")
    doc.add_picture(_image_stream(size, fmt), width=Inches(6.0))
    return _docx_bytes(doc)


def _plain_docx() -> bytes:
    doc = Document()
    doc.add_heading("Proposal", level=1)
    doc.add_paragraph("No images here.")
    return _docx_bytes(doc)


def _opens_as_docx(data: bytes) -> bool:
    try:
        Document(io.BytesIO(data))
        return True
    except Exception:
        return False


# --- (a) compression reduces size + preserves valid DOCX --------------------

def test_compress_reduces_size_and_preserves_docx():
    original = _docx_with_image(size=(1400, 1400), fmt="JPEG")
    # Force the loop with a small target well under the image-heavy original.
    out, meta = export_engine.compress_docx_lite(original, max_bytes=60_000)
    assert meta["compressed"] is True
    assert meta["final_size"] < meta["original_size"]
    assert len(out) < len(original)
    assert meta["images_processed"] == 1
    assert _opens_as_docx(out), "compressed DOCX must still open as a valid Word doc"


# --- (b) fail-soft branches -------------------------------------------------

def test_compress_no_images_is_noop():
    original = _plain_docx()
    out, meta = export_engine.compress_docx_lite(original, max_bytes=1)
    assert out == original  # unchanged bytes
    assert meta["images_processed"] == 0
    assert "no embedded images" in meta["note"]


def test_compress_already_small_is_noop():
    original = _docx_with_image(size=(200, 200), fmt="JPEG")
    out, meta = export_engine.compress_docx_lite(original, max_bytes=export_engine.LITE_MAX_BYTES)
    assert out == original
    assert "already under target" in meta["note"]


def test_compress_cannot_reach_target_warns_but_survives():
    original = _docx_with_image(size=(1400, 1400), fmt="JPEG")
    out, meta = export_engine.compress_docx_lite(original, max_bytes=1)  # impossible
    assert meta["under_target"] is False
    assert "warning" in meta
    assert _opens_as_docx(out), "best-effort result must remain a valid DOCX"


def test_compress_bad_zip_fails_soft():
    out, meta = export_engine.compress_docx_lite(b"not a zip at all")
    assert out == b"not a zip at all"
    assert "warning" in meta


# --- (c) PDF export (mocked soffice) ----------------------------------------

def test_export_pdf_mocked_success(monkeypatch):
    monkeypatch.setattr(export_engine, "_soffice_binary", lambda: "/usr/bin/soffice")

    def fake_run(cmd, **kwargs):
        outdir = cmd[cmd.index("--outdir") + 1]
        with open(os.path.join(outdir, "proposal.pdf"), "wb") as f:
            f.write(b"%PDF-1.4 mocked pdf\n%%EOF")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(export_engine.subprocess, "run", fake_run)

    pdf, err = export_engine.export_pdf(_plain_docx())
    assert err is None
    assert pdf is not None and pdf.startswith(b"%PDF")


def test_export_pdf_missing_binary_fails_soft(monkeypatch):
    monkeypatch.setattr(export_engine, "_soffice_binary", lambda: None)
    pdf, err = export_engine.export_pdf(_plain_docx())
    assert pdf is None
    assert "not found" in err


def test_export_pdf_conversion_error_fails_soft(monkeypatch):
    monkeypatch.setattr(export_engine, "_soffice_binary", lambda: "/usr/bin/soffice")

    def boom(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"conversion boom")

    monkeypatch.setattr(export_engine.subprocess, "run", boom)
    pdf, err = export_engine.export_pdf(_plain_docx())
    assert pdf is None
    assert "PDF conversion failed" in err


def test_export_pdf_no_output_file_fails_soft(monkeypatch):
    monkeypatch.setattr(export_engine, "_soffice_binary", lambda: "/usr/bin/soffice")
    # Succeeds but writes nothing -> no output file.
    monkeypatch.setattr(
        export_engine.subprocess, "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""),
    )
    pdf, err = export_engine.export_pdf(_plain_docx())
    assert pdf is None
    assert "no output" in err


# --- (d) Supabase storage upload + signed URL (mocked) ----------------------

import supabase_client  # noqa: E402


class _FakeResp:
    def __init__(self, *, json_data=None, raise_exc=None):
        self._json = json_data or {}
        self._raise = raise_exc

    def raise_for_status(self):
        if self._raise:
            raise self._raise

    def json(self):
        return self._json


class _FakeClient:
    """Minimal async httpx.AsyncClient stand-in returning a queued response."""

    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self._exc:
            raise self._exc
        return self._resp


def test_upload_generated_draft_success():
    client = _FakeClient(resp=_FakeResp())
    path = asyncio.run(
        supabase_client.upload_generated_draft(client, "prop/file.docx", b"data", "application/x")
    )
    assert path == "prop/file.docx"
    assert client.calls and "generated-drafts" in client.calls[0][0]


def test_upload_generated_draft_missing_bucket_fails_soft():
    client = _FakeClient(exc=RuntimeError("bucket not found"))
    path = asyncio.run(
        supabase_client.upload_generated_draft(client, "prop/file.docx", b"data", "application/x")
    )
    assert path is None


def test_create_signed_url_success():
    resp = _FakeResp(json_data={"signedURL": "/object/sign/generated-drafts/prop/file.docx?token=abc"})
    client = _FakeClient(resp=resp)
    url = asyncio.run(supabase_client.create_signed_url(client, "prop/file.docx"))
    assert url == (
        f"{supabase_client.SUPABASE_URL}/storage/v1"
        "/object/sign/generated-drafts/prop/file.docx?token=abc"
    )


def test_create_signed_url_failure_fails_soft():
    client = _FakeClient(exc=RuntimeError("no bucket"))
    url = asyncio.run(supabase_client.create_signed_url(client, "prop/file.docx"))
    assert url is None


def test_insert_generated_proposal_uses_valid_status():
    """Regression: status must be 'drafting' (a value allowed by the
    generated_proposals_status_check CHECK constraint), never 'draft' (which 400s
    and left generated_proposals empty)."""
    resp = _FakeResp(json_data=[{"id": "prop-999"}])
    client = _FakeClient(resp=resp)
    pid = asyncio.run(
        supabase_client.insert_generated_proposal(
            client,
            org_id="org-1",
            client_name="Test Client",
            proposal_type="implementation",
        )
    )
    assert pid == "prop-999"
    assert client.calls, "expected an insert POST"
    payload = client.calls[0][1]["json"]
    assert payload["status"] == "drafting"


# --- (e)+(f) endpoint behaviour (legacy vs opt-in) --------------------------

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402

_SAMPLE_DOCX = _plain_docx()


async def _fake_generate_proposal(client, **kwargs):
    return {
        "docx_bytes": _SAMPLE_DOCX,
        "sections_meta": [{"id": "executive_summary", "title": "Executive Summary"}],
        "draft_markdown": "## Executive Summary\n\nBody.",
        "filename": "Shilpi_Proposal_Test_20260101_000000.docx",
        "included_compliance_matrix": False,
        "proposal_depth": "standard",
        "included_appendices": False,
    }


async def _fake_insert(*args, **kwargs):
    return "prop-123"


async def _fake_set_usage(*args, **kwargs):
    return True


def _patch_common(monkeypatch, direct=True):
    monkeypatch.setattr(app_module, "generate_proposal", _fake_generate_proposal)
    monkeypatch.setattr(app_module.supabase_client, "insert_generated_proposal", _fake_insert)
    monkeypatch.setattr(app_module.supabase_client, "set_proposal_usage", _fake_set_usage)
    # These tests exercise the export step alone, with no intake session: the
    # privileged evaluation mode, switched on explicitly.
    monkeypatch.setattr(app_module, "ALLOW_DIRECT_GENERATE", direct)


def _base_body():
    return {
        "rfp_text": "Deliver an IAM implementation.",
        "client_name": "Test Client",
        "proposal_type": "implementation",
    }


def test_legacy_response_is_docx_binary_by_default(monkeypatch):
    _patch_common(monkeypatch)
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json=_base_body())
    assert resp.status_code == 200
    assert resp.headers["content-type"] == app_module.DOCX_MEDIA_TYPE
    assert resp.content == _SAMPLE_DOCX  # byte-for-byte unchanged
    assert "attachment; filename=" in resp.headers["content-disposition"]


def test_optin_export_returns_json_with_metadata_and_signed_urls(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_upload(client, path, data, content_type):
        return path

    async def fake_sign(client, path, expires_in=3600):
        return f"https://example.supabase.co/storage/v1/object/sign/generated-drafts/{path}?token=x"

    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    monkeypatch.setattr(app_module.export_engine, "export_pdf", lambda b: (b"%PDF-1.4 mock", None))

    body = {**_base_body(), "lite": True, "include_pdf": True, "return_signed_urls": True}
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json=body)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")
    data = resp.json()
    assert data["docx"]["lite"] is True
    assert data["docx"]["size"] > 0
    assert data["pdf"]["size"] == len(b"%PDF-1.4 mock")
    assert data["signed_urls"]["docx"].startswith("https://example.supabase.co/")
    assert data["signed_urls"]["pdf"].startswith("https://example.supabase.co/")
    assert data["generated_proposal_id"] == "prop-123"


def test_optin_pdf_error_and_missing_bucket_fail_soft(monkeypatch):
    _patch_common(monkeypatch)

    async def fake_upload_fail(client, path, data, content_type):
        return None  # bucket missing

    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload_fail)
    monkeypatch.setattr(
        app_module.export_engine, "export_pdf",
        lambda b: (None, "LibreOffice (soffice) binary not found on PATH; PDF export unavailable"),
    )

    body = {**_base_body(), "include_pdf": True, "return_signed_urls": True}
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json=body)
    assert resp.status_code == 200
    data = resp.json()
    assert "error" in data["pdf"]
    assert data["signed_urls"] == {}
    assert "storage_notes" in data


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))


# ---------------------------------------------------------------------------
# Audit 2026-10-12, findings 4 and 6: generation is the end of a workflow, and
# the service knows who is calling.
# ---------------------------------------------------------------------------

def test_generation_without_an_intake_session_is_refused(monkeypatch):
    _patch_common(monkeypatch, direct=False)
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json=_base_body())
    assert resp.status_code == 400 and "intake_session_id" in resp.json()["error"]


def _session_with_diagrams(monkeypatch, rows):
    async def fake_session(client, sid):
        return {"id": sid, "answers": {}, "client_name": "Test Client",
                "proposal_type": "implementation"}

    async def fake_diagrams(client, sid):
        return rows

    async def fake_link(*a, **k):
        return None
    monkeypatch.setattr(app_module.supabase_client, "get_intake_session", fake_session)
    monkeypatch.setattr(app_module.supabase_client, "list_diagrams_for_session", fake_diagrams)
    monkeypatch.setattr(app_module.supabase_client, "link_intake_to_proposal", fake_link)


def test_generation_is_refused_while_a_diagram_awaits_review(monkeypatch):
    _patch_common(monkeypatch, direct=False)
    _session_with_diagrams(monkeypatch, [
        {"id": "d2", "title": "X — Solution", "status": "needs_review", "spec_json": {}},
        {"id": "d1", "title": "X — Solution", "status": "approved", "spec_json": {}}])
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json={**_base_body(), "intake_session_id": "s1"})
    # The older approved version does not make the newer, unreviewed one acceptable.
    assert resp.status_code == 409 and "X — Solution" in resp.json()["error"]


_ONE_APPROVED = [{"id": "d1", "title": "X — Solution Architecture", "status": "approved",
                  "spec_json": {}}]
_ONE_PLANNED = [("Solution Architecture", "architecture")]


def test_generation_proceeds_from_a_reviewed_session(monkeypatch):
    _patch_common(monkeypatch, direct=False)
    # Reviewed means every planned diagram approved or skipped (deep audit F17);
    # this used to pass a session with no diagrams at all.
    _reviewed_session(monkeypatch, _ONE_APPROVED, _ONE_PLANNED)
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json={**_base_body(), "intake_session_id": "s1"})
    assert resp.status_code == 200


def test_service_key_is_required_when_configured(monkeypatch):
    _patch_common(monkeypatch)
    monkeypatch.setattr(app_module, "SERVICE_KEY", "s3cret")
    with TestClient(app_module.app) as tc:
        assert tc.post("/v1/generate-proposal", json=_base_body()).status_code == 401
        assert tc.post("/v1/generate-proposal", json=_base_body(),
                       headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert tc.post("/v1/generate-proposal", json=_base_body(),
                       headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert tc.get("/health").status_code == 200          # liveness stays open


def test_a_session_opened_by_someone_else_reads_as_not_found():
    import asyncio
    import httpx
    import supabase_client as sc

    def handler(req):
        return httpx.Response(200, json=[{"id": "s1", "created_by_email": "alice@iv.test",
                                          "answers": {"client_name": "Secret"}}])

    async def read(user):
        sc.REQUEST_USER.set(user)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await sc.get_intake_session(c, "s1")
    assert asyncio.run(read("bob@iv.test")) is None
    assert asyncio.run(read("Alice@IV.test"))["id"] == "s1"
    assert asyncio.run(read(None))["id"] == "s1"            # no identity forwarded: prior behaviour


def test_forwarded_identity_reaches_the_work_of_every_route(monkeypatch):
    """The dependency sets who is asking; the endpoint's own work must see it."""
    _patch_common(monkeypatch)
    seen = {}

    async def spying_generate(*a, **k):
        seen["user"] = app_module.supabase_client.REQUEST_USER.get()
        seen["uid"] = app_module.rfp_intake.REQUEST_USER_ID.get()
        return await _fake_generate_proposal(*a, **k)
    monkeypatch.setattr(app_module, "generate_proposal", spying_generate)
    with TestClient(app_module.app) as tc:
        tc.post("/v1/generate-proposal", json=_base_body(),
                headers={"X-OpenWebUI-User-Email": "imran@iv.test", "X-OpenWebUI-User-Id": "u-1"})
    assert seen == {"user": "imran@iv.test", "uid": "u-1"}


def test_a_second_draft_of_the_same_proposal_is_not_started(monkeypatch):
    """Audit 2026-10-12 finding 10: a retry started another paid generation."""
    _patch_common(monkeypatch, direct=False)
    _reviewed_session(monkeypatch, _ONE_APPROVED, _ONE_PLANNED)
    body = {**_base_body(), "intake_session_id": "s1"}
    with TestClient(app_module.app) as tc:
        monkeypatch.setattr(app_module, "_DRAFTS_IN_FLIGHT", {"s1"})
        resp = tc.post("/v1/generate-proposal", json=body)
        assert resp.status_code == 409 and "already being generated" in resp.json()["error"]
        # A different proposal is refused only at the overall limit.
        monkeypatch.setattr(app_module, "_DRAFTS_IN_FLIGHT", {"s8", "s9"})
        assert tc.post("/v1/generate-proposal", json=body).status_code == 409
        monkeypatch.setattr(app_module, "_DRAFTS_IN_FLIGHT", set())
        assert tc.post("/v1/generate-proposal", json=body).status_code == 200
        assert app_module._DRAFTS_IN_FLIGHT == set()      # released when it finishes


def test_a_call_with_no_cost_is_recorded_as_unpriced_never_as_zero(monkeypatch):
    """Deep audit F49: a response that reported no cost was added as $0, so an
    unknown spend read as a free run. The ledger now says how many calls were
    unpriced, the total is null when none was priced, and the request survives."""
    import document_engine
    stored = {}

    async def generate_with_usage(client, **kwargs):
        document_engine.record_usage({"prompt_tokens": 10, "completion_tokens": 2})  # no cost
        return await _fake_generate_proposal(client, **kwargs)

    async def capture_usage(client, proposal_id, usage):
        stored.update(usage)
        return True

    _patch_common(monkeypatch)
    monkeypatch.setattr(app_module, "generate_proposal", generate_with_usage)
    monkeypatch.setattr(app_module.supabase_client, "set_proposal_usage", capture_usage)
    with TestClient(app_module.app) as tc:
        resp = tc.post("/v1/generate-proposal", json=_base_body())
    assert resp.status_code == 200
    assert stored["cost_usd"] is None and stored["unpriced_calls"] == 1

    # Priced and unpriced calls together: the known part is kept and flagged.
    led = document_engine.start_ledger()
    document_engine.record_usage({"prompt_tokens": 1, "cost": 0.25})
    document_engine.record_usage({"prompt_tokens": 1, "cost": 0})        # a cached call IS priced
    document_engine.record_usage({"prompt_tokens": 1})
    assert led["cost_usd"] == 0.25 and led["unpriced_calls"] == 1 and led["calls"] == 3
    assert "unpriced" in document_engine.cost_text(led)


def test_the_matrix_breakdown_travels_from_drafting_to_the_chat_reply(monkeypatch):
    """Deep audit F08: how many matrix rows rest on no IV precedent has to be
    seen on a run before the wording for them is chosen. Caller-side check:
    the count must leave generate_proposal, the endpoint and the chat reply."""
    import asyncio
    breakdown = {"covered": {"rows": 20, "with_reference": 18},
                 "missing": {"rows": 10, "with_reference": 9}}

    async def generate_with_breakdown(client, **kwargs):
        return {**await _fake_generate_proposal(client, **kwargs),
                "compliance_breakdown": breakdown}

    async def fake_upload(client, path, data, content_type):
        return path

    async def fake_sign(client, path, expires_in=3600):
        return "https://example.supabase.co/x"

    _patch_common(monkeypatch)
    monkeypatch.setattr(app_module, "generate_proposal", generate_with_breakdown)
    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    with TestClient(app_module.app) as tc:
        data = tc.post("/v1/generate-proposal",
                       json={**_base_body(), "return_signed_urls": True}).json()
    assert data["compliance_breakdown"] == breakdown

    # ...and the chat reply built from that JSON says it in words.
    async def fake_session(client, sid):
        return {"answers": {"client_name": "Test Client", "proposal_type": "implementation",
                            "rfp_text": "x"}}

    class _Resp:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return data

    async def fake_post(self, url, **kwargs):
        return _Resp()

    monkeypatch.setattr(app_module.supabase_client, "get_intake_session", fake_session)
    monkeypatch.setattr(app_module.httpx.AsyncClient, "post", fake_post)
    reply = asyncio.run(app_module.generate_proposal_from_chat("sess-1", "prop-1"))
    assert "9 of 10" in reply and "no IV precedent" in reply


# ---------------------------------------------------------------------------
# Deep audit F03: ownership is checked wherever a row is reached by an id a
# user can edit, not only when a session is read.
# ---------------------------------------------------------------------------

_ROWS = {
    "intake_sessions": {"s-alice": {"id": "s-alice", "created_by_email": "alice@iv.test",
                                    "answers": {}},
                        "s-old": {"id": "s-old", "created_by_email": None, "answers": {}}},
    "generated_proposals": {"p-alice": {"id": "p-alice", "created_by_email": "alice@iv.test",
                                        "intake_session_id": "s-alice"},
                            "p-bob": {"id": "p-bob", "created_by_email": "bob@iv.test",
                                      "intake_session_id": "s-bob"}},
    "architecture_diagrams": {"d-alice": {"id": "d-alice", "intake_session_id": "s-alice",
                                          "generated_proposal_id": "p-alice",
                                          "status": "needs_review", "title": "T",
                                          "spec_json": {}, "rendered_svg_path": "d-alice.png"}},
}


def _fake_supabase(writes):
    """A PostgREST stand-in over _ROWS that records every write."""
    import httpx

    def handler(req):
        table = req.url.path.rsplit("/", 1)[-1]
        if req.method != "GET":
            writes.append((req.method, req.url.path))
            return httpx.Response(200, json=[{"id": "x", "answers": {}}])
        wanted = dict(req.url.params)
        rows = [r for r in _ROWS.get(table, {}).values()
                if all(str(r.get(k)) == v.removeprefix("eq.")
                       for k, v in wanted.items() if v.startswith("eq."))]
        return httpx.Response(200, json=rows)
    return httpx.MockTransport(handler)


def test_answers_cannot_be_saved_into_another_users_session():
    import asyncio
    import httpx
    import supabase_client as sc

    async def save(user, session):
        writes = []
        sc.REQUEST_USER.set(user)
        async with httpx.AsyncClient(transport=_fake_supabase(writes)) as c:
            return await sc.patch_intake_answers(c, session, {"client_name": "X"}), writes

    result, writes = asyncio.run(save("bob@iv.test", "s-alice"))
    assert result is None and writes == []                    # refused before any write
    result, writes = asyncio.run(save("alice@iv.test", "s-alice"))
    assert result is not None and len(writes) == 1
    result, writes = asyncio.run(save("bob@iv.test", "s-old"))   # no owner recorded: as before
    assert result is not None
    result, writes = asyncio.run(save(None, "s-alice"))          # no identity forwarded: as before
    assert result is not None


def test_another_users_diagram_cannot_be_read_or_approved(monkeypatch):
    import httpx
    writes = []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(app_module.httpx, "AsyncClient",
                        lambda *a, **k: real_client(transport=_fake_supabase(writes)))
    bob = {"X-OpenWebUI-User-Email": "bob@iv.test"}
    alice = {"X-OpenWebUI-User-Email": "alice@iv.test"}
    with TestClient(app_module.app) as tc:
        assert tc.get("/v1/diagrams/d-alice", headers=bob).status_code == 404
        assert tc.get("/v1/proposals/p-alice/diagrams", headers=bob).status_code == 404
        assert tc.patch("/v1/diagrams/d-alice", headers=bob,
                        json={"status": "approved"}).status_code == 404
        assert writes == []
        assert tc.get("/v1/diagrams/d-alice", headers=alice).status_code == 200
        assert tc.get("/v1/proposals/p-alice/diagrams", headers=alice).status_code == 200
        assert tc.get("/v1/diagrams/d-alice").status_code == 200     # no identity: as before


def test_a_chat_marker_naming_someone_elses_proposal_is_refused(monkeypatch):
    """The proposal id reaches the brain inside a chat marker the user can
    edit. "approve" must not act on a proposal that is not theirs, nor on one
    that does not belong to the session in the same marker."""
    import httpx
    import chat_state as cs
    writes, approved = [], []
    real_client = httpx.AsyncClient
    monkeypatch.setattr(app_module.httpx, "AsyncClient",
                        lambda *a, **k: real_client(transport=_fake_supabase(writes)))

    async def fake_intent(q):
        return cs.INTENT_APPROVE, "approve"

    async def spy_approve(proposal_id):
        approved.append(proposal_id)
        return True, "ok"

    monkeypatch.setattr(app_module, "resolve_intent", fake_intent)
    monkeypatch.setattr(app_module, "approve_architecture", spy_approve)

    def turn(user, session, proposal):
        with TestClient(app_module.app) as tc:
            return tc.post("/v1/chat/completions", headers={"X-OpenWebUI-User-Email": user}, json={
                "messages": [
                    {"role": "assistant", "content": "x " + cs.encode_marker(cs.ChatState(
                        mode=cs.MODE_ARCHITECTURE, session=session, proposal=proposal))},
                    {"role": "user", "content": "approve"}],
                "stream": False}).json()["choices"][0]["message"]["content"]

    reply = turn("bob@iv.test", "s-alice", "p-alice")              # someone else's deal
    assert approved == [] and "start over" in reply.lower()
    reply = turn("alice@iv.test", "s-alice", "p-bob")              # proposal of another session
    assert approved == [] and "start over" in reply.lower()


# ---------------------------------------------------------------------------
# Deep audit F17: drafting needs the architecture step FINISHED: every planned
# diagram approved or explicitly skipped. "Nothing pending" was not enough.
# ---------------------------------------------------------------------------

def _reviewed_session(monkeypatch, rows, plan, skipped=()):
    async def fake_session(client, sid):
        return {"id": sid, "client_name": "Test Client", "proposal_type": "implementation",
                "answers": {app_module.PLAN_KEY: [list(p) for p in plan],
                            app_module.SKIP_KEY: list(skipped)}}

    async def fake_diagrams(client, sid):
        return rows

    async def fake_link(*a, **k):
        return None
    monkeypatch.setattr(app_module.supabase_client, "get_intake_session", fake_session)
    monkeypatch.setattr(app_module.supabase_client, "list_diagrams_for_session", fake_diagrams)
    monkeypatch.setattr(app_module.supabase_client, "link_intake_to_proposal", fake_link)


_TWO = [("Solution Architecture", "architecture"), ("Network", "network")]


def _draft(monkeypatch, rows, plan=_TWO, skipped=()):
    _patch_common(monkeypatch, direct=False)
    _reviewed_session(monkeypatch, rows, plan, skipped)
    with TestClient(app_module.app) as tc:
        return tc.post("/v1/generate-proposal", json={**_base_body(), "intake_session_id": "s1"})


def test_a_session_that_never_reviewed_its_diagrams_cannot_draft(monkeypatch):
    resp = _draft(monkeypatch, [])
    assert resp.status_code == 409
    assert "0 of 2" in resp.json()["error"]


def test_one_approved_diagram_is_not_a_finished_review(monkeypatch):
    resp = _draft(monkeypatch, [{"id": "d1", "title": "X — Solution Architecture",
                                "status": "approved", "spec_json": {}}])
    assert resp.status_code == 409 and "1 of 2" in resp.json()["error"]


def test_every_diagram_approved_or_skipped_can_draft(monkeypatch):
    approved = {"id": "d1", "title": "X — Solution Architecture", "status": "approved",
                "spec_json": {}}
    assert _draft(monkeypatch, [approved], skipped=["Network"]).status_code == 200
    # Skipping everything is a finished review too: a failed renderer must not
    # lock a consultant out of drafting.
    assert _draft(monkeypatch, [], skipped=["Solution Architecture", "Network"]).status_code == 200
    # A skip recorded for a diagram that is not in the plan counts for nothing.
    assert _draft(monkeypatch, [approved], skipped=["Something Else"]).status_code == 409


# ---------------------------------------------------------------------------
# Deep audit F39 and F40: what the consultant is told after a draft, and
# getting the files again without drafting again.
# ---------------------------------------------------------------------------

def _chat_reply_for(monkeypatch, data):
    """The chat reply generate_proposal_from_chat builds from an endpoint JSON."""
    import asyncio

    async def fake_session(client, sid):
        return {"answers": {"client_name": "Test Client", "proposal_type": "implementation",
                            "rfp_text": "x"}}

    class _Resp:
        status_code = 200
        def raise_for_status(self): return None
        def json(self): return data

    async def fake_post(self, url, **kwargs):
        return _Resp()

    monkeypatch.setattr(app_module.supabase_client, "get_intake_session", fake_session)
    monkeypatch.setattr(app_module.httpx.AsyncClient, "post", fake_post)
    return asyncio.run(app_module.generate_proposal_from_chat("sess-1", "prop-1"))


def test_a_failed_pdf_and_a_failed_upload_are_said_in_the_chat_reply(monkeypatch):
    """F39: both were in the endpoint's JSON and the chat reply read neither,
    so a failed PDF showed only as a missing link."""
    reply = _chat_reply_for(monkeypatch, {
        "filename": "Shilpi_Proposal_X.docx",
        "signed_urls": {"docx": "https://example.supabase.co/d"},
        "pdf": {"error": "LibreOffice (soffice) binary not found on PATH"},
        "storage_notes": ["could not upload PDF to storage bucket"]})
    assert "PDF" in reply and "soffice" in reply
    assert "could not upload PDF to storage bucket" in reply
    assert "[Download DOCX]" in reply                     # what did work is still offered
    clean = _chat_reply_for(monkeypatch, {
        "filename": "Shilpi_Proposal_X.docx", "pdf": {"size": 10},
        "signed_urls": {"docx": "https://example.supabase.co/d",
                        "pdf": "https://example.supabase.co/p"}})
    assert "not produced" not in clean.lower() and "problem" not in clean.lower()


def test_the_stored_file_paths_are_recorded_on_the_proposal(monkeypatch):
    """F40, caller side: the files were uploaded and their paths thrown away,
    so nothing could re-issue a link once the first one expired."""
    recorded = {}

    async def fake_upload(client, path, data, content_type):
        return path

    async def fake_sign(client, path, expires_in=3600, bucket=None):
        return f"https://example.supabase.co/sign/{path}"

    async def capture_files(client, proposal_id, *, docx_path=None, pdf_path=None):
        recorded.update(proposal=proposal_id, docx=docx_path, pdf=pdf_path)
        return True

    _patch_common(monkeypatch)
    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    monkeypatch.setattr(app_module.supabase_client, "set_proposal_files", capture_files)
    monkeypatch.setattr(app_module.export_engine, "export_pdf", lambda b: (b"%PDF-1.4", None))
    with TestClient(app_module.app) as tc:
        tc.post("/v1/generate-proposal",
                json={**_base_body(), "include_pdf": True, "return_signed_urls": True})
    assert recorded["proposal"] == "prop-123"
    assert recorded["docx"].startswith("prop-123/") and recorded["docx"].endswith(".docx")
    assert recorded["pdf"].startswith("prop-123/") and recorded["pdf"].endswith(".pdf")


def test_asking_for_the_links_again_signs_the_stored_files_without_drafting(monkeypatch):
    """F40: after the one-hour links expired, the only way to the files was
    another 40-minute paid draft."""
    import chat_state as cs
    drafted = []

    async def fake_session(client, sid):
        return {"id": sid, "generated_proposal_id": "prop-9", "answers": {}}

    async def fake_files(client, proposal_id):
        return {"final_docx_path": "prop-9/a.docx", "final_pdf_path": "prop-9/a.pdf"} \
            if stored["has_files"] else {}

    async def fake_sign(client, path, expires_in=3600, bucket=None):
        return f"https://example.supabase.co/sign/{path}?token=fresh"

    async def spy_draft(session_id, proposal_id):
        drafted.append(session_id)
        return "drafted"

    stored = {"has_files": True}
    monkeypatch.setattr(app_module.supabase_client, "get_intake_session", fake_session)
    monkeypatch.setattr(app_module.supabase_client, "get_proposal_files", fake_files)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    monkeypatch.setattr(app_module, "generate_proposal_from_chat", spy_draft)

    def ask(text):
        with TestClient(app_module.app) as tc:
            return tc.post("/v1/chat/completions", json={
                "messages": [
                    {"role": "assistant", "content": "x " + cs.encode_marker(cs.ChatState(
                        mode=cs.MODE_DRAFTING, session="s1", proposal="p1"))},
                    {"role": "user", "content": text}],
                "stream": False}).json()["choices"][0]["message"]["content"]

    reply = ask("the links expired, new links please")
    assert "prop-9/a.docx?token=fresh" in reply and "prop-9/a.pdf?token=fresh" in reply
    assert drafted == []
    assert cs.decode_marker(reply).mode == cs.MODE_DRAFTING
    stored["has_files"] = False
    reply = ask("links")
    assert drafted == [] and "generate the proposal" in reply.lower()


# ---------------------------------------------------------------------------
# Deep audit F57 and F53: an approved diagram that does not reach the document
# is named in the chat reply, whatever the reason.
# ---------------------------------------------------------------------------

def test_an_approved_diagram_left_out_as_a_duplicate_is_named(monkeypatch):
    """F57: de-duplication dropped an approved diagram with only a log line."""
    import document_engine
    img = b"\x89PNG\r\n\x1a\n" + b"0" * 64
    same = ["Firewall", "Application", "Database"]
    diagrams = [
        {"status": "approved", "image_bytes": img, "diagram_type": "architecture",
         "title": "Solution Architecture", "nodes": same},
        {"status": "approved", "image_bytes": img, "diagram_type": "component",
         "title": "Integration View", "nodes": same}]
    dropped: list[str] = []
    kept = document_engine._embeddable_diagrams(diagrams, None, dropped)
    assert [d["title"] for d in kept] == ["Solution Architecture"]
    assert dropped == ["Integration View"]

    # ...and the list reaches the consultant.
    reply = _chat_reply_for(monkeypatch, {
        "filename": "Shilpi_Proposal_X.docx", "signed_urls": {"docx": "https://e/d"},
        "diagrams_left_out": [{"title": "Integration View", "why": "duplicate"},
                              {"title": "Network", "why": "not_drawn"}]})
    assert "Integration View" in reply and "duplicat" in reply.lower()
    assert "Network" in reply and "could not be drawn" in reply


def test_left_out_diagrams_travel_from_drafting_to_the_endpoint(monkeypatch):
    async def generate_reporting(client, **kwargs):
        return {**await _fake_generate_proposal(client, **kwargs),
                "diagrams_left_out": ["Integration View"]}

    async def fake_upload(client, path, data, content_type):
        return path

    async def fake_sign(client, path, expires_in=3600, bucket=None):
        return "https://example.supabase.co/x"

    _patch_common(monkeypatch)
    monkeypatch.setattr(app_module, "generate_proposal", generate_reporting)
    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    with TestClient(app_module.app) as tc:
        data = tc.post("/v1/generate-proposal",
                       json={**_base_body(), "return_signed_urls": True}).json()
    assert data["diagrams_left_out"] == [{"title": "Integration View", "why": "duplicate"}]


def test_an_approved_diagram_is_not_redrawn_by_another_engine_at_draft_time(monkeypatch):
    """F53: when D2 failed at draft time the approved diagram was silently
    redrawn by Graphviz, which draws every node as a box. The picture in the
    document must be the kind of picture that was reviewed, or be left out
    and named."""
    seen = {}

    async def spying_generate(client, **kwargs):
        seen["diagrams"] = kwargs.get("diagrams")
        return await _fake_generate_proposal(client, **kwargs)

    async def fake_upload(client, path, data, content_type):
        return path

    async def fake_sign(client, path, expires_in=3600, bucket=None):
        return "https://example.supabase.co/x"

    renderers = []

    def d2_is_down(spec, fmt="png", timeout=25.0, renderer=None):
        renderers.append(renderer)
        return None if renderer == "d2" else b"\x89PNG graphviz boxes"

    spec = {"diagram_type": "flow", "title": "X — Joiner Flow",
            "nodes": [{"id": "a", "label": "Start", "shape": "start"},
                      {"id": "b", "label": "Approved?", "shape": "decision"}],
            "edges": [{"source": "a", "target": "b"}]}
    _patch_common(monkeypatch, direct=False)
    _reviewed_session(monkeypatch, [{"id": "d1", "title": "X — Joiner Flow",
                                    "status": "approved", "spec_json": spec}],
                      [("Joiner Flow", "flow")])
    monkeypatch.setattr(app_module, "generate_proposal", spying_generate)
    monkeypatch.setattr(app_module.diagram_engine, "render_spec", d2_is_down)
    monkeypatch.setattr(app_module.diagram_engine, "d2_available", lambda: True)
    monkeypatch.setattr(app_module.supabase_client, "upload_generated_draft", fake_upload)
    monkeypatch.setattr(app_module.supabase_client, "create_signed_url", fake_sign)
    with TestClient(app_module.app) as tc:
        data = tc.post("/v1/generate-proposal", json={
            **_base_body(), "intake_session_id": "s1", "return_signed_urls": True}).json()
    assert renderers and set(renderers) == {"d2"}            # never asked for a fallback
    assert not seen["diagrams"]                              # nothing redrawn went in
    assert {"title": "X — Joiner Flow", "why": "not_drawn"} in data["diagrams_left_out"]


def test_a_requirement_read_cut_short_reaches_the_chat_reply(monkeypatch):
    """Deep audit F11: pasted RFP text is read for requirements up to 12,000
    characters. The matrix knew it had stopped early (`truncated`) and nothing
    on the drafting path read that, so the consultant was never told."""
    import asyncio
    import document_engine

    class _Matrix:
        entries, unassessed, truncated, text_cut = [], 0, True, True
        overall_notes = ""

    async def cut_matrix(client, rfp_text, requirements, top_k):
        return _Matrix()

    async def stub_embed(client, text):
        return [0.0] * 8

    async def no_chunks(*a, **k):
        return []

    async def stub_draft(client, system_prompt, user_prompt, max_tokens=None):
        return "Drafted."

    async def run():
        async with app_module.httpx.AsyncClient() as client:
            return await document_engine.generate_proposal(
                client, rfp_text="x" * 20000, client_name="Acme",
                proposal_type="implementation", iam_vendor="SailPoint",
                embed_fn=stub_embed, retrieve_fn=no_chunks,
                build_grounded_system_fn=lambda chunks: "EVIDENCE",
                run_compliance_matrix_fn=cut_matrix,
                render_matrix_markdown_fn=lambda m: "Compliance Matrix",
                sections=["executive_summary"], include_compliance_matrix=True)

    saved = document_engine.draft_with_openrouter
    document_engine.draft_with_openrouter = stub_draft
    try:
        result = asyncio.run(run())
    finally:
        document_engine.draft_with_openrouter = saved
    assert result["compliance_text_cut"] is True

    # The real matrix sets the flag only when the text really was longer.
    async def no_requirements(text):
        return []
    monkeypatch.setattr(app_module, "extract_requirements", no_requirements)
    long = asyncio.run(app_module.run_compliance_matrix(None, "y" * 20000))
    short = asyncio.run(app_module.run_compliance_matrix(None, "y" * 200))
    assert long.text_cut is True and short.text_cut is False

    reply = _chat_reply_for(monkeypatch, {
        "filename": "Shilpi_Proposal_X.docx", "signed_urls": {"docx": "https://e/d"},
        "compliance_text_cut": True})
    assert "requirement" in reply.lower() and "12,000" in reply
