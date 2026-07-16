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
