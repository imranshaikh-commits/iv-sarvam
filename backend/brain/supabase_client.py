"""
Sprint 5 Pass 1 — thin Supabase REST helpers for intake + persistence.

Uses the PostgREST endpoint (``{SUPABASE_URL}/rest/v1/<table>``) with the
service-role key, which BYPASSES row-level security. That is why a NULL
``created_by`` insert into ``generated_proposals`` succeeds here even though the
RLS INSERT policy expects ``created_by = auth.uid()`` — the service key is not
subject to those policies. Real per-user auth is a later pass.

Design rules:
  * NO import of app.py (avoids circular import). Config is read straight from
    os.environ with safe defaults so this module imports cleanly WITHOUT secrets
    (keyless smoke tests import intake_template, not this — but keep it safe).
  * Fail-soft: on any Supabase/network error, log and return None, EXCEPT the
    two operations the intake flow depends on — ``create_intake_session`` and
    ``complete_intake_session`` — which raise so the caller can surface a 4xx/5xx
    instead of silently losing the session.
  * All functions take an ``httpx.AsyncClient`` so the caller controls the
    connection lifecycle (mirrors the rest of the brain).
"""

from __future__ import annotations

import contextvars
import logging
import os

import httpx

from intake_template import missing_required

log = logging.getLogger("shilpi-brain.supabase")

# Read at import but tolerate absence so the module stays importable keyless.
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")


class SupabaseError(RuntimeError):
    """Raised by the required (non-fail-soft) operations."""


def _headers(*, prefer_representation: bool = True) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
    }
    if prefer_representation:
        h["Prefer"] = "return=representation"
    return h


def _why(e: Exception) -> str:
    """The reason a Supabase call failed, INCLUDING the response body.

    httpx's str(HTTPStatusError) is only the status line: "Client error '400 Bad
    Request' for url ...". PostgREST puts the actual cause in the body -- the
    column, the constraint, the offending value -- and every handler here was
    throwing that away.

    A real 400 read: "invalid input syntax for type uuid" / "violates check
    constraint". Without the body, a migration proposal failing to insert
    surfaced to the user as "the database didn't respond" and took two rounds of
    guessing to diagnose. The database answered immediately, and precisely.
    """
    detail = ""
    resp = getattr(e, "response", None)
    if resp is not None:
        try:
            body = resp.json()
            detail = " | ".join(
                str(body[k]) for k in ("message", "details", "hint", "code")
                if body.get(k))
        except Exception:  # noqa: BLE001
            try:
                detail = (resp.text or "")[:400]
            except Exception:  # noqa: BLE001
                detail = ""
    return f"{e}{' — ' + detail if detail else ''}"


def _table_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"



# Who is working (Sprint 3.5). Open WebUI forwards the signed-in user as
# X-OpenWebUI-User-Email (ENABLE_FORWARD_USER_INFO_HEADERS). The created_by /
# approved_by uuid columns point at Supabase auth users, which nobody signs in
# as, so the email goes in *_by_email text columns (sarvam_019). Each stamp is
# its own fail-soft write: a missing column or a hiccup loses the name, never
# the proposal, session or approval.
REQUEST_USER: contextvars.ContextVar = contextvars.ContextVar("request_user", default=None)


def _may_access(row: dict) -> bool:
    """A session opened by one signed-in user is not readable by another
    (audit 2026-10-12, finding 6). Session ids reach the brain inside chat
    markers, which a user can edit, so the id alone proves nothing. Rows with
    no recorded owner (created before sarvam_019, or with identity forwarding
    off) stay readable: IV is one organisation and that is the prior behaviour.
    ponytail: sharing a session between two colleagues needs an explicit grant."""
    user, owner = REQUEST_USER.get(), (row or {}).get("created_by_email")
    return not (user and owner and user.strip().lower() != owner.strip().lower())


