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

import logging
import os

import httpx

from intake_template import missing_required

log = logging.getLogger("sarvam-brain.supabase")

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


def _table_url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


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
        log.error("create_intake_session failed: %s", e)
        raise SupabaseError(f"could not create intake session: {e}") from e
    if not rows:
        raise SupabaseError("create_intake_session returned no row")
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
        log.error("get_intake_session failed: %s", e)
        return None
    return rows[0] if rows else None


async def patch_intake_answers(
    client: httpx.AsyncClient, session_id: str, answers_partial: dict
) -> dict | None:
    """Merge answers_partial into the stored answers jsonb. Fail-soft -> None.

    Read-merge-write: PostgREST cannot deep-merge jsonb in a single PATCH, so we
    load the current answers, shallow-merge the partial, and write back. Also
    keeps the denormalised proposal_type/client_name/iam_vendor columns in sync
    when those keys are present in the partial."""
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
        log.error("patch_intake_answers failed: %s", e)
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
        log.error("complete_intake_session failed: %s", e)
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
        log.error("link_intake_to_proposal failed: %s", e)
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
) -> str | None:
    """Persist a generated proposal draft. Fail-soft -> None.

    created_by is intentionally omitted (NULL). The DB column was made nullable
    in sarvam_005 and the service-role key bypasses the RLS INSERT policy, so a
    NULL created_by insert succeeds. Restore once real auth is wired."""
    payload = {
        "org_id": org_id,
        "client_name": client_name,
        "proposal_type": proposal_type,
        "iam_vendor": iam_vendor,
        "discovery_answers": discovery_answers or {},
        "draft_markdown": draft_markdown or "",
        "retrieval_trace": retrieval_trace or [],
        "status": "draft",
        "intake_session_id": intake_session_id,
    }
    try:
        resp = await client.post(
            _table_url("generated_proposals"), headers=_headers(), json=payload, timeout=30.0
        )
        resp.raise_for_status()
        rows = resp.json()
    except Exception as e:  # noqa: BLE001
        log.error("insert_generated_proposal failed: %s", e)
        return None
    return rows[0]["id"] if rows else None


# --- architecture_diagrams (Pass 4) -----------------------------------------
# All fail-soft (log + None/empty). The core proposal flow must never break on a
# diagram persistence hiccup. RLS is enforced at the DB layer; the service-role
# key bypasses it here exactly as for the other tables above.

DIAGRAM_BUCKET = os.environ.get("DIAGRAM_BUCKET", "diagram-renders")


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
        log.error("insert_diagram failed: %s", e)
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
        log.error("get_diagram failed: %s", e)
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
        log.error("list_diagrams_for_proposal failed: %s", e)
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
        log.error("update_diagram failed: %s", e)
        return None
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
    client: httpx.AsyncClient, path: str, expires_in: int = 3600
) -> str | None:
    """Create a time-limited signed URL for a GENERATED_DRAFTS_BUCKET object. Fail-soft.

    Returns a fully-qualified URL, or None on failure. Supabase returns a relative
    ``signedURL`` (``/object/sign/<bucket>/<path>?token=...``) which we join onto
    the storage base so the caller gets a directly usable link."""
    url = f"{SUPABASE_URL}/storage/v1/object/sign/{GENERATED_DRAFTS_BUCKET}/{path}"
    try:
        resp = await client.post(
            url, headers=_headers(prefer_representation=False),
            json={"expiresIn": expires_in}, timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json() or {}
    except Exception as e:  # noqa: BLE001
        log.error("create_signed_url failed (bucket '%s' missing? fail-soft): %s",
                  GENERATED_DRAFTS_BUCKET, e)
        return None
    signed = data.get("signedURL") or data.get("signedUrl")
    if not signed:
        return None
    if signed.startswith("/"):
        return f"{SUPABASE_URL}/storage/v1{signed}"
    return signed