async def may_access_row(client: httpx.AsyncClient, table: str, row_id: str | None) -> bool:
    """May the signed-in user act on this row? False when it belongs to another
    user, or when that could not be checked (a failed read is not a yes).

    For rows reached by an id the user can edit: a session or proposal id in a
    chat marker, a diagram's parent in a REST path (audit F03: ownership was
    checked when a session was READ, and nowhere else). True when no identity
    is forwarded, the row has no recorded owner, or the row does not exist --
    there is then no other user's data to protect, and the caller's own
    not-found handling applies."""
    if not REQUEST_USER.get() or not row_id:
        return True
    try:
        resp = await client.get(
            _table_url(table), headers=_headers(prefer_representation=False),
            params={"id": f"eq.{row_id}", "select": "id,created_by_email", "limit": "1"},
            timeout=30.0)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("ownership check on %s failed: %s", table, _why(e))
        return False
    if rows and not _may_access(rows[0]):
        log.warning("%s %s refused: it belongs to another user", table, row_id)
        return False
    return True


async def may_access_diagram(client: httpx.AsyncClient, row: dict) -> bool:
    """A diagram has no owner of its own: it is its session's, else its proposal's."""
    if row.get("intake_session_id"):
        return await may_access_row(client, "intake_sessions", row["intake_session_id"])
    return await may_access_row(client, "generated_proposals", row.get("generated_proposal_id"))


async def marker_is_mine(client: httpx.AsyncClient, session_id: str | None,
                         proposal_id: str | None) -> bool:
    """Do the ids a chat marker carries belong to the signed-in user, and to
    each other? The marker travels through the client and can be edited, so a
    proposal id proves nothing by being there: it must be the user's own and,
    when it names a session, the session in the same marker."""
    if not REQUEST_USER.get():
        return True
    if not await may_access_row(client, "intake_sessions", session_id):
        return False
    if not proposal_id:
        return True
    try:
        resp = await client.get(
            _table_url("generated_proposals"), headers=_headers(prefer_representation=False),
            params={"id": f"eq.{proposal_id}",
                    "select": "id,created_by_email,intake_session_id", "limit": "1"},
            timeout=30.0)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("ownership check on generated_proposals failed: %s", _why(e))
        return False
    if not rows:
        return True
    linked = rows[0].get("intake_session_id")
    if not _may_access(rows[0]) or (linked and session_id and linked != session_id):
        log.warning("proposal %s refused: not this user's, or not this session's", proposal_id)
        return False
    return True


async def _stamp_user(client: httpx.AsyncClient, table: str, row_id: str, column: str) -> None:
    user = REQUEST_USER.get()
    if not (user and row_id):
        return
    try:
        resp = await client.patch(_table_url(table), headers=_headers(prefer_representation=False),
                                  params={"id": f"eq.{row_id}"}, json={column: user}, timeout=30.0)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.warning("could not record %s on %s: %s", column, table, _why(e))

# --- intake_sessions --------------------------------------------------------

async def create_intake_session(
    client: httpx.AsyncClient,
    *,
    org_id: str,
    proposal_type: str | None = None,
    client_name: str | None = None,
    iam_vendor: str | None = None,
    answers: dict | None = None,
) -> str:
    """Insert a new intake session, return its id. REQUIRED (raises on failure)."""
    payload = {
        "org_id": org_id,
        "proposal_type": proposal_type,
        "client_name": client_name,
        "iam_vendor": iam_vendor,
        "answers": answers or {},
    }
    try:
        resp = await client.post(
            _table_url("intake_sessions"), headers=_headers(), json=payload, timeout=30.0
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001 — surface as a typed error for the endpoint
        log.error("create_intake_session failed: %s", _why(e))
        raise SupabaseError(f"could not create intake session: {e}") from e
    if not rows:
        raise SupabaseError("create_intake_session returned no row")
    await _stamp_user(client, "intake_sessions", rows[0]["id"], "created_by_email")
    return rows[0]["id"]


async def get_intake_session(client: httpx.AsyncClient, session_id: str) -> dict | None:
    """Fetch a single intake session by id. Fail-soft -> None."""
    try:
        resp = await client.get(
            _table_url("intake_sessions"),
            headers=_headers(prefer_representation=False),
            params={"id": f"eq.{session_id}", "select": "*", "limit": "1"},
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("get_intake_session failed: %s", _why(e))
        return None
    row = rows[0] if rows else None
    if row and not _may_access(row):
        log.warning("intake session %s refused: it belongs to another user", session_id)
        return None
    return row


async def patch_intake_answers(
    client: httpx.AsyncClient, session_id: str, answers_partial: dict
) -> dict | None:
    """Merge answers_partial into the stored answers jsonb. Fail-soft -> None.

    Atomic: one UPDATE in the database (merge_intake_answers, sarvam_020), which
    also keeps the denormalised proposal_type/client_name/iam_vendor columns in
    sync. It used to be read-merge-write here, and two concurrent saves each
    read the old object, so the later write dropped the other's answer."""
    if not await may_access_row(client, "intake_sessions", session_id):
        return None
    try:
        resp = await client.post(
            f"{SUPABASE_URL}/rest/v1/rpc/merge_intake_answers", headers=_headers(),
            json={"p_session": session_id, "p_partial": answers_partial or {}}, timeout=30.0)
        if resp.status_code == 404:
            # The function is not in this database yet (sarvam_020 not applied).
            log.warning("merge_intake_answers missing; using the non-atomic merge. "
                        "Apply supabase/migrations/sarvam_020_atomic_answer_merge.sql")
            return await _patch_intake_answers_legacy(client, session_id, answers_partial)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("patch_intake_answers failed: %s", _why(e))
        return None
    return rows[0] if rows else None


async def _patch_intake_answers_legacy(
    client: httpx.AsyncClient, session_id: str, answers_partial: dict
) -> dict | None:
    """Read-merge-write. NOT safe under concurrent saves; only for a database
    that predates sarvam_020."""
    current = await get_intake_session(client, session_id)
    if current is None:
        return None
    merged = dict(current.get("answers") or {})
    merged.update(answers_partial or {})

    body: dict = {"answers": merged, "updated_at": "now()"}
    for col in ("proposal_type", "client_name", "iam_vendor"):
        if col in (answers_partial or {}):
            body[col] = answers_partial[col]

    try:
        resp = await client.patch(
            _table_url("intake_sessions"),
            headers=_headers(),
            params={"id": f"eq.{session_id}"},
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("patch_intake_answers failed: %s", _why(e))
        return None
    return rows[0] if rows else None


async def complete_intake_session(client: httpx.AsyncClient, session_id: str) -> dict:
    """Validate required answers and, if complete, mark the session 'complete'.

    REQUIRED operation (raises on transport failure). Returns:
      {"session_id", "status", "complete": bool, "missing": [ids...]}
    If required answers are missing, status stays 'in_progress' and complete=False
    (this is a normal validation result, NOT an error)."""
    session = await get_intake_session(client, session_id)
    if session is None:
        raise SupabaseError(f"intake session {session_id} not found")

    answers = session.get("answers") or {}
    proposal_type = session.get("proposal_type") or answers.get("proposal_type")
    missing = missing_required(answers, proposal_type)
    if missing:
        return {
            "session_id": session_id,
            "status": session.get("status", "in_progress"),
            "complete": False,
            "missing": missing,
        }

    try:
        resp = await client.patch(
            _table_url("intake_sessions"),
            headers=_headers(),
            params={"id": f"eq.{session_id}"},
            json={"status": "complete", "updated_at": "now()"},
            timeout=30.0,
        )
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.error("complete_intake_session failed: %s", _why(e))
        raise SupabaseError(f"could not complete intake session: {e}") from e

    return {"session_id": session_id, "status": "complete", "complete": True, "missing": []}


async def link_intake_to_proposal(
    client: httpx.AsyncClient, session_id: str, proposal_id: str
) -> dict | None:
    """Set intake_sessions.generated_proposal_id. Fail-soft -> None."""
    try:
        resp = await client.patch(
            _table_url("intake_sessions"),
            headers=_headers(),
            params={"id": f"eq.{session_id}"},
            json={"generated_proposal_id": proposal_id, "updated_at": "now()"},
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("link_intake_to_proposal failed: %s", _why(e))
        return None
    return rows[0] if rows else None


# --- generated_proposals ----------------------------------------------------

async def insert_generated_proposal(
    client: httpx.AsyncClient,
    *,
    org_id: str,
    client_name: str,
    proposal_type: str,
    iam_vendor: str | None = None,
    discovery_answers: dict | None = None,
    draft_markdown: str | None = None,
    retrieval_trace: list | dict | None = None,
    intake_session_id: str | None = None,
    status: str = "drafting",
) -> str | None:
    """Persist a generated proposal draft. Fail-soft -> None.

    created_by is intentionally omitted (NULL). The DB column was made nullable
    in shilpi_005 and the service-role key bypasses the RLS INSERT policy, so a
    NULL created_by insert succeeds. Restore once real auth is wired.

    status MUST be one of the values allowed by the generated_proposals_status_check
    CHECK constraint (discovery, architecture_review, architecture_approved,
    drafting, review, final, abandoned). We use 'drafting' for a freshly generated
    draft — 'draft' is NOT a valid value and makes the insert 400."""
    payload = {
        "org_id": org_id,
        "client_name": client_name,
        "proposal_type": proposal_type,
        "iam_vendor": iam_vendor,
        "discovery_answers": discovery_answers or {},
        "draft_markdown": draft_markdown or "",
        "retrieval_trace": retrieval_trace or [],
        "status": status,
        "intake_session_id": intake_session_id,
    }
    try:
        resp = await client.post(
            _table_url("generated_proposals"), headers=_headers(), json=payload, timeout=30.0
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("insert_generated_proposal failed: %s", _why(e))
        return None
    if rows:
        await _stamp_user(client, "generated_proposals", rows[0]["id"], "created_by_email")
    return rows[0]["id"] if rows else None


async def set_proposal_files(client: httpx.AsyncClient, proposal_id: str, *,
                             docx_path: str | None = None,
                             pdf_path: str | None = None) -> bool:
    """Record where a proposal's exported files are stored (final_docx_path,
    final_pdf_path). The columns have existed since 001_init and were never
    written, so nothing could re-issue a download link once the first expired."""
    body = {k: v for k, v in (("final_docx_path", docx_path), ("final_pdf_path", pdf_path)) if v}
    if not (proposal_id and body):
        return False
    try:
        resp = await client.patch(
            _table_url("generated_proposals"), headers=_headers(prefer_representation=False),
            params={"id": f"eq.{proposal_id}"}, json=body, timeout=30.0)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("set_proposal_files failed: %s", _why(e))
        return False


async def get_proposal_files(client: httpx.AsyncClient, proposal_id: str) -> dict:
    """{"final_docx_path", "final_pdf_path"} for a proposal the signed-in user
    may access; {} when there is none, it is another user's, or the read failed."""
    try:
        resp = await client.get(
            _table_url("generated_proposals"), headers=_headers(prefer_representation=False),
            params={"id": f"eq.{proposal_id}", "limit": "1",
                    "select": "id,created_by_email,final_docx_path,final_pdf_path"},
            timeout=30.0)
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("get_proposal_files failed: %s", _why(e))
        return {}
    return rows[0] if rows and _may_access(rows[0]) else {}


# --- architecture_diagrams (Pass 4) -----------------------------------------
# All fail-soft (log + None/empty). The core proposal flow must never break on a
# diagram persistence hiccup. RLS is enforced at the DB layer; the service-role
# key bypasses it here exactly as for the other tables above.

DIAGRAM_BUCKET = os.environ.get("DIAGRAM_BUCKET", "diagram-renders")


async def set_proposal_usage(client: httpx.AsyncClient, proposal_id: str, usage: dict) -> bool:
    """Record what generating this proposal cost (llm_usage, sarvam_019).
    A separate fail-soft write: if the column is missing, only this is lost,
    never the proposal row itself."""
    try:
        resp = await client.patch(
            _table_url("generated_proposals"), headers=_headers(prefer_representation=False),
            params={"id": f"eq.{proposal_id}"}, json={"llm_usage": usage}, timeout=30.0)
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("set_proposal_usage failed: %s", _why(e))
        return False


async def insert_diagram(
    client: httpx.AsyncClient,
    *,
    org_id: str,
    generated_proposal_id: str | None,
    diagram_type: str,
    title: str,
    spec_json: dict,
    status: str = "draft",
    intake_session_id: str | None = None,
) -> dict | None:
    """Insert a new architecture diagram row. Fail-soft -> None.

    mermaid_source is legacy but NOT NULL in the base schema, so we write an
    empty string (the structured spec lives in spec_json; renderer='graphviz')."""
    payload = {
        "org_id": org_id,
        "generated_proposal_id": generated_proposal_id,
        "mermaid_source": "",  # legacy NOT NULL column; superseded by spec_json
        "spec_json": spec_json,
        "diagram_type": diagram_type,
        "title": title,
        "renderer": "graphviz",
        "status": status,
        "intake_session_id": intake_session_id,
    }
    try:
        resp = await client.post(
            _table_url("architecture_diagrams"), headers=_headers(), json=payload, timeout=30.0
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("insert_diagram failed: %s", _why(e))
        return None
    return rows[0] if rows else None


async def get_diagram(client: httpx.AsyncClient, diagram_id: str) -> dict | None:
    """Fetch a single diagram by id. Fail-soft -> None."""
    try:
        resp = await client.get(
            _table_url("architecture_diagrams"),
            headers=_headers(prefer_representation=False),
            params={"id": f"eq.{diagram_id}", "select": "*", "limit": "1"},
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("get_diagram failed: %s", _why(e))
        return None
    return rows[0] if rows else None


async def list_diagrams_for_proposal(
    client: httpx.AsyncClient, generated_proposal_id: str
) -> list[dict]:
    """List diagrams attached to a proposal (newest first). Fail-soft -> []."""
    try:
        resp = await client.get(
            _table_url("architecture_diagrams"),
            headers=_headers(prefer_representation=False),
            params={
                "generated_proposal_id": f"eq.{generated_proposal_id}",
                "select": "*",
                "order": "created_at.desc",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:  # noqa: BLE001
        log.error("list_diagrams_for_proposal failed: %s", _why(e))
        return []


async def list_diagrams_for_session(client: httpx.AsyncClient, intake_session_id: str) -> list[dict]:
    """Every diagram of an intake session, newest first. Fail-soft -> [].
    The session is the authority for what was reviewed, not a proposal id
    supplied by the caller."""
    try:
        resp = await client.get(
            _table_url("architecture_diagrams"), headers=_headers(prefer_representation=False),
            params={"intake_session_id": f"eq.{intake_session_id}", "select": "*",
                    "order": "created_at.desc"}, timeout=30.0)
        resp.raise_for_status()
        return resp.json() or []
    except Exception as e:  # noqa: BLE001
        log.error("list_diagrams_for_session failed: %s", _why(e))
        return []


async def update_diagram(
    client: httpx.AsyncClient, diagram_id: str, patch: dict
) -> dict | None:
    """Apply a column patch to a diagram row. Fail-soft -> None."""
    body = {**patch, "updated_at": "now()"} if "updated_at" not in patch else dict(patch)
    # architecture_diagrams has no updated_at column in the base schema; only
    # send known columns to avoid PostgREST rejecting the write.
    body.pop("updated_at", None)
    try:
        resp = await client.patch(
            _table_url("architecture_diagrams"),
            headers=_headers(),
            params={"id": f"eq.{diagram_id}"},
            json=body,
            timeout=30.0,
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("update_diagram failed: %s", _why(e))
        return None
    if rows and body.get("status") == "approved":
        await _stamp_user(client, "architecture_diagrams", diagram_id, "approved_by_email")
    return rows[0] if rows else None


async def upload_diagram_render(
    client: httpx.AsyncClient, path: str, image_bytes: bytes, content_type: str = "image/png"
) -> str | None:
    """Upload a rendered diagram to the DIAGRAM_BUCKET storage bucket. Fail-soft.

    Returns the storage object path on success, or None if the bucket is absent /
    upload fails (caller must fall back to skipping the embed or documenting
    manual setup — never crash)."""
    url = f"{SUPABASE_URL}/storage/v1/object/{DIAGRAM_BUCKET}/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    try:
        resp = await client.post(url, headers=headers, content=image_bytes, timeout=30.0)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.error("upload_diagram_render failed (bucket '%s' missing? fail-soft): %s",
                  DIAGRAM_BUCKET, e)
        return None
    return path


# --- generated-drafts storage (Pass 6 — export pipeline) --------------------
# Signed-URL delivery of the exported DOCX/PDF. Both helpers are fail-soft: if the
# bucket is absent or storage errors, they log and return None so the export
# endpoint can surface a clear manual-setup note instead of crashing. RLS is
# enforced at the DB layer; the service-role key bypasses it here as elsewhere.

GENERATED_DRAFTS_BUCKET = os.environ.get("GENERATED_DRAFTS_BUCKET", "generated-drafts")


async def upload_generated_draft(
    client: httpx.AsyncClient,
    path: str,
    data: bytes,
    content_type: str,
) -> str | None:
    """Upload an exported draft (DOCX/PDF) to the GENERATED_DRAFTS_BUCKET. Fail-soft.

    Returns the storage object path on success, or None if the bucket is missing /
    the upload fails (caller documents manual setup — never crashes the request)."""
    url = f"{SUPABASE_URL}/storage/v1/object/{GENERATED_DRAFTS_BUCKET}/{path}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": content_type,
        "x-upsert": "true",
    }
    try:
        resp = await client.post(url, headers=headers, content=data, timeout=60.0)
        resp.raise_for_status()
    except Exception as e:  # noqa: BLE001
        log.error("upload_generated_draft failed (bucket '%s' missing? fail-soft): %s",
                  GENERATED_DRAFTS_BUCKET, e)
        return None
    return path


async def create_signed_url(
    client: httpx.AsyncClient, path: str, expires_in: int = 3600,
    bucket: str | None = None,
) -> str | None:
    """Create a time-limited signed URL for a GENERATED_DRAFTS_BUCKET object. Fail-soft.

    Returns a fully-qualified URL, or None on failure. Supabase returns a relative
    ``signedURL`` (``/object/sign/<bucket>/<path>?token=...``) which we join onto
    the storage base so the caller gets a directly usable link."""
    bucket = bucket or GENERATED_DRAFTS_BUCKET
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{bucket}/{path}"
    try:
        resp = await client.post(
            url, headers=_headers(prefer_representation=False),
            json={"expiresIn": expires_in}, timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:  # noqa: BLE001
        log.error("create_signed_url failed (bucket '%s' missing? fail-soft): %s",
                  bucket, e)
        return None
    signed = data.get("signedURL") or data.get("signedUrl")
    if not signed:
        return None
    if signed.startswith("/"):
        return f"{SUPABASE_URL}/storage/v1{signed}"
    return signed


# --- keep-alive -------------------------------------------------------------

async def ping(client: httpx.AsyncClient) -> bool:
    """Cheapest possible authenticated read, used by /v1/keepalive.

    Supabase free-tier projects pause after 7 days with no activity, which is
    exactly what happened once already. A daily hit on this keeps Postgres warm.
    Returns True on success; never raises.
    """
    try:
        resp = await client.get(
            _table_url("organizations"),
            headers={**_headers(prefer_representation=False), "Range": "0-0"},
            params={"select": "id"},
            timeout=20.0,
        )
        resp.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log.error("supabase ping failed: %s", _why(e))
        return False
